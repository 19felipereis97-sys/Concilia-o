"""
Orquestrador principal do motor de conciliação.

Ordem de execução (extrato → financeiro):
  1. 1:1  D0   — mesmo dia, fechamento exato.
  2. 1:N  D0   — mesmo dia, soma de N financeiros = 1 extrato, fechamento total.
  3. 1:1  D±   — variação de datas em cascata (D-1 > D+1 > D-2 > D+2).
  4. 1:N  D±   — variação de datas, soma de N financeiros, fechamento total.
  5. N:1  D0   — N extratos = 1 financeiro, mesmo dia (ativado por padrão).
  6. 1:N  D0 parcial — maior somatório possível ≤ valor do extrato, mesmo dia.
  6.5. 1:1 D0  — segunda passagem, captura lançamentos remanescentes que batem 1:1.
  7. N:1  D0   — segunda passagem, aproveita financeiros liberados pelo parcial.

  Após todos os passos, linhas de pendência parcial são anexadas ao df_bnk.
"""
from __future__ import annotations

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_CONCILIADO_MANUAL,
    STATUS_REVISAR, STATUS_REVISAR_COLISAO, STATUS_IGNORADO_USUARIO,
    STATUS_PARCIAL, STATUS_PENDENTE_PARCIAL,
)
from .match_one_to_one import match_one_to_one
from .match_n_to_one import match_n_to_one
from .match_one_to_n import match_one_to_n
from .match_partial_one_to_n import match_partial_one_to_n
from .collision import resolve_collisions
from .params import ConciliacaoParams
from .combo_search import clear_cache

_ALL_STATUSES = [
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_CONCILIADO_MANUAL,
    STATUS_REVISAR, STATUS_REVISAR_COLISAO, STATUS_IGNORADO_USUARIO,
    STATUS_PARCIAL, STATUS_PENDENTE_PARCIAL,
]


def run_engine(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Executa o pipeline completo de conciliação e retorna (df_bnk, df_fin).
    df_bnk pode conter linhas extras (PND_*) representando pendências parciais.
    """
    clear_cache()

    # Garante colunas de controle
    for col, default in [("_status", STATUS_SEM_PAREAMENTO), ("_metodo", ""), ("_ids_fin", "")]:
        if col not in df_bnk.columns:
            df_bnk[col] = default
    for col, default in [("_status", STATUS_IGNORADO_SEM_PAR), ("_metodo", ""), ("_id_bnk", "")]:
        if col not in df_fin.columns:
            df_fin[col] = default

    # Categorical acelera filtros isin/== em DataFrames grandes
    df_bnk["_status"] = pd.Categorical(df_bnk["_status"], categories=_ALL_STATUSES)
    df_fin["_status"] = pd.Categorical(df_fin["_status"], categories=_ALL_STATUSES)

    # ── Passo 1: 1:1 D0 ──────────────────────────────────────────────────────
    df_bnk, df_fin, pairs_d0 = match_one_to_one(df_bnk, df_fin, params, offsets=[0])
    df_bnk, df_fin = resolve_collisions(df_bnk, df_fin, pairs_d0, params)

    # ── Passo 2: 1:N D0 (fechamento total) ───────────────────────────────────
    df_bnk, df_fin = match_one_to_n(df_bnk, df_fin, params, offsets=[0])

    # ── Passo 3: 1:1 D± (cascata D-1 > D+1 > D-2 > D+2) ────────────────────
    var_offsets = [k for k in params.date_offsets if k != 0]
    if var_offsets:
        df_bnk, df_fin, pairs_var = match_one_to_one(df_bnk, df_fin, params, offsets=var_offsets)
        df_bnk, df_fin = resolve_collisions(df_bnk, df_fin, pairs_var, params)

    # ── Passo 4: 1:N D± (fechamento total, candidatos de datas distintas) ────
    if var_offsets:
        df_bnk, df_fin = match_one_to_n(df_bnk, df_fin, params, offsets=var_offsets)

    # ── Passo 5: N:1 D0 (N extratos = 1 financeiro, mesmo dia) ─────────────────
    # Executa em loop até convergência: entre iterações recoloca entradas REVISAR
    # de volta a STATUS_SEM_PAREAMENTO para que possam ser reconsideradas quando
    # outras combinações ambíguas já foram resolvidas.
    if params.enable_n_to_one:
        for _ in range(10):
            _n1_rev = (df_bnk["_status"] == STATUS_REVISAR) & df_bnk["_metodo"].str.startswith("N:1", na=False)
            df_bnk.loc[_n1_rev, "_status"] = STATUS_SEM_PAREAMENTO
            df_bnk.loc[_n1_rev, "_metodo"] = ""
            _prev = int((df_fin["_status"] == STATUS_CONCILIADO).sum())
            df_bnk, df_fin = match_n_to_one(df_bnk, df_fin, params)
            if int((df_fin["_status"] == STATUS_CONCILIADO).sum()) == _prev:
                break

    # ── Passo 6: 1:N D0 parcial (maior somatório ≤ extrato, mesmo dia) ───────
    df_bnk, df_fin, pending_rows = match_partial_one_to_n(df_bnk, df_fin, params)

    if pending_rows:
        pending_df = pd.DataFrame(pending_rows)
        for col in df_bnk.columns:
            if col not in pending_df.columns:
                pending_df[col] = ""
        # Categorical não é aplicado às linhas de pendência — status já é string válida
        df_bnk = pd.concat([df_bnk, pending_df[df_bnk.columns]], ignore_index=True)

    # ── Passo 6.5: 1:1 D0 — segunda passagem (lançamentos remanescentes) ────────
    df_bnk, df_fin, pairs_post = match_one_to_one(
        df_bnk, df_fin, params, offsets=[0],
        extra_bnk_statuses=[STATUS_PENDENTE_PARCIAL],
    )
    df_bnk, df_fin = resolve_collisions(df_bnk, df_fin, pairs_post, params)

    # ── Passo 7: N:1 D0 — segunda passagem (após parcial) ────────────────────
    if params.enable_n_to_one:
        for _ in range(10):
            _n1_rev = (df_bnk["_status"] == STATUS_REVISAR) & df_bnk["_metodo"].str.startswith("N:1", na=False)
            df_bnk.loc[_n1_rev, "_status"] = STATUS_SEM_PAREAMENTO
            df_bnk.loc[_n1_rev, "_metodo"] = ""
            _prev = int((df_fin["_status"] == STATUS_CONCILIADO).sum())
            df_bnk, df_fin = match_n_to_one(df_bnk, df_fin, params,
                                             extra_bnk_statuses=[STATUS_PENDENTE_PARCIAL])
            if int((df_fin["_status"] == STATUS_CONCILIADO).sum()) == _prev:
                break

    return df_bnk, df_fin
