"""
Exportação e aplicação do De-Para contábil.
"""
from __future__ import annotations
import csv
import io
from typing import List, Dict

import pandas as pd


def export_depara_csv(depara_rows: List[dict]) -> bytes:
    """
    Exporta lista de dicts {classif, debito, credito} como CSV bytes.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["classif", "debito", "credito"])
    writer.writeheader()
    for row in depara_rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def apply_depara(df_fin: pd.DataFrame, depara_rows: List[dict]) -> pd.DataFrame:
    """
    Adiciona colunas _conta_debito e _conta_credito ao df_fin baseado no De-Para.
    """
    mapping = {r["classif"]: (r["debito"], r["credito"]) for r in depara_rows}
    df = df_fin.copy()
    df["_conta_debito"] = df["_classif"].map(lambda c: mapping.get(c, ("", ""))[0])
    df["_conta_credito"] = df["_classif"].map(lambda c: mapping.get(c, ("", ""))[1])
    return df


def get_depara_dict(depara_rows: List[dict]) -> dict:
    """
    Converte lista de De-Para para dict {classif: (debito, credito)}.
    """
    return {r["classif"]: (r["debito"], r["credito"]) for r in depara_rows}
