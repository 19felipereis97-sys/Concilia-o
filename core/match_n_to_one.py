"""
Conciliação N:1 — soma de N linhas bancárias = 1 linha financeira.
Caso típico: SISPAG agrupa vários pagamentos em um único lançamento no extrato.

Executado somente após a passagem 1:1; portanto, todas as linhas bancárias e
financeiras aqui presentes já são confirmadamente sem pareamento naquele passo.
A análise combinatória considera exclusivamente o mesmo dia do extrato (sem D±2).

Estratégia em duas fases:
  Fase 1 — k=2 com TODOS os candidatos (O(n²) instantâneo). Garante captura de
  pares assimétricos como A+B=D que seriam excluídos pelo limite de candidatos.
  Fase 2 — k≥3 com candidatos limitados (n_to_one_max_candidates), protegida
  pelo combo_timeout_sec para grupos patológicos.
"""
from __future__ import annotations
import time
from collections import defaultdict
from typing import Tuple

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_REVISAR,
)
from .params import ConciliacaoParams
from .combo_search import find_combos
from .candidate_selection import limit_subset_candidates


def match_n_to_one(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    extra_bnk_statuses: list | None = None,
    bnk_pos: dict | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Para cada linha financeira livre, busca combinações de linhas bancárias
    livres (mesma data, mesmo sinal) cuja soma bate com o valor financeiro.
    """
    tol = float(params.value_tolerance_cents) / 100
    use_deadline = params.combo_timeout_sec > 0
    max_candidates = int(getattr(params, "n_to_one_max_candidates", 30) or 0)

    _free_statuses = {STATUS_SEM_PAREAMENTO, *(extra_bnk_statuses or [])}

    bnk_pos = bnk_pos if bnk_pos is not None else dict(zip(df_bnk["_id"], df_bnk.index))

    # Pré-agrupa linhas bancárias LIVRES por (data, sinal) — dica 6: usa _valor_f
    bnk_groups: dict = defaultdict(list)
    cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_bnk.columns else ["_id", "_data", "_valor"]
    for rec in df_bnk.loc[df_bnk["_status"].isin(_free_statuses), cols].to_dict("records"):
        vf = rec.get("_valor_f", float(rec["_valor"]))
        sign = vf > 0
        bnk_groups[(rec["_data"], sign)].append({"_id": rec["_id"], "_valor_f": vf})

    free_bnk: set = set(df_bnk.loc[df_bnk["_status"].isin(_free_statuses), "_id"])

    fin_free_df = df_fin[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR]
    fin_cols = ["_id", "_data", "_valor", "_valor_f"] if "_valor_f" in df_fin.columns else ["_id", "_data", "_valor"]

    # Pré-calcula número de candidatos por linha financeira e ordena ascendente.
    # Linhas com menos candidatos (mais restritas) são processadas primeiro, evitando
    # que linhas com muitas combinações "roubem" lançamentos bancários de correspondências únicas.
    fin_records: list = []
    for fi, row_f in zip(fin_free_df.index, fin_free_df[fin_cols].to_dict("records")):
        _tf = row_f.get("_valor_f", float(row_f["_valor"]))
        _sign = _tf > 0
        _abs = abs(_tf)
        n_cands = sum(
            1 for c in bnk_groups.get((row_f["_data"], _sign), [])
            if c["_id"] in free_bnk and abs(c["_valor_f"]) <= _abs + tol
        )
        fin_records.append((fi, row_f, n_cands))
    fin_records.sort(key=lambda x: x[2])

    for fi, row_f, _ in fin_records:
        target_f = row_f.get("_valor_f", float(row_f["_valor"]))
        sign = target_f > 0
        abs_target = abs(target_f)

        # Candidatos bancários na mesma data e mesmo sinal, ainda livres
        candidatos = [
            c for c in bnk_groups.get((row_f["_data"], sign), [])
            if c["_id"] in free_bnk and abs(c["_valor_f"]) <= abs_target + tol
        ]
        if len(candidatos) < 2:
            continue

        # Pre-check: impossível atingir o alvo mesmo somando todos os candidatos
        if sum(abs(c["_valor_f"]) for c in candidatos) < abs_target - tol:
            continue

        # ── Fase 1: k=2 com TODOS os candidatos ──────────────────────────────
        # C(n,2) ≤ C(98,2)=4.753 < MITM_THRESHOLD → brute-force instantâneo.
        # Captura pares assimétricos (ex: 19.328 + 3.900 = 23.228) que seriam
        # excluídos pelo limite de candidatos da fase 2.
        all_vals = [c["_valor_f"] for c in candidatos]
        phase1_matches = find_combos(all_vals, target_f, tol, max_k=2)

        if phase1_matches:
            if len(phase1_matches) == 1:
                combo_rows = [candidatos[i] for i in phase1_matches[0]]
                ids_bnk = [r["_id"] for r in combo_rows]
                metodo = f"N:1 soma={len(ids_bnk)}"
                for r in combo_rows:
                    bi = bnk_pos[r["_id"]]
                    df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
                    df_bnk.at[bi, "_metodo"] = metodo
                    df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                    free_bnk.discard(r["_id"])
                df_fin.at[fi, "_status"] = STATUS_CONCILIADO
                df_fin.at[fi, "_metodo"] = metodo
                df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
            else:
                # Múltiplos pares k=2 → ambiguidade real
                ids_bloqueados = {
                    candidatos[i]["_id"]
                    for match in phase1_matches
                    for i in match
                }
                for c in candidatos:
                    bi = bnk_pos[c["_id"]]
                    if c["_id"] in ids_bloqueados and df_bnk.at[bi, "_status"] in _free_statuses:
                        df_bnk.at[bi, "_status"] = STATUS_REVISAR
                        df_bnk.at[bi, "_metodo"] = "N:1 ambiguo"
                        df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                        free_bnk.discard(c["_id"])
                df_fin.at[fi, "_status"] = STATUS_REVISAR
                df_fin.at[fi, "_metodo"] = "bloqueado:N:1 ambiguo"
                df_fin.at[fi, "_id_bnk"] = ";".join(sorted(ids_bloqueados))
            continue

        # ── Fase 2: k≥3 com candidatos limitados ─────────────────────────────
        # Fase 1 já descartou k=2; aqui buscamos grupos maiores com o guardião
        # de performance (max_candidates + deadline).
        candidatos_busca, limited = limit_subset_candidates(
            candidatos,
            target_f,
            max_candidates,
            max_group_size=params.max_group_size,
        )
        if len(candidatos_busca) < 2:
            continue

        vals = [c["_valor_f"] for c in candidatos_busca]
        deadline = time.monotonic() + params.combo_timeout_sec if use_deadline else None
        search_start = time.monotonic()
        matches = find_combos(
            vals,
            target_f,
            tol,
            params.max_group_size,
            deadline=deadline,
            stop_after_first_k=True,
        )
        timed_out = use_deadline and (time.monotonic() - search_start) >= (params.combo_timeout_sec * 0.95)

        if not matches:
            if limited or timed_out:
                motivo = "tempo esgotado" if timed_out else "grupo limitado"
                for c in candidatos_busca:
                    bi = bnk_pos[c["_id"]]
                    if df_bnk.at[bi, "_status"] in _free_statuses:
                        df_bnk.at[bi, "_metodo"] = f"N:1 {motivo}"
            continue

        if len(matches) == 1:
            combo_rows = [candidatos_busca[i] for i in matches[0]]
            ids_bnk = [r["_id"] for r in combo_rows]
            metodo = f"N:1 soma={len(ids_bnk)}"
            for r in combo_rows:
                bi = bnk_pos[r["_id"]]
                df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
                df_bnk.at[bi, "_metodo"] = metodo
                df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                free_bnk.discard(r["_id"])
            df_fin.at[fi, "_status"] = STATUS_CONCILIADO
            df_fin.at[fi, "_metodo"] = metodo
            df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
        else:
            # Múltiplos combos encontrados. Preferir o único combo de menor k.
            min_k = min(len(m) for m in matches)
            min_k_matches = [m for m in matches if len(m) == min_k]

            if len(min_k_matches) == 1:
                combo_rows = [candidatos_busca[i] for i in min_k_matches[0]]
                ids_bnk = [r["_id"] for r in combo_rows]
                metodo = f"N:1 soma={len(ids_bnk)}"
                for r in combo_rows:
                    bi = bnk_pos[r["_id"]]
                    df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
                    df_bnk.at[bi, "_metodo"] = metodo
                    df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                    free_bnk.discard(r["_id"])
                df_fin.at[fi, "_status"] = STATUS_CONCILIADO
                df_fin.at[fi, "_metodo"] = metodo
                df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
            else:
                # Múltiplos combos no mesmo k → ambiguidade real
                ids_bloqueados = {
                    candidatos_busca[i]["_id"]
                    for match in min_k_matches
                    for i in match
                }
                for c in candidatos:
                    bi = bnk_pos[c["_id"]]
                    if c["_id"] in ids_bloqueados and df_bnk.at[bi, "_status"] in _free_statuses:
                        df_bnk.at[bi, "_status"] = STATUS_REVISAR
                        df_bnk.at[bi, "_metodo"] = "N:1 ambiguo"
                        df_bnk.at[bi, "_ids_fin"] = row_f["_id"]
                        free_bnk.discard(c["_id"])
                df_fin.at[fi, "_status"] = STATUS_REVISAR
                df_fin.at[fi, "_metodo"] = "bloqueado:N:1 ambiguo"
                df_fin.at[fi, "_id_bnk"] = ";".join(sorted(ids_bloqueados))

    return df_bnk, df_fin
