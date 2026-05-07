"""
Conciliador manual — Processo 4.
Duas colunas: extrato bancário (seleção) × financeiro (checkboxes com destaque de elegíveis).
"""
from __future__ import annotations
from decimal import Decimal
from typing import Optional

import pandas as pd
import streamlit as st

from core.normalize import (
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
    STATUS_CONCILIADO_MANUAL, STATUS_PARCIAL, STATUS_PENDENTE_PARCIAL,
)
from core.params import ConciliacaoParams
from ui.components import fmt_valor, fmt_data

_GREEN  = "#21C354"
_YELLOW = "#FFB800"
_RED    = "#FF4B4B"
_DIM    = "rgba(255,255,255,0.35)"


def _card_css() -> None:
    st.markdown("""
    <style>
    .mc-card {
        border: 1.5px solid #2a2a2a;
        border-radius: 10px;
        padding: 12px 14px;
        margin-bottom: 8px;
        background: #0e1117;
        cursor: pointer;
        transition: border-color .15s;
    }
    .mc-card.selected {
        border-color: #21C354;
        background: #0d1f15;
    }
    .mc-card .mc-id   { font-size:.72em; color:#666; margin-bottom:2px; }
    .mc-card .mc-val  { font-size:1.15em; font-weight:700; }
    .mc-card .mc-date { font-size:.8em; color:#aaa; margin-top:2px; }
    .mc-card .mc-hist { font-size:.75em; color:#888; margin-top:3px;
                        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .mc-badge {
        display:inline-block; border-radius:20px; padding:2px 10px;
        font-size:.7em; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
    }
    .mc-summary {
        display:flex; gap:24px; align-items:center;
        background:#111; border-radius:10px; padding:14px 20px; margin-top:12px;
    }
    .mc-summary .mc-metric { display:flex; flex-direction:column; gap:2px; }
    .mc-summary .mc-label  { font-size:.7em; color:#888; text-transform:uppercase; letter-spacing:.05em; }
    .mc-summary .mc-value  { font-size:1.05em; font-weight:700; }
    </style>
    """, unsafe_allow_html=True)


def _color_for_value(v: float) -> str:
    if v > 0:
        return _GREEN
    if v < 0:
        return _RED
    return _DIM


def _render_bank_panel(bnk_sem: pd.DataFrame) -> Optional[str]:
    id_list = list(bnk_sem["_id"].astype(str))

    # Cache do lookup para format_func (evita percorrer o DataFrame por ID a cada chamada)
    _row_cache: dict = {
        str(row["_id"]): row
        for _, row in bnk_sem.iterrows()
    }

    def _fmt(rid: str) -> str:
        row = _row_cache.get(str(rid))
        if row is None:
            return str(rid)
        val  = float(row["_valor"])
        sign = "+" if val > 0 else ""
        return f"{sign}{fmt_valor(row['_valor'])}  ·  {fmt_data(row['_data'])}  ·  {str(row.get('_historico',''))[:40]}"

    sel_id: Optional[str] = st.selectbox(
        "Selecione o lançamento:",
        options=id_list,
        format_func=_fmt,
        index=None,
        placeholder="— escolha um lançamento —",
        key="mc_bnk_sel",
    )

    # Card de detalhe da entrada selecionada
    if sel_id:
        row   = _row_cache.get(str(sel_id))
        if row is not None:
            val   = float(row["_valor"])
            color = _color_for_value(val)
            st.markdown(f"""
            <div style="background:#0d1f15;border:2px solid {_GREEN};border-radius:10px;
                        padding:12px 16px;margin-top:8px;">
                <div style="font-size:.7em;color:{_GREEN};font-weight:700;
                            text-transform:uppercase;letter-spacing:.05em;">Selecionado</div>
                <div style="font-size:1.2em;font-weight:700;color:{color};margin-top:4px;">
                    {fmt_valor(row['_valor'])}</div>
                <div style="font-size:.8em;color:#aaa;margin-top:2px;">{fmt_data(row['_data'])}</div>
                <div style="font-size:.76em;color:#888;margin-top:4px;word-break:break-word;">
                    {str(row.get('_historico',''))}</div>
            </div>
            """, unsafe_allow_html=True)

    return str(sel_id) if sel_id else None


def _eligible_ids(fin_sem: pd.DataFrame, bank_row) -> set:
    """IDs de lançamentos financeiros na mesma data do bancário (destacados como elegíveis)."""
    bank_date = bank_row["_data"]
    return set(fin_sem.loc[fin_sem["_data"] == bank_date, "_id"])


def _render_fin_panel(fin_sem: pd.DataFrame, bank_row, sel_bnk_id: str) -> list:
    eligible = _eligible_ids(fin_sem, bank_row)
    eligible_df  = fin_sem[fin_sem["_id"].isin(eligible)].copy()
    other_df     = fin_sem[~fin_sem["_id"].isin(eligible)].copy()

    selected_ids: list = []
    display_cols = [c for c in ["_id", "_data", "_valor", "_historico"] if c in fin_sem.columns]

    def _render_fin_table(df: pd.DataFrame, key: str) -> list:
        tbl = df[display_cols].copy()
        tbl.insert(0, "Sel", False)
        tbl.columns = ["Sel", "ID", "Data", "Valor", "Histórico"][: len(tbl.columns)]
        res = st.data_editor(
            tbl,
            column_config={
                "Sel":   st.column_config.CheckboxColumn("✓", width="small"),
                "Valor": st.column_config.NumberColumn(format="R$ %.2f"),
            },
            hide_index=True,
            use_container_width=True,
            key=key,
            disabled=["ID", "Data", "Valor", "Histórico"],
        )
        return list(df.iloc[res["Sel"].values]["_id"])

    # Elegíveis (mesma data, mesmo sinal, valor ≤ banco)
    if not eligible_df.empty:
        st.markdown(
            f'<span class="mc-badge" style="background:{_GREEN}22;color:{_GREEN};'
            f'border:1px solid {_GREEN}44;">✅ Elegíveis — {len(eligible_df)}</span>',
            unsafe_allow_html=True,
        )
        selected_ids.extend(_render_fin_table(eligible_df, f"mc_fin_{sel_bnk_id}"))
    else:
        st.caption("Nenhum candidato elegível para esta data/valor.")

    # Outros disponíveis (fora do critério de elegibilidade)
    if not other_df.empty:
        label = f"Outros disponíveis — {len(other_df)}"
        expanded = eligible_df.empty  # abre automaticamente se não há elegíveis
        with st.expander(label, expanded=expanded):
            st.caption("Fora do critério de data, sinal ou valor.")
            selected_ids.extend(_render_fin_table(other_df, f"mc_fin_other_{sel_bnk_id}"))

    if fin_sem.empty:
        st.info("Nenhum lançamento financeiro livre disponível.")

    return selected_ids


def _apply_match(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    id_bnk: str,
    ids_fin: list,
    partial: bool,
) -> tuple:
    bnk_pos = dict(zip(df_bnk["_id"].astype(str), df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"].astype(str), df_fin.index))
    bi = bnk_pos.get(id_bnk)
    if bi is None:
        return df_bnk, df_fin

    bank_val = Decimal(str(df_bnk.at[bi, "_valor"]))
    fin_sum  = sum(Decimal(str(df_fin.at[fin_pos[f], "_valor"])) for f in ids_fin if f in fin_pos)
    status   = STATUS_PARCIAL if partial else STATUS_CONCILIADO_MANUAL
    metodo   = "manual:parcial" if partial else "manual"

    df_bnk.at[bi, "_status"]  = status
    df_bnk.at[bi, "_metodo"]  = metodo
    df_bnk.at[bi, "_ids_fin"] = ";".join(ids_fin)

    for id_f in ids_fin:
        fi = fin_pos.get(id_f)
        if fi is not None:
            df_fin.at[fi, "_status"] = status
            df_fin.at[fi, "_metodo"] = metodo
            df_fin.at[fi, "_id_bnk"] = id_bnk

    if partial:
        diff = bank_val - fin_sum
        pending: dict = {col: "" for col in df_bnk.columns}
        pending.update({
            "_id":       f"PND_{id_bnk}",
            "_data":     df_bnk.at[bi, "_data"],
            "_valor":    diff,
            "_historico": df_bnk.at[bi, "_historico"],
            "_status":   STATUS_PENDENTE_PARCIAL,
            "_metodo":   f"pendente:{id_bnk}",
            "_ids_fin":  "",
        })
        df_bnk = pd.concat([df_bnk, pd.DataFrame([pending])[df_bnk.columns]], ignore_index=True)

    return df_bnk, df_fin


def _run_process_5(df_bnk: pd.DataFrame, df_fin: pd.DataFrame, params: ConciliacaoParams) -> tuple:
    from core.match_partial_one_to_n import match_partial_one_to_n
    pnd_mask = df_bnk["_status"].astype(str) == STATUS_PENDENTE_PARCIAL
    df_bnk.loc[pnd_mask, "_status"] = STATUS_SEM_PAREAMENTO
    df_bnk.loc[pnd_mask, "_metodo"] = ""
    df_bnk, df_fin, pending_rows = match_partial_one_to_n(df_bnk, df_fin, params)
    if pending_rows:
        pend_df = pd.DataFrame(pending_rows)
        for col in df_bnk.columns:
            if col not in pend_df.columns:
                pend_df[col] = ""
        df_bnk = pd.concat([df_bnk, pend_df[df_bnk.columns]], ignore_index=True)
    return df_bnk, df_fin


def step_manual_conciliator(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> tuple:
    """
    Renderiza o conciliador manual.
    Retorna (df_bnk, df_fin, finished: bool).
    """
    _card_css()

    bnk_sem = df_bnk[df_bnk["_status"].astype(str) == STATUS_SEM_PAREAMENTO].copy()
    bnk_sem["_id"] = bnk_sem["_id"].astype(str)
    fin_sem = df_fin[df_fin["_status"].astype(str) == STATUS_IGNORADO_SEM_PAR].copy()
    fin_sem["_id"] = fin_sem["_id"].astype(str)

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    st.subheader("Conciliador Manual")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Banco sem par",    len(bnk_sem))
    m2.metric("Financeiro livre", len(fin_sem))
    m3.metric("Total banco",      fmt_valor(bnk_sem["_valor"].sum()) if not bnk_sem.empty else "R$ 0,00")
    m4.metric("Total financeiro", fmt_valor(fin_sem["_valor"].sum()) if not fin_sem.empty else "R$ 0,00")

    if bnk_sem.empty:
        st.success("Todos os lançamentos bancários foram conciliados!")
        st.divider()
        if st.button("Finalizar", type="primary", key="mc_fin_empty"):
            return df_bnk, df_fin, True
        return df_bnk, df_fin, False

    st.divider()

    # ── Títulos alinhados na mesma linha ──────────────────────────────────────
    th_bnk, th_fin = st.columns(2)
    th_bnk.markdown("**🏦 Extrato Bancário**")
    th_fin.markdown("**💼 Financeiro**")

    # ── Dois painéis com borda ────────────────────────────────────────────────
    sel_bnk_id: Optional[str] = st.session_state.get("manual_sel_bnk")

    col_bnk, col_fin = st.columns(2, gap="medium")

    with col_bnk:
        with st.container(border=True):
            sel_bnk_id = _render_bank_panel(bnk_sem)

    selected_fin_ids: list = []
    with col_fin:
        with st.container(border=True):
            if sel_bnk_id and sel_bnk_id in bnk_sem["_id"].values:
                bank_row = bnk_sem[bnk_sem["_id"] == sel_bnk_id].iloc[0]
                selected_fin_ids = _render_fin_panel(fin_sem, bank_row, sel_bnk_id)
            else:
                st.info("Selecione um lançamento bancário ao lado.")

    # ── Barra de ação ─────────────────────────────────────────────────────────
    if sel_bnk_id and sel_bnk_id in bnk_sem["_id"].values:
        bank_row  = bnk_sem[bnk_sem["_id"] == sel_bnk_id].iloc[0]
        bank_val  = Decimal(str(bank_row["_valor"]))
        fin_sum   = sum(
            Decimal(str(fin_sem.loc[fin_sem["_id"] == fid, "_valor"].iloc[0]))
            for fid in selected_fin_ids
            if fid in fin_sem["_id"].values
        )
        diff      = bank_val - fin_sum
        is_exact  = abs(diff) <= Decimal("0.01")
        st.divider()
        sa, sb, sc = st.columns(3)
        sa.metric("Bancário",    fmt_valor(bank_val))
        sb.metric("Selecionado", fmt_valor(fin_sum))
        sc.metric("Diferença",   fmt_valor(diff), delta_color="off")

        if selected_fin_ids:
            btn_a, btn_b, _ = st.columns([1, 1, 2])
            with btn_a:
                lbl_exact = "✅ Conciliar" if is_exact else "✅ Conciliar exato"
                if st.button(lbl_exact, key="mc_btn_exact", disabled=not is_exact, use_container_width=True, type="primary"):
                    df_bnk, df_fin = _apply_match(df_bnk, df_fin, sel_bnk_id, selected_fin_ids, partial=False)
                    st.session_state["df_bnk"] = df_bnk
                    st.session_state["df_fin"] = df_fin
                    st.session_state["manual_sel_bnk"] = None
                    st.rerun()
            with btn_b:
                lbl_partial = f"🔶 Parcial (R$ {abs(diff):,.2f} pendente)".replace(",", "X").replace(".", ",").replace("X", ".")
                can_partial = not is_exact and diff > 0 and fin_sum > 0
                if st.button(lbl_partial, key="mc_btn_partial", disabled=not can_partial, use_container_width=True):
                    df_bnk, df_fin = _apply_match(df_bnk, df_fin, sel_bnk_id, selected_fin_ids, partial=True)
                    st.session_state["df_bnk"] = df_bnk
                    st.session_state["df_fin"] = df_fin
                    st.session_state["manual_sel_bnk"] = None
                    st.rerun()
        else:
            st.caption("Selecione ao menos um lançamento financeiro para conciliar.")

    # ── Navegação final ────────────────────────────────────────────────────────
    st.divider()
    _, nav_b, nav_c = st.columns([3, 1, 1])
    with nav_b:
        if st.button("Finalizar sem parcial", use_container_width=True, key="mc_nav_skip"):
            st.session_state.pop("manual_sel_bnk", None)
            return df_bnk, df_fin, True
    with nav_c:
        has_pending = (df_bnk["_status"].astype(str) == STATUS_PENDENTE_PARCIAL).any()
        btn_label = "▶ Seguir conciliação" if has_pending else "▶ Finalizar"
        if st.button(btn_label, type="primary", use_container_width=True, key="mc_nav_next"):
            if has_pending:
                with st.spinner("Rodando Processo 5..."):
                    df_bnk, df_fin = _run_process_5(df_bnk, df_fin, params)
            st.session_state.pop("manual_sel_bnk", None)
            return df_bnk, df_fin, True

    return df_bnk, df_fin, False
