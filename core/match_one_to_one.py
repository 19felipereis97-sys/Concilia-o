"""
Conciliação 1:1 com suporte a janela de datas configurável.

Implementação vetorizada via pandas merge:
  - _valor_f convertido para centavos inteiros (int64) → join exato sem risco
    de igualdade em ponto flutuante e hashing mais rápido que Decimal.
  - Cascata por offset: cada linha bancária usa o primeiro offset com candidatos.
  - Linhas bancárias sem nenhum candidato permanecem intocadas.
  - Retorna pending_pairs para resolve_collisions tratar colisões N→fin.
"""
from __future__ import annotations
import datetime
from typing import List, Optional, Tuple

import pandas as pd

from .normalize import STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR
from .params import ConciliacaoParams


def match_one_to_one(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
    offsets: Optional[List[int]] = None,
    extra_bnk_statuses: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[Tuple]]:
    """
    Retorna (df_bnk, df_fin, pending_pairs).
    pending_pairs: lista de (id_bnk, id_fin, offset_k) para resolve_collisions.
    """
    if offsets is None:
        offsets = params.date_offsets

    _free_statuses = {STATUS_SEM_PAREAMENTO, *(extra_bnk_statuses or [])}

    vf = "_valor_f" if "_valor_f" in df_fin.columns else "_valor"

    fin_free = df_fin.loc[df_fin["_status"] == STATUS_IGNORADO_SEM_PAR, ["_id", "_data", vf]].copy()
    bnk_free = df_bnk.loc[df_bnk["_status"].isin(_free_statuses), ["_id", "_data", vf]].copy()

    if fin_free.empty or bnk_free.empty:
        return df_bnk, df_fin, []

    # Centavos inteiros: hashing de int64 >> Decimal, sem colisão de float
    fin_free["_vc"] = (fin_free[vf].astype(float) * 100).round().astype("int64")
    bnk_free["_vc"] = (bnk_free[vf].astype(float) * 100).round().astype("int64")

    fin_lookup = fin_free[["_id", "_data", "_vc"]].rename(
        columns={"_id": "_id_fin", "_data": "_data_s"}
    )

    pending_pairs: List[Tuple] = []
    matched_bnk: set = set()

    for offset in offsets:
        unmatched = bnk_free.loc[~bnk_free["_id"].isin(matched_bnk), ["_id", "_data", "_vc"]].copy()
        if unmatched.empty:
            break

        unmatched["_data_s"] = unmatched["_data"] + datetime.timedelta(days=offset)
        hits = unmatched[["_id", "_data_s", "_vc"]].merge(fin_lookup, on=["_data_s", "_vc"])

        if hits.empty:
            continue

        for rec in hits[["_id", "_id_fin"]].to_dict("records"):
            pending_pairs.append((rec["_id"], rec["_id_fin"], offset))
        matched_bnk.update(hits["_id"].unique())

    return df_bnk, df_fin, pending_pairs
