"""
Conciliação 1:1 com suporte a janela de datas configurável.

O chamador decide quais offsets tentar:
  - offsets=[0]          → somente D0 (Passo 1 do motor)
  - offsets=[-1,1,-2,2]  → variação de datas em cascata (Passo 3 do motor)

Comportamento de cascata: para cada linha bancária, itera os offsets em ordem
e para no primeiro que produzir algum candidato financeiro. Assim, D-1 é
preferido a D+1, que é preferido a D-2, etc.
"""
from __future__ import annotations
import datetime
from collections import defaultdict
from typing import List, Optional, Tuple

import pandas as pd

from .normalize import STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR
from .params import ConciliacaoParams


def match_one_to_one(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    offsets: Optional[List[int]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[Tuple]]:
    """
    Retorna (df_bnk, df_fin, pending_pairs).
    pending_pairs: lista de (id_bnk, id_fin, offset_k) para resolução de colisões.

    offsets: lista de deslocamentos de data a tentar, em ordem de prioridade.
             Padrão None usa params.date_offsets (comportamento legado).
    """
    if offsets is None:
        offsets = params.date_offsets

    # Índice: (data, valor) -> lista de id_fin livres
    fin_index: dict = defaultdict(list)
    for rec in df_fin[["_status", "_data", "_valor", "_id"]].to_dict("records"):
        if rec["_status"] == STATUS_IGNORADO_SEM_PAR:
            fin_index[(rec["_data"], rec["_valor"])].append(rec["_id"])

    pending_pairs: List[Tuple] = []

    for rec_b in df_bnk[["_status", "_id", "_data", "_valor"]].to_dict("records"):
        if rec_b["_status"] != STATUS_SEM_PAREAMENTO:
            continue
        for offset in offsets:
            search_date = rec_b["_data"] + datetime.timedelta(days=offset)
            candidates = fin_index.get((search_date, rec_b["_valor"]), [])
            if candidates:
                for id_fin in candidates:
                    pending_pairs.append((rec_b["_id"], id_fin, offset))
                break  # cascata: encontrou candidatos neste offset, não avança

    return df_bnk, df_fin, pending_pairs
