"""
Orquestrador principal do motor de conciliação.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_CONCILIADO_MANUAL,
    STATUS_REVISAR, STATUS_REVISAR_COLISAO, STATUS_IGNORADO_USUARIO,
)
from .match_one_to_one import match_one_to_one
from .match_n_to_one import match_n_to_one
from .match_one_to_n import match_one_to_n
from .collision import resolve_collisions
from .params import ConciliacaoParams
from .combo_search import clear_cache

# Threshold base de paralelismo. O valor efetivo é reduzido proporcionalmente ao
# max_group_size: grupos maiores tornam cada find_combos mais custoso, fazendo o
# paralelismo valer a pena com menos linhas livres.
_PARALLEL_THRESHOLD_BASE = 800

# Todos os status possíveis — usados para converter _status em Categorical (dica 7)
_ALL_STATUSES = [
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO, STATUS_CONCILIADO_MANUAL,
    STATUS_REVISAR, STATUS_REVISAR_COLISAO, STATUS_IGNORADO_USUARIO,
]


def run_engine(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Executa o pipeline completo de conciliação:
      1. 1:1  (cascata de datas, para no primeiro offset com candidatos)
      2. N:1  (banco soma = financeiro, mesmo dia)
      3. 1:N  (financeiro soma = banco, mesmo dia)
      4. Resolução de colisões

    Otimizações aplicadas neste módulo:
      Dica 7 — _status convertido para pd.Categorical antes das passagens de
               matching, acelerando filtros isin/== em colunas com poucos valores.
      Dica 8 — cache de combinatória limpo aqui antes de cada rodada.
      Dica 10 — N:1 e 1:N executados em threads paralelas quando há muitas
                linhas livres; resultados fundidos sem conflito pelo caller.
    """
    # Limpa cache de combinatória da rodada anterior (dica 8)
    clear_cache()

    # Garante colunas de controle
    for col, default in [("_status", STATUS_SEM_PAREAMENTO), ("_metodo", ""), ("_ids_fin", "")]:
        if col not in df_bnk.columns:
            df_bnk[col] = default

    for col, default in [("_status", STATUS_IGNORADO_SEM_PAR), ("_metodo", ""), ("_id_bnk", "")]:
        if col not in df_fin.columns:
            df_fin[col] = default

    # Dica 7: converte _status para Categorical — filtragens com == e isin()
    # ficam 2-4x mais rápidas em DataFrames grandes (pandas usa lookup interno).
    df_bnk["_status"] = pd.Categorical(df_bnk["_status"], categories=_ALL_STATUSES)
    df_fin["_status"] = pd.Categorical(df_fin["_status"], categories=_ALL_STATUSES)

    # Passagem 1:1 + resolução de colisões
    df_bnk, df_fin, pending_pairs = match_one_to_one(df_bnk, df_fin, params)
    df_bnk, df_fin = resolve_collisions(df_bnk, df_fin, pending_pairs, params)

    # Passagens N:1 e 1:N — paralelas se volume justifica (dica 10).
    # Threshold adaptativo: grupos maiores demoram mais por chamada, portanto
    # o paralelismo compensa com menos linhas livres.
    parallel_threshold = max(100, _PARALLEL_THRESHOLD_BASE // max(1, params.max_group_size - 3))
    n_free = int((df_bnk["_status"] == STATUS_SEM_PAREAMENTO).sum())
    if n_free >= parallel_threshold:
        df_bnk, df_fin = _run_n1_1n_parallel(df_bnk, df_fin, params)
    else:
        df_bnk, df_fin = match_n_to_one(df_bnk, df_fin, params)
        df_bnk, df_fin = match_one_to_n(df_bnk, df_fin, params)

    return df_bnk, df_fin


# ---------------------------------------------------------------------------
# Execução paralela N:1 + 1:N (dica 10)
# ---------------------------------------------------------------------------

def _run_n1_1n_parallel(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Executa match_n_to_one e match_one_to_n em threads simultâneas sobre
    cópias independentes dos DataFrames, depois funde os resultados.

    Prioridade em conflito: N:1 prevalece sobre 1:N (linha bancária ou
    financeira já conciliada por N:1 não pode ser reutilizada por 1:N).

    Nota: ThreadPoolExecutor libera o GIL durante operações pandas/C e
    permite sobreposição real entre as duas passagens. A combinatória pura
    (Python) ainda é sujeita ao GIL, mas o pré-agrupamento e as escritas
    se beneficiam do paralelismo de I/O de memória.
    """
    bnk_a, fin_a = df_bnk.copy(), df_fin.copy()
    bnk_b, fin_b = df_bnk.copy(), df_fin.copy()

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_n1 = ex.submit(match_n_to_one, bnk_a, fin_a, params)
        fut_1n = ex.submit(match_one_to_n, bnk_b, fin_b, params)
        bnk_n1, fin_n1 = fut_n1.result()
        bnk_1n, fin_1n = fut_1n.result()

    return _merge_results(df_bnk, df_fin, bnk_n1, fin_n1, bnk_1n, fin_1n)


def _merge_results(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    bnk_n1: pd.DataFrame,
    fin_n1: pd.DataFrame,
    bnk_1n: pd.DataFrame,
    fin_1n: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Funde resultados de N:1 e 1:N aplicando-os em df_bnk/df_fin originais.
    N:1 tem prioridade: uma linha já conciliada por N:1 não pode ser
    reutilizada por 1:N.
    """
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    # IDs conciliados pelo passe N:1
    n1_bnk_matched: set = set()
    n1_fin_matched: set = set()

    # Aplica N:1 primeiro
    _status_col = "_status"
    for rec in bnk_n1.loc[
        bnk_n1[_status_col].isin([STATUS_CONCILIADO, STATUS_REVISAR]),
        ["_id", "_status", "_metodo", "_ids_fin"],
    ].to_dict("records"):
        bi = bnk_pos.get(rec["_id"])
        if bi is None:
            continue
        df_bnk.at[bi, "_status"] = rec["_status"]
        df_bnk.at[bi, "_metodo"] = rec["_metodo"]
        df_bnk.at[bi, "_ids_fin"] = rec["_ids_fin"]
        if rec["_status"] == STATUS_CONCILIADO:
            n1_bnk_matched.add(rec["_id"])

    for rec in fin_n1.loc[
        fin_n1[_status_col] == STATUS_CONCILIADO,
        ["_id", "_status", "_metodo", "_id_bnk"],
    ].to_dict("records"):
        fi = fin_pos.get(rec["_id"])
        if fi is None:
            continue
        df_fin.at[fi, "_status"] = rec["_status"]
        df_fin.at[fi, "_metodo"] = rec["_metodo"]
        df_fin.at[fi, "_id_bnk"] = rec["_id_bnk"]
        n1_fin_matched.add(rec["_id"])

    # Aplica 1:N apenas para linhas não conciliadas por N:1
    for rec in bnk_1n.loc[
        bnk_1n[_status_col].isin([STATUS_CONCILIADO, STATUS_REVISAR]),
        ["_id", "_status", "_metodo", "_ids_fin"],
    ].to_dict("records"):
        if rec["_id"] in n1_bnk_matched:
            continue
        bi = bnk_pos.get(rec["_id"])
        if bi is None:
            continue
        if df_bnk.at[bi, "_status"] == STATUS_CONCILIADO:
            continue  # N:1 já tratou esta linha
        ids_fin = [x for x in (rec["_ids_fin"] or "").split(";") if x]
        if any(fid in n1_fin_matched for fid in ids_fin):
            continue  # alguma linha financeira já foi consumida por N:1
        df_bnk.at[bi, "_status"] = rec["_status"]
        df_bnk.at[bi, "_metodo"] = rec["_metodo"]
        df_bnk.at[bi, "_ids_fin"] = rec["_ids_fin"]

    for rec in fin_1n.loc[
        fin_1n[_status_col] == STATUS_CONCILIADO,
        ["_id", "_status", "_metodo", "_id_bnk"],
    ].to_dict("records"):
        if rec["_id"] in n1_fin_matched:
            continue
        # Verifica se o banco correspondente foi de fato conciliado por 1:N
        bi = bnk_pos.get(rec["_id_bnk"]) if rec["_id_bnk"] else None
        if bi is not None and df_bnk.at[bi, "_status"] != STATUS_CONCILIADO:
            continue
        fi = fin_pos.get(rec["_id"])
        if fi is None:
            continue
        if df_fin.at[fi, "_status"] == STATUS_CONCILIADO:
            continue  # já tratada por N:1
        df_fin.at[fi, "_status"] = rec["_status"]
        df_fin.at[fi, "_metodo"] = rec["_metodo"]
        df_fin.at[fi, "_id_bnk"] = rec["_id_bnk"]

    return df_bnk, df_fin
