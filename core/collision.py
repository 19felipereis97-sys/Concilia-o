"""
Resolução de colisões após match 1:1.
Para cada linha bancária com múltiplos candidatos financeiros (ou financeiro
disputado por múltiplos bancos), elege um vencedor e marca os demais como REVISAR_COLISAO.
"""
from __future__ import annotations
from collections import defaultdict
from typing import List, Tuple

import pandas as pd

from .normalize import (
    STATUS_CONCILIADO, STATUS_REVISAR_COLISAO,
)
from .params import ConciliacaoParams


def resolve_collisions(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    pending_pairs: List[Tuple],
    params: ConciliacaoParams,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    pending_pairs: [(id_bnk, id_fin, offset_k), ...]
    Elege o par com menor |offset| (e menor offset negativo em empate).
    Vencedor -> CONCILIADO; perdedores -> REVISAR_COLISAO.
    """
    if not pending_pairs:
        return df_bnk, df_fin

    # Agrupa por id_bnk
    by_bnk: dict = defaultdict(list)
    for id_b, id_f, k in pending_pairs:
        by_bnk[id_b].append((id_f, k))

    # Agrupa por id_fin para detectar financeiros disputados
    by_fin: dict = defaultdict(list)
    for id_b, id_f, k in pending_pairs:
        by_fin[id_f].append((id_b, k))

    elected: dict = {}  # id_bnk -> id_fin vencedor
    elected_fin: dict = {}  # id_fin -> id_bnk vencedor

    # Ordena candidatos por (|k|, -k) para cada banco
    def sort_key(item):
        id_f, k = item
        return (abs(k), -k, id_f)

    for id_b, candidates in by_bnk.items():
        candidates_sorted = sorted(candidates, key=sort_key)
        # Tenta eleger o primeiro candidato ainda livre
        for id_f, k in candidates_sorted:
            if id_f not in elected_fin:
                elected[id_b] = (id_f, k)
                elected_fin[id_f] = id_b
                break

    # Aplica resultados
    # Mapa posicional — dict(zip) mais rápido que iterrows
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    # Marca vencedores diretamente nos DataFrames originais (sem cópia)
    for id_b, (id_f, k) in elected.items():
        metodo = f"1:1 {params.offset_label(k)}"
        bi = bnk_pos[id_b]
        fi = fin_pos[id_f]
        df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
        df_bnk.at[bi, "_metodo"] = metodo
        df_bnk.at[bi, "_ids_fin"] = id_f
        df_fin.at[fi, "_status"] = STATUS_CONCILIADO
        df_fin.at[fi, "_metodo"] = metodo
        df_fin.at[fi, "_id_bnk"] = id_b

    # Marca perdedores como REVISAR_COLISAO
    for id_b, candidates in by_bnk.items():
        if id_b not in elected:
            bi = bnk_pos[id_b]
            df_bnk.at[bi, "_status"] = STATUS_REVISAR_COLISAO
        else:
            id_f_won, _ = elected[id_b]
            for id_f, k in candidates:
                if id_f != id_f_won:
                    fi = fin_pos[id_f]
                    if df_fin.at[fi, "_status"] != STATUS_CONCILIADO:
                        df_fin.at[fi, "_status"] = STATUS_REVISAR_COLISAO

    return df_bnk, df_fin
