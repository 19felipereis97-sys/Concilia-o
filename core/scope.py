from __future__ import annotations

import pandas as pd

from .normalize import STATUS_CONCILIADO, STATUS_SEM_PAREAMENTO


def _modalidade_key(modalidade: object) -> str:
    value = modalidade.value if hasattr(modalidade, "value") else str(modalidade)
    return value.strip().upper()


def apply_financeiro_scope(df_bnk: pd.DataFrame, modalidade: object) -> tuple[pd.DataFrame, int]:
    """
    Remove do confronto bancario o sinal oposto ao arquivo financeiro enviado.

    - PAGAMENTOS: entradas/creditos do banco ficam conciliados como recebimento
      fora do confronto.
    - RECEBIMENTOS: saidas/debitos do banco ficam conciliados como pagamento
      fora do confronto.

    Retorna (df_bnk, quantidade_marcada).
    """
    if df_bnk.empty or "_valor" not in df_bnk.columns:
        return df_bnk, 0

    mode = _modalidade_key(modalidade)
    if mode in {"PAGAMENTOS", "PAGAMENTOS"}:
        mask = df_bnk["_valor"] > 0
        metodo = "fora_confronto_recebimento"
    elif mode in {"RECEBIMENTOS", "RECEBIMENTOS"}:
        mask = df_bnk["_valor"] < 0
        metodo = "fora_confronto_pagamento"
    else:
        return df_bnk, 0

    if "_status" in df_bnk.columns:
        mask = mask & (df_bnk["_status"].astype(str) == STATUS_SEM_PAREAMENTO)
    qtd = int(mask.sum())
    if qtd == 0:
        return df_bnk, 0

    for col, default in [("_status", STATUS_SEM_PAREAMENTO), ("_metodo", ""), ("_ids_fin", "")]:
        if col not in df_bnk.columns:
            df_bnk[col] = default

    df_bnk.loc[mask, "_status"] = STATUS_CONCILIADO
    df_bnk.loc[mask, "_metodo"] = metodo
    df_bnk.loc[mask, "_ids_fin"] = ""
    return df_bnk, qtd
