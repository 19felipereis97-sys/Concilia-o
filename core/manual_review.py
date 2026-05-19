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
    STATUS_CONCILIADO, STATUS_CONCILIADO_MANUAL,
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
    id_fin: str = ""
    tipo: str = "1:N"
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

    fin_available_by_key: dict = defaultdict(list)
    fin_statuses = df_fin["_status"].astype(str)
    blocked_mask = (
        (fin_statuses == STATUS_REVISAR)
        & df_fin["_metodo"].astype(str).str.startswith("bloqueado:", na=False)
    )
    available_mask = (fin_statuses == STATUS_IGNORADO_SEM_PAR) | blocked_mask
    for rec in df_fin.loc[
        available_mask,
        ["_id", "_data", "_valor", "_historico", "_classif"],
    ].to_dict("records"):
        fin_available_by_key[(rec["_data"], rec["_valor"] > 0)].append(rec)

    cards = []
    revisar_mask = (
        df_bnk["_status"].isin([STATUS_REVISAR, STATUS_REVISAR_COLISAO])
        & ~df_bnk["_metodo"].astype(str).str.startswith("N:1", na=False)
    )
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))

    for rec_b in df_bnk.loc[revisar_mask, ["_id", "_data", "_valor", "_historico"]].to_dict("records"):
        bnk_sign = rec_b["_valor"] > 0
        abs_bnk = abs(float(rec_b["_valor"]))
        candidatos = []

        for rec_f in fin_available_by_key.get((rec_b["_data"], bnk_sign), []):
            fi = df_fin.index[df_fin["_id"] == rec_f["_id"]]
            if len(fi) == 0:
                continue
            fin_row = df_fin.loc[fi[0]]
            fin_status = str(fin_row.get("_status", ""))
            if fin_status == STATUS_REVISAR and str(fin_row.get("_id_bnk", "")) != rec_b["_id"]:
                continue
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
                max_group_size=params.max_group_size,
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

        if len(combinacoes) == 1:
            _auto_conciliate_bank_card(df_bnk, df_fin, rec_b["_id"], combinacoes[0], "1:N D revisao_unica")
            continue

        cards.append(ReviewCard(
            id_bnk=rec_b["_id"],
            data=rec_b["_data"],
            valor=rec_b["_valor"],
            historico=rec_b["_historico"],
            candidatos=candidatos,
            combinacoes=combinacoes,
        ))

    fin_revisar_mask = (
        (df_fin["_status"].astype(str) == STATUS_REVISAR)
        & df_fin["_metodo"].astype(str).str.startswith("bloqueado:N:1", na=False)
    )
    for rec_f in df_fin.loc[fin_revisar_mask, ["_id", "_data", "_valor", "_historico", "_id_bnk"]].to_dict("records"):
        ids_bnk = [x.strip() for x in str(rec_f.get("_id_bnk", "")).split(";") if x.strip()]
        candidatos = []
        for id_b in ids_bnk:
            bi = bnk_pos.get(id_b)
            if bi is None:
                continue
            row_b = df_bnk.loc[bi]
            if str(row_b.get("_status", "")) != STATUS_REVISAR:
                continue
            if str(row_b.get("_ids_fin", "")) != rec_f["_id"]:
                continue
            candidatos.append({
                "id": id_b,
                "data": row_b.get("_data"),
                "valor": row_b.get("_valor"),
                "historico": row_b.get("_historico", ""),
                "classif": row_b.get("_classif", ""),
                "delta_dias": 0,
                "probabilidade": "media",
            })

        if not candidatos:
            fi = fin_pos.get(rec_f["_id"])
            if fi is not None:
                df_fin.at[fi, "_status"] = STATUS_IGNORADO_SEM_PAR
                df_fin.at[fi, "_metodo"] = ""
                df_fin.at[fi, "_id_bnk"] = ""
            continue

        vals_b = [float(c["valor"]) for c in candidatos]
        combos_idx = find_all_combos(vals_b, float(rec_f["_valor"]), tol, params.max_group_size)
        combinacoes = [[candidatos[i]["id"] for i in combo] for combo in combos_idx]

        if len(combinacoes) == 1:
            _auto_conciliate_fin_card(df_bnk, df_fin, rec_f["_id"], combinacoes[0], "N:1 revisao_unica")
            continue

        cards.append(ReviewCard(
            id_bnk="",
            id_fin=rec_f["_id"],
            tipo="N:1",
            data=rec_f["_data"],
            valor=rec_f["_valor"],
            historico=rec_f["_historico"],
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
            # Sem decisão: libera igualmente para o confronto manual
            if card.id_fin:
                fi = fin_pos.get(card.id_fin)
                if fi is not None:
                    df_fin.at[fi, "_status"] = STATUS_IGNORADO_SEM_PAR
                    df_fin.at[fi, "_metodo"] = ""
                    df_fin.at[fi, "_id_bnk"] = ""
                    _release_blocked_banco(df_bnk, card.id_fin, set())
            else:
                bi = bnk_pos.get(card.id_bnk)
                if bi is not None:
                    df_bnk.at[bi, "_status"] = STATUS_SEM_PAREAMENTO
                    df_bnk.at[bi, "_metodo"] = ""
                    df_bnk.at[bi, "_ids_fin"] = ""
                    _release_blocked_financeiro(df_fin, card.id_bnk, set())
            continue

        if card.id_fin:
            fi = fin_pos.get(card.id_fin)
            if fi is None:
                continue
            if card.decisao == "ignorar" or (card.decisao == "conciliar" and not card.selecao_pre):
                # Libera para o confronto manual (não ignora permanentemente)
                df_fin.at[fi, "_status"] = STATUS_IGNORADO_SEM_PAR
                df_fin.at[fi, "_metodo"] = ""
                df_fin.at[fi, "_id_bnk"] = ""
                _release_blocked_banco(df_bnk, card.id_fin, set())
            elif card.decisao == "conciliar" and card.selecao_pre:
                ids_bnk = list(dict.fromkeys(card.selecao_pre))
                df_fin.at[fi, "_status"] = STATUS_CONCILIADO_MANUAL
                df_fin.at[fi, "_metodo"] = "manual"
                df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
                _release_blocked_banco(df_bnk, card.id_fin, set(ids_bnk))
                for id_b in ids_bnk:
                    bi_sel = bnk_pos.get(id_b)
                    if bi_sel is not None:
                        df_bnk.at[bi_sel, "_status"] = STATUS_CONCILIADO_MANUAL
                        df_bnk.at[bi_sel, "_metodo"] = "manual"
                        df_bnk.at[bi_sel, "_ids_fin"] = card.id_fin
            continue

        bi = bnk_pos.get(card.id_bnk)
        if bi is None:
            continue

        if card.decisao == "ignorar" or (card.decisao == "conciliar" and not card.selecao_pre):
            # Libera para o confronto manual (não ignora permanentemente)
            df_bnk.at[bi, "_status"] = STATUS_SEM_PAREAMENTO
            df_bnk.at[bi, "_metodo"] = ""
            df_bnk.at[bi, "_ids_fin"] = ""
            _release_blocked_financeiro(df_fin, card.id_bnk, set())
        elif card.decisao == "conciliar" and card.selecao_pre:
            ids_fin = list(dict.fromkeys(card.selecao_pre))
            df_bnk.at[bi, "_status"] = STATUS_CONCILIADO_MANUAL
            df_bnk.at[bi, "_metodo"] = "manual"
            df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)
            _release_blocked_financeiro(df_fin, card.id_bnk, set(ids_fin))
            for id_f in ids_fin:
                fi = fin_pos.get(id_f)
                if fi is not None:
                    df_fin.at[fi, "_status"] = STATUS_CONCILIADO_MANUAL
                    df_fin.at[fi, "_metodo"] = "manual"
                    df_fin.at[fi, "_id_bnk"] = card.id_bnk

    return df_bnk, df_fin


def _auto_conciliate_bank_card(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    id_bnk: str,
    ids_fin: list[str],
    metodo: str,
) -> None:
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))
    bi = bnk_pos.get(id_bnk)
    if bi is None:
        return
    df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
    df_bnk.at[bi, "_metodo"] = f"{metodo} soma={len(ids_fin)}"
    df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)
    _release_blocked_financeiro(df_fin, id_bnk, set(ids_fin))
    for id_f in ids_fin:
        fi = fin_pos.get(id_f)
        if fi is not None:
            df_fin.at[fi, "_status"] = STATUS_CONCILIADO
            df_fin.at[fi, "_metodo"] = f"{metodo} soma={len(ids_fin)}"
            df_fin.at[fi, "_id_bnk"] = id_bnk


def _auto_conciliate_fin_card(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    id_fin: str,
    ids_bnk: list[str],
    metodo: str,
) -> None:
    bnk_pos = dict(zip(df_bnk["_id"], df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"], df_fin.index))
    fi = fin_pos.get(id_fin)
    if fi is None:
        return
    df_fin.at[fi, "_status"] = STATUS_CONCILIADO
    df_fin.at[fi, "_metodo"] = f"{metodo} soma={len(ids_bnk)}"
    df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
    _release_blocked_banco(df_bnk, id_fin, set(ids_bnk))
    for id_b in ids_bnk:
        bi = bnk_pos.get(id_b)
        if bi is not None:
            df_bnk.at[bi, "_status"] = STATUS_CONCILIADO
            df_bnk.at[bi, "_metodo"] = f"{metodo} soma={len(ids_bnk)}"
            df_bnk.at[bi, "_ids_fin"] = id_fin


def _release_blocked_financeiro(
    df_fin: pd.DataFrame,
    id_bnk: str,
    keep_ids: set[str],
) -> None:
    """Libera financeiros bloqueados na revisão que não foram usados na decisão."""
    mask = (
        (df_fin["_status"].astype(str) == STATUS_REVISAR)
        & df_fin["_metodo"].astype(str).str.startswith("bloqueado:", na=False)
        & (df_fin["_id_bnk"].astype(str) == str(id_bnk))
        & ~df_fin["_id"].astype(str).isin(keep_ids)
    )
    if not mask.any():
        return
    df_fin.loc[mask, "_status"] = STATUS_IGNORADO_SEM_PAR
    df_fin.loc[mask, "_metodo"] = ""
    df_fin.loc[mask, "_id_bnk"] = ""


def _release_blocked_banco(
    df_bnk: pd.DataFrame,
    id_fin: str,
    keep_ids: set[str],
) -> None:
    mask = (
        (df_bnk["_status"].astype(str) == STATUS_REVISAR)
        & df_bnk["_metodo"].astype(str).str.startswith("N:1", na=False)
        & (df_bnk["_ids_fin"].astype(str) == str(id_fin))
        & ~df_bnk["_id"].astype(str).isin(keep_ids)
    )
    if not mask.any():
        return
    df_bnk.loc[mask, "_status"] = STATUS_SEM_PAREAMENTO
    df_bnk.loc[mask, "_metodo"] = ""
    df_bnk.loc[mask, "_ids_fin"] = ""
