"""
Conciliador manual - Processo 4.
Tela de trabalho para selecionar lançamentos bancários e financeiros pendentes.
"""
from __future__ import annotations

from html import escape
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Optional

import pandas as pd
import streamlit as st

from core.normalize import (
    STATUS_CONCILIADO_MANUAL,
    STATUS_IGNORADO_SEM_PAR,
    STATUS_IGNORADO_USUARIO,
    STATUS_PARCIAL,
    STATUS_PENDENTE_PARCIAL,
    STATUS_SEM_PAREAMENTO,
)
from core.params import ConciliacaoParams
from ui.components import fmt_data, fmt_valor

_PANEL_HEIGHT = 560


def _manual_css() -> None:
    st.markdown(
        """
        <style>
        .manual-workbar{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:4px 0 14px}
        .manual-kpi,.manual-selection{border:1px solid rgba(120,120,120,.18);background:var(--secondary-background-color);border-radius:8px;padding:12px 14px}
        .manual-kpi .label,.manual-selection .label{color:rgba(120,120,120,.95);font-size:.74rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
        .manual-kpi .value{color:var(--text-color);font-size:1.08rem;font-weight:750;white-space:nowrap}
        .manual-selection{margin:0 0 14px}
        .manual-selection-grid{display:grid;grid-template-columns:1.2fr 1.2fr 1fr;gap:10px;align-items:stretch}
        .manual-selection .amount{font-size:1.18rem;font-weight:800;color:var(--text-color)}
        .manual-selection .hint{font-size:.78rem;color:rgba(120,120,120,.95);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .manual-diff-ok{color:#168A3A!important}.manual-diff-warn{color:#B26A00!important}.manual-diff-bad{color:#C62828!important}
        @media (max-width:900px){.manual-workbar,.manual-selection-grid{grid-template-columns:1fr}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _tolerance(params: ConciliacaoParams) -> Decimal:
    cents = int(getattr(params, "value_tolerance_cents", 0) or 0)
    return Decimal(cents) / Decimal("100")


def _text_similarity(a: str, b: str) -> int:
    a = " ".join(str(a or "").lower().split())
    b = " ".join(str(b or "").lower().split())
    if not a or not b:
        return 0
    return int(round(SequenceMatcher(None, a, b).ratio() * 100))


def _date_delta_days(a, b) -> Optional[int]:
    try:
        return abs((pd.to_datetime(a).date() - pd.to_datetime(b).date()).days)
    except Exception:
        return None


def _score_fin_candidate(bank_row, fin_row, params: ConciliacaoParams) -> tuple[int, list[str], Decimal]:
    bank_val = abs(_to_decimal(bank_row.get("_valor")))
    fin_val = abs(_to_decimal(fin_row.get("_valor")))
    diff_abs = abs(bank_val - fin_val)
    tol = _tolerance(params)
    days = _date_delta_days(bank_row.get("_data"), fin_row.get("_data"))
    hist_score = _text_similarity(bank_row.get("_historico", ""), fin_row.get("_historico", ""))

    score = 0
    reasons: list[str] = []
    if diff_abs <= Decimal("0.01"):
        score += 35
        reasons.append("valor igual")
    elif diff_abs <= tol:
        score += 28
        reasons.append("tolerância")
    elif bank_val and diff_abs / bank_val <= Decimal("0.02"):
        score += 18
        reasons.append("valor próximo")

    if days == 0:
        score += 60
        reasons.append("mesmo dia")
    elif days is not None and days <= 2:
        score += 42
        reasons.append(f"D{days}")
    elif days is not None and days <= 5:
        score += 25
        reasons.append(f"D{days}")

    if hist_score >= 70:
        score += 5
        reasons.append("histórico parecido")
    elif hist_score >= 45:
        score += 3
        reasons.append("histórico próximo")

    diff_signed = _to_decimal(bank_row.get("_valor")) - _to_decimal(fin_row.get("_valor"))
    return min(score, 100), reasons, diff_signed


def _selected_rows(df: pd.DataFrame, ids: list[str]) -> pd.DataFrame:
    if not ids or df.empty:
        return df.iloc[0:0].copy()
    ids_norm = {str(i) for i in ids}
    return df[df["_id"].astype(str).isin(ids_norm)].copy()


def _selection_summary(bank_row, fin_rows: pd.DataFrame, params: ConciliacaoParams) -> tuple[Decimal, Decimal, Decimal, str]:
    bank_val = _to_decimal(bank_row.get("_valor")) if bank_row is not None else Decimal("0")
    fin_sum = sum((_to_decimal(v) for v in fin_rows["_valor"].tolist()), Decimal("0"))
    diff = bank_val - fin_sum
    tol = _tolerance(params)
    if abs(diff) <= Decimal("0.01"):
        klass = "manual-diff-ok"
    elif abs(diff) <= tol:
        klass = "manual-diff-warn"
    else:
        klass = "manual-diff-bad"
    return bank_val, fin_sum, diff, klass


def _render_workbar(bnk_sem: pd.DataFrame, fin_sem: pd.DataFrame) -> None:
    st.markdown(
        f"""
        <div class="manual-workbar">
            <div class="manual-kpi"><div class="label">Banco pendente</div><div class="value">{len(bnk_sem)}</div></div>
            <div class="manual-kpi"><div class="label">Financeiro livre</div><div class="value">{len(fin_sem)}</div></div>
            <div class="manual-kpi"><div class="label">Total banco</div><div class="value">{fmt_valor(bnk_sem["_valor"].sum()) if not bnk_sem.empty else "R$ 0,00"}</div></div>
            <div class="manual-kpi"><div class="label">Total financeiro</div><div class="value">{fmt_valor(fin_sem["_valor"].sum()) if not fin_sem.empty else "R$ 0,00"}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_selection_bar(bank_row, fin_rows: pd.DataFrame, params: ConciliacaoParams) -> tuple[Decimal, Decimal, Decimal, bool]:
    bank_val, fin_sum, diff, klass = _selection_summary(bank_row, fin_rows, params)
    bank_hist = escape(str(bank_row.get("_historico", ""))[:96]) if bank_row is not None else "Selecione um lançamento bancário"
    fin_hint = "Nenhum lançamento financeiro selecionado"
    if not fin_rows.empty:
        fin_hint = " | ".join(escape(str(v)[:34]) for v in fin_rows["_historico"].head(3).tolist())
        if len(fin_rows) > 3:
            fin_hint += f" +{len(fin_rows) - 3}"
    is_exact = abs(diff) <= Decimal("0.01")

    st.markdown(
        f"""
        <div class="manual-selection">
            <div class="manual-selection-grid">
                <div>
                    <div class="label">Banco selecionado</div>
                    <div class="amount">{fmt_valor(bank_val)}</div>
                    <div class="hint">{bank_hist}</div>
                </div>
                <div>
                    <div class="label">Financeiro selecionado</div>
                    <div class="amount">{fmt_valor(fin_sum)}</div>
                    <div class="hint">{fin_hint}</div>
                </div>
                <div>
                    <div class="label">Diferença</div>
                    <div class="amount {klass}">{fmt_valor(diff)}</div>
                    <div class="hint">{"fecha exatamente" if is_exact else "fora do fechamento exato"}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return bank_val, fin_sum, diff, is_exact


def _render_bank_panel(bnk_sem: pd.DataFrame) -> list[str]:
    nonce = st.session_state.get("manual_selection_nonce", 0)
    termo = st.text_input("Buscar no banco", key="mc_busca_banco", placeholder="Data, valor ou histórico")
    if termo.strip():
        t = termo.strip().lower()
        mask = (
            bnk_sem["_historico"].astype(str).str.lower().str.contains(t, na=False)
            | bnk_sem["_data"].astype(str).str.lower().str.contains(t, na=False)
            | bnk_sem["_valor"].astype(str).str.lower().str.contains(t, na=False)
        )
        bnk_sem = bnk_sem[mask].copy()

    df_disp = pd.DataFrame({
        "Tipo": ["Entrada" if _to_decimal(r["_valor"]) >= 0 else "Saída" for _, r in bnk_sem.iterrows()],
        "Valor": [fmt_valor(r["_valor"]) for _, r in bnk_sem.iterrows()],
        "Data": [fmt_data(r["_data"]) for _, r in bnk_sem.iterrows()],
        "Histórico": [str(r.get("_historico", ""))[:72] for _, r in bnk_sem.iterrows()],
        "_id": bnk_sem["_id"].astype(str).tolist(),
    })

    event = st.dataframe(
        df_disp,
        column_config={
            "Tipo": st.column_config.TextColumn("Tipo", width="small"),
            "Valor": st.column_config.TextColumn("Valor", width="medium"),
            "Data": st.column_config.TextColumn("Data", width="small"),
            "Histórico": st.column_config.TextColumn("Histórico"),
            "_id": None,
        },
        selection_mode="multi-row",
        on_select="rerun",
        hide_index=True,
        use_container_width=True,
        height=_PANEL_HEIGHT,
        key=f"bnk_df_{nonce}",
    )

    sel_rows = event.selection.rows
    sel_ids = df_disp.iloc[sel_rows]["_id"].tolist() if sel_rows and not df_disp.empty else []
    st.session_state["manual_sel_bnk"] = sel_ids[0] if len(sel_ids) == 1 else None
    st.session_state["manual_sel_bnk_ids"] = sel_ids
    return sel_ids


def _render_fin_panel(
    fin_sem: pd.DataFrame,
    bank_row,
    sel_bnk_id: Optional[str],
    params: ConciliacaoParams,
) -> list[str]:
    key_prefix = sel_bnk_id or "nosel"
    nonce = st.session_state.get("manual_selection_nonce", 0)
    if fin_sem.empty:
        st.info("Nenhum lançamento financeiro livre disponível.")
        return []
    if bank_row is None:
        st.caption("Selecione um lançamento bancário para ordenar por afinidade.")

    filtro = st.radio(
        "Filtro financeiro",
        ["Todos", "Mesmo valor", "Mesmo dia", "Tolerância", "Histórico parecido"],
        horizontal=True,
        key=f"mc_fin_filtro_{key_prefix}",
    )
    termo = st.text_input("Buscar no financeiro", key=f"mc_busca_fin_{key_prefix}", placeholder="Data, valor ou histórico")

    rows = []
    for _, r in fin_sem.iterrows():
        rid = str(r["_id"])
        if bank_row is not None:
            score, reasons, diff = _score_fin_candidate(bank_row, r, params)
            day_delta = _date_delta_days(bank_row.get("_data"), r.get("_data"))
        else:
            score, reasons, diff = 0, [], Decimal("0")
            day_delta = None
        rows.append({
            "Afinidade": score,
            "Valor": fmt_valor(r["_valor"]),
            "Data": fmt_data(r["_data"]),
            "Historico": str(r.get("_historico", ""))[:72],
            "Diferenca": fmt_valor(diff) if bank_row is not None else "",
            "Sinais": ", ".join(reasons[:3]),
            "_id": rid,
            "_score": score,
            "_day_delta": 9999 if day_delta is None else int(day_delta),
            "_diff_abs": abs(diff),
            "_reasons": reasons,
        })

    df_disp = pd.DataFrame(rows)
    if termo.strip():
        t = termo.strip().lower()
        mask = (
            df_disp["Historico"].astype(str).str.lower().str.contains(t, na=False)
            | df_disp["Data"].astype(str).str.lower().str.contains(t, na=False)
            | df_disp["Valor"].astype(str).str.lower().str.contains(t, na=False)
        )
        df_disp = df_disp[mask].copy()

    if bank_row is not None:
        if filtro == "Mesmo valor":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "valor igual" in rs)].copy()
        elif filtro == "Mesmo dia":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "mesmo dia" in rs)].copy()
        elif filtro == "Tolerância":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "tolerância" in rs or "valor igual" in rs)].copy()
        elif filtro == "Histórico parecido":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: any("histórico" in r for r in rs))].copy()

    df_disp = df_disp.sort_values(["_day_delta", "_diff_abs", "_score", "Data"], ascending=[True, True, False, True]).drop(columns=["_score", "_day_delta", "_diff_abs", "_reasons"])
    df_disp = df_disp[["Afinidade", "Valor", "Data", "Historico", "Diferenca", "Sinais", "_id"]]

    event = st.dataframe(
        df_disp,
        column_config={
            "Afinidade": st.column_config.ProgressColumn("Afinidade", min_value=0, max_value=100, width="small"),
            "Valor": st.column_config.TextColumn("Valor", width="medium"),
            "Data": st.column_config.TextColumn("Data", width="small"),
            "Historico": st.column_config.TextColumn("Histórico"),
            "Diferenca": st.column_config.TextColumn("Diferença", width="medium"),
            "Sinais": st.column_config.TextColumn("Sinais", width="medium"),
            "_id": None,
        },
        selection_mode="multi-row",
        on_select="rerun",
        hide_index=True,
        use_container_width=True,
        height=_PANEL_HEIGHT,
        key=f"fin_df_{key_prefix}_{nonce}",
    )

    sel_rows = event.selection.rows
    return df_disp.iloc[sel_rows]["_id"].tolist() if sel_rows and not df_disp.empty else []


def _apply_match(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    id_bnk: str,
    ids_fin: list[str],
    partial: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bnk_pos = dict(zip(df_bnk["_id"].astype(str), df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"].astype(str), df_fin.index))
    bi = bnk_pos.get(id_bnk)
    if bi is None:
        return df_bnk, df_fin

    bank_val = _to_decimal(df_bnk.at[bi, "_valor"])
    fin_sum = sum((_to_decimal(df_fin.at[fin_pos[f], "_valor"]) for f in ids_fin if f in fin_pos), Decimal("0"))
    status = STATUS_PARCIAL if partial else STATUS_CONCILIADO_MANUAL
    metodo = "manual:parcial" if partial else "manual"

    df_bnk.at[bi, "_status"] = status
    df_bnk.at[bi, "_metodo"] = metodo
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
            "_id": f"PND_{id_bnk}",
            "_data": df_bnk.at[bi, "_data"],
            "_valor": float(diff),
            "_historico": df_bnk.at[bi, "_historico"],
            "_status": STATUS_PENDENTE_PARCIAL,
            "_metodo": f"pendente:{id_bnk}",
            "_ids_fin": "",
        })
        df_bnk = pd.concat([df_bnk, pd.DataFrame([pending])[df_bnk.columns]], ignore_index=True)

    return df_bnk, df_fin


def _ignore_bank(df_bnk: pd.DataFrame, ids_bnk: str | list[str]) -> pd.DataFrame:
    if isinstance(ids_bnk, str):
        ids = [ids_bnk]
    else:
        ids = [str(i) for i in ids_bnk]
    bnk_pos = dict(zip(df_bnk["_id"].astype(str), df_bnk.index))
    for id_bnk in ids:
        bi = bnk_pos.get(id_bnk)
        if bi is not None:
            df_bnk.at[bi, "_status"] = STATUS_IGNORADO_USUARIO
            df_bnk.at[bi, "_metodo"] = "manual:ignorado"
    return df_bnk


def _apply_n_to_1_match(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    ids_bnk: list[str],
    id_fin: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bnk_pos = dict(zip(df_bnk["_id"].astype(str), df_bnk.index))
    fin_pos = dict(zip(df_fin["_id"].astype(str), df_fin.index))
    fi = fin_pos.get(id_fin)
    if fi is None:
        return df_bnk, df_fin
    df_fin.at[fi, "_status"] = STATUS_CONCILIADO_MANUAL
    df_fin.at[fi, "_metodo"] = "manual"
    df_fin.at[fi, "_id_bnk"] = ";".join(ids_bnk)
    for id_b in ids_bnk:
        bi = bnk_pos.get(id_b)
        if bi is not None:
            df_bnk.at[bi, "_status"] = STATUS_CONCILIADO_MANUAL
            df_bnk.at[bi, "_metodo"] = "manual"
            df_bnk.at[bi, "_ids_fin"] = id_fin
    return df_bnk, df_fin


def _ignore_financeiro(df_fin: pd.DataFrame, ids_fin: list[str]) -> pd.DataFrame:
    fin_pos = dict(zip(df_fin["_id"].astype(str), df_fin.index))
    for id_f in ids_fin:
        fi = fin_pos.get(id_f)
        if fi is not None:
            df_fin.at[fi, "_status"] = STATUS_IGNORADO_USUARIO
            df_fin.at[fi, "_metodo"] = "manual:ignorado"
    return df_fin


def _clear_selection(sel_bnk_id: Optional[str]) -> None:
    st.session_state.pop("manual_sel_bnk", None)
    st.session_state.pop("manual_sel_bnk_ids", None)
    st.session_state["manual_selection_nonce"] = st.session_state.get("manual_selection_nonce", 0) + 1


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
    _manual_css()
    bnk_sem = df_bnk[df_bnk["_status"].astype(str) == STATUS_SEM_PAREAMENTO].copy()
    bnk_sem["_id"] = bnk_sem["_id"].astype(str)
    fin_sem = df_fin[df_fin["_status"].astype(str) == STATUS_IGNORADO_SEM_PAR].copy()
    fin_sem["_id"] = fin_sem["_id"].astype(str)

    st.subheader("Conciliador Manual")
    _render_workbar(bnk_sem, fin_sem)

    if bnk_sem.empty:
        st.success("Todos os lançamentos bancários foram conciliados.")
        st.divider()
        if st.button("Finalizar", type="primary", key="mc_fin_empty"):
            return df_bnk, df_fin, True
        return df_bnk, df_fin, False

    col_bnk, col_fin = st.columns(2, gap="medium")
    with col_bnk:
        st.markdown("**Extrato bancário**")
        selected_bnk_ids = _render_bank_panel(bnk_sem)
        sel_bnk_id = selected_bnk_ids[0] if len(selected_bnk_ids) == 1 else None

    bank_row = None
    if sel_bnk_id and sel_bnk_id in bnk_sem["_id"].values:
        bank_row = bnk_sem[bnk_sem["_id"] == sel_bnk_id].iloc[0]
    elif len(selected_bnk_ids) > 1:
        st.info(f"{len(selected_bnk_ids)} lançamentos bancários selecionados para ação em bloco.")

    with col_fin:
        st.markdown("**Financeiro**")
        selected_fin_ids = _render_fin_panel(fin_sem, bank_row, sel_bnk_id, params)

    fin_selected = _selected_rows(fin_sem, selected_fin_ids)

    if bank_row is not None:
        bank_val, fin_sum, diff, is_exact = _render_selection_bar(bank_row, fin_selected, params)
        # Parcial válido quando financeiro cobre menos que o banco (mesmo sinal, não exato)
        can_partial = (
            bool(selected_fin_ids)
            and not is_exact
            and fin_sum != 0
            and abs(fin_sum) < abs(bank_val)
            and (bank_val * fin_sum > 0)  # mesmo sinal (ambos crédito ou ambos débito)
        )

        btn_exact, btn_partial, btn_ignore_bnk, btn_ignore_fin, btn_clear = st.columns([1.2, 1.3, 1.1, 1.2, 1])
        with btn_exact:
            if st.button("Conciliar selecionados", key="mc_btn_exact", disabled=not (selected_fin_ids and is_exact), use_container_width=True, type="primary"):
                df_bnk, df_fin = _apply_match(df_bnk, df_fin, sel_bnk_id, selected_fin_ids, partial=False)
                st.session_state["df_bnk"] = df_bnk
                st.session_state["df_fin"] = df_fin
                _clear_selection(sel_bnk_id)
                st.rerun()
        with btn_partial:
            if st.button("Conciliar parcial", key="mc_btn_partial", disabled=not can_partial, use_container_width=True):
                df_bnk, df_fin = _apply_match(df_bnk, df_fin, sel_bnk_id, selected_fin_ids, partial=True)
                st.session_state["df_bnk"] = df_bnk
                st.session_state["df_fin"] = df_fin
                _clear_selection(sel_bnk_id)
                st.rerun()
        with btn_ignore_bnk:
            if st.button("Ignorar banco", key="mc_btn_ignore_bnk", use_container_width=True):
                df_bnk = _ignore_bank(df_bnk, selected_bnk_ids)
                st.session_state["df_bnk"] = df_bnk
                _clear_selection(sel_bnk_id)
                st.rerun()
        with btn_ignore_fin:
            if st.button("Ignorar financeiro", key="mc_btn_ignore_fin", disabled=not selected_fin_ids, use_container_width=True):
                df_fin = _ignore_financeiro(df_fin, selected_fin_ids)
                st.session_state["df_fin"] = df_fin
                _clear_selection(sel_bnk_id)
                st.rerun()
        with btn_clear:
            if st.button("Limpar seleção", key="mc_btn_clear", use_container_width=True):
                _clear_selection(sel_bnk_id)
                st.rerun()
    elif len(selected_bnk_ids) > 1:
        # N banco → 1 financeiro: exibe resumo e botão de conciliação N:1
        if selected_fin_ids:
            fin_n1 = _selected_rows(fin_sem, selected_fin_ids)
            bnk_n1 = _selected_rows(bnk_sem, selected_bnk_ids)
            bnk_sum = sum((_to_decimal(v) for v in bnk_n1["_valor"].tolist()), Decimal("0"))
            fin_val = _to_decimal(fin_n1.iloc[0]["_valor"]) if len(fin_n1) == 1 else Decimal("0")
            diff_n1 = bnk_sum - fin_val
            diff_klass = "manual-diff-ok" if abs(diff_n1) <= Decimal("0.01") else "manual-diff-warn" if abs(diff_n1) <= _tolerance(params) else "manual-diff-bad"
            st.markdown(
                f"""<div class="manual-selection"><div class="manual-selection-grid">
                <div><div class="label">Soma bancários ({len(selected_bnk_ids)})</div><div class="amount">{fmt_valor(bnk_sum)}</div></div>
                <div><div class="label">Financeiro selecionado</div><div class="amount">{fmt_valor(fin_val) if len(fin_n1)==1 else "—"}</div></div>
                <div><div class="label">Diferença</div><div class="amount {diff_klass}">{fmt_valor(diff_n1) if len(fin_n1)==1 else "—"}</div></div>
                </div></div>""",
                unsafe_allow_html=True,
            )

        can_n1 = len(selected_fin_ids) == 1
        n1_col, bulk_col, clear_col = st.columns([1.4, 1.5, 1])
        with n1_col:
            if st.button(
                f"Conciliar {len(selected_bnk_ids)}:1",
                key="mc_btn_n1",
                disabled=not can_n1,
                use_container_width=True,
                type="primary",
                help="Selecione exatamente 1 lançamento financeiro para vincular aos bancos selecionados.",
            ):
                df_bnk, df_fin = _apply_n_to_1_match(df_bnk, df_fin, selected_bnk_ids, selected_fin_ids[0])
                st.session_state["df_bnk"] = df_bnk
                st.session_state["df_fin"] = df_fin
                _clear_selection(None)
                st.rerun()
        with bulk_col:
            if st.button(f"Ignorar {len(selected_bnk_ids)} bancos", key="mc_btn_ignore_bnk_bulk", use_container_width=True):
                df_bnk = _ignore_bank(df_bnk, selected_bnk_ids)
                st.session_state["df_bnk"] = df_bnk
                _clear_selection(None)
                st.rerun()
        with clear_col:
            if st.button("Limpar seleção", key="mc_btn_clear_bulk", use_container_width=True):
                _clear_selection(None)
                st.rerun()
    else:
        st.info("Selecione uma linha do banco para ver sugestões, diferença e ações.")

    st.divider()
    _, nav_b, nav_c = st.columns([3, 1, 1])
    with nav_b:
        if st.button("Finalizar sem parcial", use_container_width=True, key="mc_nav_skip"):
            _clear_selection(st.session_state.get("manual_sel_bnk"))
            return df_bnk, df_fin, True
    with nav_c:
        has_pending = (df_bnk["_status"].astype(str) == STATUS_PENDENTE_PARCIAL).any()
        btn_label = "Seguir conciliação" if has_pending else "Finalizar"
        if st.button(btn_label, type="primary", use_container_width=True, key="mc_nav_next"):
            if has_pending:
                with st.spinner("Rodando Processo 5..."):
                    df_bnk, df_fin = _run_process_5(df_bnk, df_fin, params)
            _clear_selection(st.session_state.get("manual_sel_bnk"))
            return df_bnk, df_fin, True

    return df_bnk, df_fin, False
