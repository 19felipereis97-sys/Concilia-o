"""
Resolução de colisões após match 1:1.

Regras:
  - Banco com candidato único e financeiro livre  → CONCILIADO.
  - Banco com vários fins no mesmo offset (mesmo dia/valor) → REVISAR  (ambiguidade real).
  - Banco cujo único fin foi eleito por outro banco              → REVISAR_COLISAO.
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
    bnk_pos: dict | None = None,
    fin_pos: dict | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    pending_pairs: [(id_bnk, id_fin, offset_k), ...]

    Como o match_one_to_one usa cascata (break no primeiro offset com candidatos),
    todos os candidatos de um mesmo banco estão necessariamente no mesmo offset.
    Portanto:
      len(by_bnk[id_b]) > 1  <=>  múltiplos fins com mesmo valor na mesma data
                               ==>  ambiguidade 1:1 → REVISAR.
    """
    if not pending_pairs:
        return df_bnk, df_fin

    by_bnk: dict = defaultdict(list)
    for id_b, id_f, k in pending_pairs:
        by_bnk[id_b].append((id_f, k))

    elected: dict = {}      # id_bnk -> (id_fin, offset)
    elected_fin: dict = {}  # id_fin -> id_bnk

    for id_b, candidates in by_bnk.items():
        # Tenta candidatos em ordem de chegada; elege o primeiro financeiro ainda livre.
        # Duplicatas financeiras (mesmo valor/data) ficam intocadas como SEM_PAREAMENTO.
        for id_f, k in candidates:
            if id_f not in elected_fin:
                elected[id_b] = (id_f, k)
                elected_fin[id_f] = id_b
                break

    bnk_pos = bnk_pos if bnk_pos is not None else dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = fin_pos if fin_pos is not None else dict(zip(df_fin["_id"], df_fin.index))

    # Vencedores → CONCILIADO
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

    # Perdedores: todos os candidatos já foram eleitos por outro banco
    for id_b in by_bnk:
        if id_b not in elected:
            df_bnk.at[bnk_pos[id_b], "_status"] = STATUS_REVISAR_COLISAO

    return df_bnk, df_fin
