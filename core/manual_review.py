"""
Fila de revisão manual para linhas ambíguas ou sem pareamento.
"""
from __future__ import annotations
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List

import pandas as pd

from .normalize import (
    STATUS_REVISAR, STATUS_REVISAR_COLISAO,
    STATUS_CONCILIADO_MANUAL, STATUS_IGNORADO_USUARIO,
    STATUS_IGNORADO_SEM_PAR, STATUS_SEM_PAREAMENTO,
)
from .params import ConciliacaoParams
from .combo_search import find_valid_indices, find_all_combos
from .candidate_selection import limit_subset_candidates


@dataclass
class ReviewCard:
    id_bnk: str
    data: datetime.date
    valor: Decimal
    historico: str
    candidatos: List[dict] = field(default_factory=list)
    combinacoes: List[List[str]] = field(default_factory=list)  # grupos válidos de IDs financeiros
    selecao_pre: List[str] = field(default_factory=list)
    decisao: str = ""  # "conciliar", "ignorar", ""


def _filter_to_valid_combos(
    candidatos: List[dict],
    target_f: float,
    tol: float,
    max_group_size: int,
) -> List[dict]:
    """
    Mantém apenas candidatos que aparecem em pelo menos uma combinação válida.
    Trata dois casos:
      k=1 — correspondência exata 1:1 (find_valid_indices só busca k≥2).
      k≥2 — combinação N lançamentos financeiros = 1 lançamento bancário.
    Retorna lista vazia se nenhuma combinação válida for encontrada.
    """
    vals = [float(c["valor"]) for c in candidatos]

    # Candidatos com correspondência exata de valor (k=1)
    exact_idx = {i for i, v in enumerate(vals) if abs(v - target_f) <= tol}

    # Candidatos que participam de alguma combinação k≥2
    combo_idx = find_valid_indices(vals, target_f, tol, max_group_size)

    valid_idx = exact_idx | combo_idx
    if not valid_idx:
        return []
    return [c for i, c in enumerate(candidatos) if i in valid_idx]


def build_review_queue(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> List[ReviewCard]:
    """
    Constrói fila de revisão para linhas bancárias com status REVISAR ou REVISAR_COLISAO.

    Regras aplicadas a cada candidato financeiro:
      1. Mesmo sinal que o extrato — débito com débito, crédito com crédito.
         Nunca mistura positivos e negativos para compor um valor.
      2. Mesma data (D0) — apenas lançamentos do mesmo dia do extrato.
         D±1/D±2 não são exibidos na revisão manual; cross-day 1:1 é
         resolvido exclusivamente pela passagem automática.
      3. Valor absoluto ≤ valor absoluto do extrato + tolerância —
         um candidato maior que o alvo nunca pode integrar uma soma válida.

    Classificação de probabilidade:
      Alta  — valor idêntico ao extrato (candidato 1:1 perfeito).
      Média — valor menor (candidato combinatório 1:N).

    Ordem: Alta → Média, desempate por diferença de valor.
    """
    tol = float(params.value_tolerance_cents) / 100

    fin_free_by_key: dict = defaultdict(list)
    for rec in df_fin.loc[
        df_fin["_status"] == STATUS_IGNORADO_SEM_PAR,
        ["_id", "_data", "_valor", "_historico", "_classif"],
    ].to_dict("records"):
        fin_free_by_key[(rec["_data"], rec["_valor"] > 0)].append(rec)

    cards = []
    revisar_mask = df_bnk["_status"].isin([STATUS_REVISAR, STATUS_REVISAR_COLISAO])
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))

    for rec_b in df_bnk.loc[revisar_mask, ["_id", "_data", "_valor", "_historico"]].to_dict("records"):
        bnk_sign = rec_b["_valor"] > 0
        abs_bnk = abs(float(rec_b["_valor"]))
        candidatos = []

        for rec_f in fin_free_by_key.get((rec_b["_data"], bnk_sign), []):
            # Regra 3: valor absoluto não pode superar o alvo
            abs_fin = abs(float(rec_f["_valor"]))
            if abs_fin > abs_bnk + tol:
                continue

            value_exact = rec_f["_valor"] == rec_b["_valor"]
            candidatos.append({
                "id":            rec_f["_id"],
                "data":          rec_f["_data"],
                "valor":         rec_f["_valor"],
                "historico":     rec_f["_historico"],
                "classif":       rec_f.get("_classif", ""),
                "delta_dias":    0,
                "probabilidade": "alta" if value_exact else "media",
            })

        limited = False
        if candidatos:
            candidatos, limited = limit_subset_candidates(
                candidatos,
                float(rec_b["_valor"]),
                int(getattr(params, "max_candidates_per_group", 0) or 0),
                value_key="valor",
            )
            candidatos = _filter_to_valid_combos(
                candidatos, float(rec_b["_valor"]), tol, params.max_group_size
            )

        if not candidatos:
            # Nenhum candidato válido — move para SEM_PAREAMENTO, fora da fila de revisão
            bi = bnk_pos.get(rec_b["_id"])
            if bi is not None:
                df_bnk.at[bi, "_status"] = STATUS_SEM_PAREAMENTO
            continue

        candidatos.sort(key=lambda x: (
            0 if x["probabilidade"] == "alta" else 1,
            abs(float(x["valor"]) - float(rec_b["_valor"])),
        ))

        # Todas as combinações válidas de candidatos que somam ao valor bancário
        vals_f = [float(c["valor"]) for c in candidatos]
        combos_idx = [] if limited else find_all_combos(vals_f, float(rec_b["_valor"]), tol, params.max_group_size)
        combinacoes = [[candidatos[i]["id"] for i in combo] for combo in combos_idx]

        cards.append(ReviewCard(
            id_bnk=rec_b["_id"],
            data=rec_b["_data"],
            valor=rec_b["_valor"],
            historico=rec_b["_historico"],
            candidatos=candidatos,
            combinacoes=combinacoes,
        ))

    return cards


def apply_review_decisions(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    cards: List[ReviewCard],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Aplica as decisões de revisão manual aos DataFrames.
    """
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    for card in cards:
        if not card.decisao:
            continue
        bi = bnk_pos.get(card.id_bnk)
        if bi is None:
            continue

        if card.decisao == "ignorar":
            df_bnk.at[bi, "_status"] = STATUS_IGNORADO_USUARIO
            df_bnk.at[bi, "_metodo"] = "manual:ignorado"
        elif card.decisao == "conciliar" and card.selecao_pre:
            ids_fin = card.selecao_pre
            df_bnk.at[bi, "_status"] = STATUS_CONCILIADO_MANUAL
            df_bnk.at[bi, "_metodo"] = "manual"
            df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)
            for id_f in ids_fin:
                fi = fin_pos.get(id_f)
                if fi is not None:
                    df_fin.at[fi, "_status"] = STATUS_CONCILIADO_MANUAL
                    df_fin.at[fi, "_metodo"] = "manual"
                    df_fin.at[fi, "_id_bnk"] = card.id_bnk

    return df_bnk, df_fin
