"""
Etapa de revisão manual — layout lado-a-lado com checkboxes e subtotal ao vivo.
"""
from __future__ import annotations
from decimal import Decimal
from typing import List

import streamlit as st

from core.manual_review import ReviewCard
from ui.components import fmt_valor, fmt_data

_PROB_LABEL = {
    "alta":  "◆ Alta",
    "media": "◇ Média",
    "baixa": "○ Baixa",
}


def _render_cand_checkbox(card: ReviewCard, cand: dict, selecionados: list) -> None:
    delta_label = f"+{cand['delta_dias']}d" if cand["delta_dias"] else "D0"
    prob = _PROB_LABEL.get(cand.get("probabilidade", "baixa"), "○")
    label = (
        f"[{prob}] `{cand['id']}` | {fmt_data(cand['data'])} ({delta_label})"
        f" | {fmt_valor(cand['valor'])} | {cand['historico'][:45]}"
    )
    if st.checkbox(label, key=f"chk_{card.id_bnk}_{cand['id']}", value=cand["id"] in card.selecao_pre):
        selecionados.append(cand["id"])


def step_review(cards: List[ReviewCard]) -> List[ReviewCard]:
    st.subheader("Etapa 8 — Revisão Manual")

    if not cards:
        st.success("Nenhuma linha requer revisão manual!")
        return cards

    st.info(f"Existem **{len(cards)}** linhas que necessitam de revisão manual.")

    for i, card in enumerate(cards):
        label = f"#{i+1} | {fmt_data(card.data)} | {fmt_valor(card.valor)} | {card.historico[:60]}"
        with st.expander(label, expanded=(i == 0)):
            col_bnk, col_fin = st.columns([1, 2])

            with col_bnk:
                st.markdown("**Lançamento Bancário**")
                st.markdown(f"**ID:** `{card.id_bnk}`")
                st.markdown(f"**Data:** {fmt_data(card.data)}")
                st.markdown(f"**Valor:** {fmt_valor(card.valor)}")
                st.markdown("**Histórico:**")
                st.caption(card.historico)

            with col_fin:
                if card.candidatos:
                    st.markdown("**Candidatos Financeiros**")

                    filtro = st.text_input(
                        "Filtrar candidatos:",
                        key=f"filter_{card.id_bnk}",
                        placeholder="Histórico, ID ou classificação…",
                        label_visibility="collapsed",
                    )

                    # Candidatos previamente selecionados sempre visíveis
                    pinned = set(card.selecao_pre)

                    def _matches(c: dict) -> bool:
                        if not filtro:
                            return True
                        term = filtro.strip().lower()
                        return (
                            term in c.get("historico", "").lower()
                            or term in c.get("id", "").lower()
                            or term in c.get("classif", "").lower()
                        )

                    visiveis  = [c for c in card.candidatos if c["id"] in pinned or _matches(c)]
                    n_ocultos = len(card.candidatos) - len(visiveis)

                    if filtro and n_ocultos:
                        st.caption(f"_{n_ocultos} candidato(s) oculto(s) pelo filtro_")

                    prováveis = [c for c in visiveis if c.get("probabilidade") in ("alta", "media")]
                    outros    = [c for c in visiveis if c.get("probabilidade") == "baixa"]

                    selecionados: list = []

                    if prováveis:
                        n_alta  = sum(1 for c in prováveis if c.get("probabilidade") == "alta")
                        n_media = sum(1 for c in prováveis if c.get("probabilidade") == "media")
                        partes  = []
                        if n_alta:
                            partes.append(f"{n_alta} alta{'s' if n_alta > 1 else ''}")
                        if n_media:
                            partes.append(f"{n_media} média{'s' if n_media > 1 else ''}")
                        st.caption(f"Mais prováveis — {', '.join(partes)}")
                        for cand in prováveis:
                            _render_cand_checkbox(card, cand, selecionados)

                    if outros:
                        with st.expander(f"Outros disponíveis — {len(outros)} movimento(s)"):
                            for cand in outros:
                                _render_cand_checkbox(card, cand, selecionados)

                    card.selecao_pre = selecionados

                    # Live subtotal
                    soma = sum(c["valor"] for c in card.candidatos if c["id"] in selecionados)
                    diff = soma - card.valor
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Selecionado", fmt_valor(soma))
                    m2.metric("Bancário", fmt_valor(card.valor))
                    m3.metric("Diferença", fmt_valor(diff))

                    if selecionados:
                        if abs(diff) > Decimal("0.01"):
                            st.warning("Soma selecionada difere do valor bancário.")
                        else:
                            st.success("Soma confere.")
                else:
                    st.info("Nenhum candidato financeiro disponível.")

            st.divider()
            if card.candidatos:
                decisao = st.radio(
                    "Decisão:",
                    ["Conciliar seleção", "Ignorar linha", "Deixar sem decisão"],
                    key=f"review_dec_{card.id_bnk}",
                    index=2,
                    horizontal=True,
                )
                card.decisao = (
                    "conciliar" if decisao == "Conciliar seleção"
                    else "ignorar" if decisao == "Ignorar linha"
                    else ""
                )
            else:
                decisao = st.radio(
                    "Decisão:",
                    ["Ignorar linha", "Deixar sem decisão"],
                    key=f"review_dec_{card.id_bnk}",
                    index=1,
                    horizontal=True,
                )
                card.decisao = "ignorar" if decisao == "Ignorar linha" else ""

    return cards
