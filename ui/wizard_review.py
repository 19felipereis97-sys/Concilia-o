"""
Etapa de revisão manual — layout lado-a-lado com checkboxes e subtotal ao vivo.
"""
from __future__ import annotations
from decimal import Decimal
from typing import List

import pandas as pd
import streamlit as st

from core.manual_review import ReviewCard
from ui.components import fmt_valor, fmt_data

_PROB_LABEL = {
    "alta":  "◆ Alta",
    "media": "◇ Média",
    "baixa": "○ Baixa",
}

_STATUS_ICON = {
    "conciliar": "✅",
    "ignorar":   "🚫",
    "":          "⏳",
}


def _render_cand_checkbox(card: ReviewCard, cand: dict, selecionados: list) -> None:
    card_key = card.id_fin or card.id_bnk
    delta_label = f"+{cand['delta_dias']}d" if cand["delta_dias"] else "D0"
    prob = _PROB_LABEL.get(cand.get("probabilidade", "baixa"), "○")
    label = (
        f"[{prob}] `{cand['id']}` | {fmt_data(cand['data'])} ({delta_label})"
        f" | {fmt_valor(cand['valor'])} | {cand['historico'][:45]}"
    )
    if st.checkbox(label, key=f"chk_{card_key}_{cand['id']}", value=cand["id"] in card.selecao_pre):
        selecionados.append(cand["id"])


def step_review(cards: List[ReviewCard]) -> List[ReviewCard]:
    st.subheader("Revisão Manual")

    if not cards:
        st.success("Nenhuma linha requer revisão manual!")
        return cards

    # ── 2. Barra de progresso ─────────────────────────────────────────────────
    n_total     = len(cards)
    n_decididos = sum(1 for c in cards if c.decisao)
    pct = n_decididos / n_total
    st.progress(pct, text=f"{n_decididos} de {n_total} revisados")

    # ── 5. Filtro global de cards ─────────────────────────────────────────────
    filtro_global = st.text_input(
        "Filtrar lançamentos:",
        key="review_filtro_global",
        placeholder="Data, valor ou histórico…",
        label_visibility="collapsed",
    )

    cards_visiveis = cards
    if filtro_global.strip():
        term = filtro_global.strip().lower()
        cards_visiveis = [
            c for c in cards
            if term in c.historico.lower()
            or term in fmt_data(c.data).lower()
            or term in fmt_valor(c.valor).lower()
            or term in (c.id_fin or c.id_bnk).lower()
        ]
        n_ocultos = len(cards) - len(cards_visiveis)
        if n_ocultos:
            st.caption(f"_{n_ocultos} lançamento(s) oculto(s) pelo filtro_")

    # Primeiro card ainda sem decisão abre automaticamente
    primeiro_pendente = next((i for i, c in enumerate(cards_visiveis) if not c.decisao), None)

    # ── Cards ─────────────────────────────────────────────────────────────────
    for i, card in enumerate(cards_visiveis):
        card_key = card.id_fin or card.id_bnk

        # ── 1. Ícone de status no título do expander ──────────────────────────
        icon  = _STATUS_ICON.get(card.decisao, "⏳")
        label = f"{icon} #{i+1} | {fmt_data(card.data)} | {fmt_valor(card.valor)} | {card.historico[:60]}"

        with st.expander(label, expanded=(i == primeiro_pendente)):
            col_bnk, col_fin = st.columns([1, 2])

            with col_bnk:
                base_label = "Lançamento Financeiro" if card.id_fin else "Lançamento Bancário"
                st.markdown(f"**{base_label}**")
                st.markdown(f"**ID:** `{card_key}`")
                st.markdown(f"**Data:** {fmt_data(card.data)}")
                st.markdown(f"**Valor:** {fmt_valor(card.valor)}")
                st.markdown("**Histórico:**")
                st.caption(card.historico)

            with col_fin:
                if card.candidatos:
                    cand_label = "Candidatos Bancários" if card.id_fin else "Candidatos Financeiros"
                    st.markdown(f"**{cand_label}**")

                    # ── 3. Navegação de combinações válidas ───────────────────
                    if card.combinacoes:
                        n_combos  = len(card.combinacoes)
                        combo_key = f"combo_idx_{card_key}"
                        if combo_key not in st.session_state:
                            st.session_state[combo_key] = 0
                        idx = min(st.session_state[combo_key], n_combos - 1)

                        nav1, nav2, nav3, nav4 = st.columns([1, 1, 3, 3])
                        with nav1:
                            if st.button("◀", key=f"btn_prev_{card_key}", disabled=(idx == 0)):
                                for c in card.candidatos:
                                    st.session_state[f"chk_{card_key}_{c['id']}"] = False
                                st.session_state[combo_key] = idx - 1
                                st.rerun()
                        with nav2:
                            if st.button("▶", key=f"btn_next_{card_key}", disabled=(idx >= n_combos - 1)):
                                for c in card.candidatos:
                                    st.session_state[f"chk_{card_key}_{c['id']}"] = False
                                st.session_state[combo_key] = idx + 1
                                st.rerun()
                        with nav3:
                            st.markdown(f"**Combinação {idx + 1} / {n_combos}**")
                        with nav4:
                            if st.button("⚡ Selecionar combinação", key=f"btn_sel_combo_{card_key}"):
                                combo_atual = set(card.combinacoes[idx])
                                for c in card.candidatos:
                                    st.session_state[f"chk_{card_key}_{c['id']}"] = c["id"] in combo_atual
                                st.rerun()

                    filtro = st.text_input(
                        "Filtrar candidatos:",
                        key=f"filter_{card_key}",
                        placeholder="Histórico, ID ou classificação…",
                        label_visibility="collapsed",
                    )

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

                    provaveis = [c for c in visiveis if c.get("probabilidade") in ("alta", "media")]
                    outros    = [c for c in visiveis if c.get("probabilidade") == "baixa"]

                    selecionados: list = []

                    if provaveis:
                        n_alta  = sum(1 for c in provaveis if c.get("probabilidade") == "alta")
                        n_media = sum(1 for c in provaveis if c.get("probabilidade") == "media")
                        partes  = []
                        if n_alta:
                            partes.append(f"{n_alta} alta{'s' if n_alta > 1 else ''}")
                        if n_media:
                            partes.append(f"{n_media} média{'s' if n_media > 1 else ''}")
                        st.caption(f"Mais prováveis — {', '.join(partes)}")
                        for cand in provaveis:
                            _render_cand_checkbox(card, cand, selecionados)

                    if outros:
                        with st.expander(f"Outros disponíveis — {len(outros)} movimento(s)"):
                            for cand in outros:
                                _render_cand_checkbox(card, cand, selecionados)

                    card.selecao_pre = selecionados

                    soma = sum(c["valor"] for c in card.candidatos if c["id"] in selecionados)
                    diff = soma - card.valor
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Selecionado", fmt_valor(soma))
                    m2.metric("Bancário",    fmt_valor(card.valor))
                    m3.metric("Diferença",   fmt_valor(diff))

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
                    ["Conciliar seleção", "Ignorar revisão (liberar para confronto manual)"],
                    key=f"review_dec_{card_key}",
                    index=1,
                    horizontal=True,
                )
                card.decisao = "conciliar" if decisao == "Conciliar seleção" else "ignorar"
            else:
                card.decisao = "ignorar"
                st.info("Sem candidatos disponíveis — será liberado para o confronto manual.")

    # ── 4. Resumo global ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("**Resumo das decisões**")

    n_conciliar = sum(1 for c in cards if c.decisao == "conciliar")
    n_ignorar   = sum(1 for c in cards if c.decisao == "ignorar")
    n_pendente  = sum(1 for c in cards if not c.decisao)
    v_conciliar = sum((c.valor for c in cards if c.decisao == "conciliar"), Decimal("0"))
    v_ignorar   = sum((c.valor for c in cards if c.decisao == "ignorar"),   Decimal("0"))
    v_pendente  = sum((c.valor for c in cards if not c.decisao),            Decimal("0"))

    resumo_df = pd.DataFrame({
        "Decisão":     ["✅ Conciliar", "🚫 Ignorar", "⏳ Pendente"],
        "Qtd":         [n_conciliar,    n_ignorar,    n_pendente],
        "Valor Total": [fmt_valor(v_conciliar), fmt_valor(v_ignorar), fmt_valor(v_pendente)],
    })
    st.dataframe(resumo_df, use_container_width=True, hide_index=True)

    return cards
