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
    STATUS_CONCILIADO, STATUS_REVISAR, STATUS_REVISAR_COLISAO,
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
    ambiguous: set = set()  # bancos com múltiplos candidatos no mesmo offset

    for id_b, candidates in by_bnk.items():
        if len(candidates) > 1:
            ambiguous.add(id_b)
            continue
        id_f, k = candidates[0]
        if id_f not in elected_fin:
            elected[id_b] = (id_f, k)
            elected_fin[id_f] = id_b

    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

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

    # Perdedores e ambíguos
    for id_b, candidates in by_bnk.items():
        bi = bnk_pos[id_b]

        if id_b in elected:
            # Vencedor: fins não eleitos viram REVISAR_COLISAO
            id_f_won, _ = elected[id_b]
            for id_f, _ in candidates:
                if id_f != id_f_won:
                    fi = fin_pos[id_f]
                    if df_fin.at[fi, "_status"] != STATUS_CONCILIADO:
                        df_fin.at[fi, "_status"] = STATUS_REVISAR_COLISAO

        elif id_b in ambiguous:
            # Múltiplos fins com mesmo valor e data → ambiguidade 1:1 → REVISAR
            k = candidates[0][1]
            label = params.offset_label(k)
            df_bnk.at[bi, "_status"] = STATUS_REVISAR
            df_bnk.at[bi, "_metodo"] = f"1:1 {label} ambiguo"
            df_bnk.at[bi, "_ids_fin"] = ";".join(str(id_f) for id_f, _ in candidates)
            for id_f, _ in candidates:
                fi = fin_pos.get(id_f)
                if fi is not None and df_fin.at[fi, "_status"] not in {
                    STATUS_CONCILIADO, STATUS_REVISAR
                }:
                    df_fin.at[fi, "_status"] = STATUS_REVISAR
                    df_fin.at[fi, "_metodo"] = f"bloqueado:1:1 {label} ambiguo"
                    df_fin.at[fi, "_id_bnk"] = id_b

        else:
            # Candidato único mas o fin foi eleito por outro banco
            df_bnk.at[bi, "_status"] = STATUS_REVISAR_COLISAO

    return df_bnk, df_fin
