"""
Conciliação 1:1 com cascata de datas.
Regra: para cada linha do extrato bancário, busca uma linha financeira com
       mesmo valor (sinal incluso) priorizando o mesmo dia (D).
       Somente se nenhum candidato for encontrado no dia exato, expande para
       D-1, D+1, D-2 e D+2 (para na primeira janela que tiver candidatos).
"""
from __future__ import annotations
import datetime
from collections import defaultdict
from typing import List, Tuple

import pandas as pd

from .normalize import STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR
from .params import ConciliacaoParams


def match_one_to_one(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[Tuple]]:
    """
    Retorna (df_bnk, df_fin, pending_pairs).
    pending_pairs: lista de (id_bnk, id_fin, offset_k) para resolucao de colisoes.

    Cascata de offsets: para cada linha bancária, itera params.date_offsets
    em ordem (inicia em 0 = mesmo dia) e para no primeiro offset que produzir
    algum candidato. Assim, D±1 e D±2 só são tentados se o dia exato não tiver
    nenhum lançamento financeiro com o mesmo valor.
    """
    # Índice: (data, valor) -> lista de id_fin livres
    # to_dict("records") preserva nomes de colunas com underscore (itertuples não preserva)
    fin_index: dict = defaultdict(list)
    for rec in df_fin[["_status", "_data", "_valor", "_id"]].to_dict("records"):
        if rec["_status"] == STATUS_IGNORADO_SEM_PAR:
            fin_index[(rec["_data"], rec["_valor"])].append(rec["_id"])

    pending_pairs: List[Tuple] = []

    for rec_b in df_bnk[["_status", "_id", "_data", "_valor"]].to_dict("records"):
        if rec_b["_status"] != STATUS_SEM_PAREAMENTO:
            continue
        for offset in params.date_offsets:
            search_date = rec_b["_data"] + datetime.timedelta(days=offset)
            candidates = fin_index.get((search_date, rec_b["_valor"]), [])
            if candidates:
                for id_fin in candidates:
                    pending_pairs.append((rec_b["_id"], id_fin, offset))
                break  # Cascata: encontrou candidatos neste offset, não avança para os demais

    return df_bnk, df_fin, pending_pairs
