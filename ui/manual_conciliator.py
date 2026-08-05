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
        .manual-selection{margin:0 0 14px;position:sticky;top:.5rem;z-index:999;box-shadow:0 10px 24px rgba(15,23,42,.10)}
        .manual-selection-grid{display:grid;grid-template-columns:1.2fr 1.2fr 1fr;gap:10px;align-items:stretch}
        .manual-selection .amount{font-size:1.18rem;font-weight:800;color:var(--text-color);line-height:1.5}
        .manual-selection .hint{font-size:.78rem;line-height:1.4;min-height:1.4em;color:rgba(120,120,120,.95);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .manual-diff-idle{color:rgba(120,120,120,.95)!important}
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


def _date_sort_key(value) -> pd.Timestamp:
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return pd.Timestamp.max
    return parsed.normalize()


def _score_fin_candidate(
    bank_row,
    fin_row,
    params: ConciliacaoParams,
    selected_fin_sum: Decimal = Decimal("0"),
    candidate_selected: bool = False,
) -> tuple[int, list[str], Decimal]:
    bank_raw = _to_decimal(bank_row.get("_valor"))
    fin_raw = _to_decimal(fin_row.get("_valor"))
    remaining = bank_raw - selected_fin_sum
    remaining_abs = abs(remaining)
    fin_val = abs(fin_raw)
    diff_abs = abs(remaining_abs - fin_val)
    tol = _tolerance(params)
    days = _date_delta_days(bank_row.get("_data"), fin_row.get("_data"))
    hist_score = _text_similarity(bank_row.get("_historico", ""), fin_row.get("_historico", ""))
    diff_signed = remaining - fin_raw

    if candidate_selected:
        return 100, ["selecionado"], diff_signed

    if remaining_abs <= Decimal("0.01"):
        return 0, ["seleção fechada"], diff_signed

    if bank_raw and fin_raw and bank_raw * fin_raw < 0:
        return 0, ["sinal diferente"], diff_signed

    if fin_val > remaining_abs + tol:
        return 0, ["acima do restante"], diff_signed

    score = 0
    reasons: list[str] = []
    if diff_abs <= Decimal("0.01"):
        score += 74
        reasons.append("fecha restante" if selected_fin_sum else "valor igual")
    elif diff_abs <= tol:
        score += 68
        reasons.append("tolerância")
    elif remaining_abs:
        closeness = Decimal("1") - min(diff_abs / remaining_abs, Decimal("1"))
        score += int(round(float(closeness * Decimal("62"))))
        if closeness >= Decimal("0.85"):
            reasons.append("valor muito próximo")
        elif closeness >= Decimal("0.55"):
            reasons.append("valor próximo")
        elif closeness >= Decimal("0.25"):
            reasons.append("valor menor")

    if days == 0:
        score += 20
        reasons.append("mesmo dia")
    elif days is not None and days <= 2:
        score += 12
        reasons.append(f"D{days}")
    elif days is not None and days <= 5:
        score += 6
        reasons.append(f"D{days}")

    if hist_score >= 70:
        score += 5
        reasons.append("histórico parecido")
    elif hist_score >= 45:
        score += 3
        reasons.append("histórico próximo")

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
    if bank_row is None and fin_rows.empty:
        klass = "manual-diff-idle"
    elif abs(diff) <= Decimal("0.01"):
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
    if bank_row is None and fin_rows.empty:
        diff_hint = "aguardando seleção"
    else:
        diff_hint = "fecha exatamente" if is_exact else "fora do fechamento exato"

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
                    <div class="hint">{diff_hint}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return bank_val, fin_sum, diff, is_exact


def _render_n_to_1_bar(bnk_rows: pd.DataFrame, fin_rows: pd.DataFrame, params: ConciliacaoParams) -> None:
    bnk_sum = sum((_to_decimal(v) for v in bnk_rows["_valor"].tolist()), Decimal("0"))
    single_fin = len(fin_rows) == 1
    fin_val = _to_decimal(fin_rows.iloc[0]["_valor"]) if single_fin else Decimal("0")
    diff = bnk_sum - fin_val

    if not single_fin:
        klass = "manual-diff-idle"
    elif abs(diff) <= Decimal("0.01"):
        klass = "manual-diff-ok"
    elif abs(diff) <= _tolerance(params):
        klass = "manual-diff-warn"
    else:
        klass = "manual-diff-bad"

    if fin_rows.empty:
        fin_hint = "Nenhum lançamento financeiro selecionado"
        diff_hint = "aguardando seleção"
    elif single_fin:
        fin_hint = escape(str(fin_rows.iloc[0].get("_historico", ""))[:96])
        diff_hint = "fecha exatamente" if abs(diff) <= Decimal("0.01") else "fora do fechamento exato"
    else:
        fin_hint = f"{len(fin_rows)} selecionados - escolha apenas 1"
        diff_hint = "seleção inválida para N:1"

    st.markdown(
        f"""
        <div class="manual-selection">
            <div class="manual-selection-grid">
                <div>
                    <div class="label">Soma bancários ({len(bnk_rows)})</div>
                    <div class="amount">{fmt_valor(bnk_sum)}</div>
                    <div class="hint">{len(bnk_rows)} lançamentos bancários selecionados</div>
                </div>
                <div>
                    <div class="label">Financeiro selecionado</div>
                    <div class="amount">{fmt_valor(fin_val) if single_fin else "—"}</div>
                    <div class="hint">{fin_hint}</div>
                </div>
                <div>
                    <div class="label">Diferença</div>
                    <div class="amount {klass}">{fmt_valor(diff) if single_fin else "—"}</div>
                    <div class="hint">{diff_hint}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def _fin_candidate_rows(
    fin_sem: pd.DataFrame,
    bank_row,
    sel_bnk_id: Optional[str],
    params: ConciliacaoParams,
    selected_fin_ids: Optional[list[str]] = None,
) -> list[dict]:
    """
    Linhas do painel financeiro com afinidade calculada.

    O resultado depende do banco selecionado, dos dados pendentes e da seleção
    financeira atual, porque a afinidade usa o valor restante.
    """
    selected_fin_ids = selected_fin_ids or []
    selected_fin_key = tuple(sorted(str(i) for i in selected_fin_ids))
    cache_key = (st.session_state.get("manual_selection_nonce", 0), sel_bnk_id, len(fin_sem), selected_fin_key)
    cached = st.session_state.get("_mc_fin_rows_cache")
    if cached is not None and cached[0] == cache_key:
        return cached[1]

    selected_fin_sum = sum(
        (_to_decimal(v) for v in _selected_rows(fin_sem, selected_fin_ids)["_valor"].tolist()),
        Decimal("0"),
    )
    selected_fin_set = {str(i) for i in selected_fin_ids}

    rows = []
    for _, r in fin_sem.iterrows():
        if bank_row is not None:
            row_id = str(r["_id"])
            base_score, _, base_diff = _score_fin_candidate(bank_row, r, params)
            score, reasons, diff = _score_fin_candidate(
                bank_row,
                r,
                params,
                selected_fin_sum=selected_fin_sum,
                candidate_selected=row_id in selected_fin_set,
            )
            day_delta = _date_delta_days(bank_row.get("_data"), r.get("_data"))
        else:
            score, reasons, diff = 0, [], Decimal("0")
            base_score, base_diff = 0, Decimal("0")
            day_delta = None
        rows.append({
            "Afinidade": score,
            "Valor": fmt_valor(r["_valor"]),
            "Data": fmt_data(r["_data"]),
            "Historico": str(r.get("_historico", ""))[:72],
            "Diferenca": fmt_valor(diff) if bank_row is not None else "",
            "Sinais": ", ".join(reasons[:3]),
            "_id": str(r["_id"]),
            "_score": score,
            "_sort_score": base_score,
            "_day_delta": 9999 if day_delta is None else int(day_delta),
            "_diff_abs": abs(diff),
            "_sort_diff_abs": abs(base_diff),
            "_date_key": _date_sort_key(r["_data"]),
            "_reasons": reasons,
        })

    st.session_state["_mc_fin_rows_cache"] = (cache_key, rows)
    return rows


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

    active_fin_ids = []
    if st.session_state.get("manual_sel_fin_bnk_id") == sel_bnk_id:
        active_fin_ids = [str(i) for i in st.session_state.get("manual_sel_fin_ids", [])]

    df_disp = pd.DataFrame(_fin_candidate_rows(fin_sem, bank_row, sel_bnk_id, params, active_fin_ids))
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
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "valor igual" in rs or "fecha restante" in rs)].copy()
        elif filtro == "Mesmo dia":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "mesmo dia" in rs)].copy()
        elif filtro == "Tolerância":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: "tolerância" in rs or "valor igual" in rs or "fecha restante" in rs)].copy()
        elif filtro == "Histórico parecido":
            df_disp = df_disp[df_disp["_reasons"].apply(lambda rs: any("histórico" in r for r in rs))].copy()

    if bank_row is not None:
        df_disp = df_disp.sort_values(
            ["_sort_score", "_sort_diff_abs", "_day_delta", "_date_key"],
            ascending=[False, True, True, True],
        )
    else:
        df_disp = df_disp.sort_values(["_date_key", "Valor"], ascending=[True, True])
    df_disp = df_disp.drop(columns=["_score", "_sort_score", "_day_delta", "_diff_abs", "_sort_diff_abs", "_date_key", "_reasons"])
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
    sel_ids = df_disp.iloc[sel_rows]["_id"].tolist() if sel_rows and not df_disp.empty else []
    st.session_state["manual_sel_fin_ids"] = sel_ids
    st.session_state["manual_sel_fin_bnk_id"] = sel_bnk_id
    return sel_ids


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
    st.session_state.pop("manual_sel_fin_ids", None)
    st.session_state.pop("manual_sel_fin_bnk_id", None)
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


def _finish(df_bnk: pd.DataFrame, df_fin: pd.DataFrame) -> None:
    """Encerra a etapa: grava os dados e devolve o controle ao script principal."""
    st.session_state["df_bnk"] = df_bnk
    st.session_state["df_fin"] = df_fin
    st.session_state["manual_finished"] = True
    st.rerun(scope="app")


@st.fragment
def _manual_workspace(params: ConciliacaoParams) -> None:
    """
    Área de trabalho do conciliador manual.

    É um fragmento: selecionar linhas nas tabelas reexecuta apenas esta função,
    não o script inteiro. Os DataFrames trafegam via session_state porque um
    fragmento não devolve valores ao chamador.
    """
    df_bnk = st.session_state["df_bnk"]
    df_fin = st.session_state["df_fin"]

    bnk_sem = df_bnk[df_bnk["_status"].astype(str) == STATUS_SEM_PAREAMENTO].copy()
    bnk_sem["_id"] = bnk_sem["_id"].astype(str)
    fin_sem = df_fin[df_fin["_status"].astype(str) == STATUS_IGNORADO_SEM_PAR].copy()
    fin_sem["_id"] = fin_sem["_id"].astype(str)

    _render_workbar(bnk_sem, fin_sem)

    if bnk_sem.empty:
        st.success("Todos os lançamentos bancários foram conciliados.")
        st.divider()
        if st.button("Finalizar", type="primary", key="mc_fin_empty"):
            _finish(df_bnk, df_fin)
        return

    # Reservado antes das listas: preenchido depois, quando a seleção é conhecida.
    summary_slot = st.container()

    col_bnk, col_fin = st.columns(2, gap="medium")
    with col_bnk:
        st.markdown("**Extrato bancário**")
        selected_bnk_ids = _render_bank_panel(bnk_sem)
        sel_bnk_id = selected_bnk_ids[0] if len(selected_bnk_ids) == 1 else None

    bank_row = None
    if sel_bnk_id and sel_bnk_id in bnk_sem["_id"].values:
        bank_row = bnk_sem[bnk_sem["_id"] == sel_bnk_id].iloc[0]

    with col_fin:
        st.markdown("**Financeiro**")
        selected_fin_ids = _render_fin_panel(fin_sem, bank_row, sel_bnk_id, params)

    fin_selected = _selected_rows(fin_sem, selected_fin_ids)

    # O resumo é sempre renderizado, zerado se não houver seleção, para a página não saltar.
    with summary_slot:
        if len(selected_bnk_ids) > 1:
            _render_n_to_1_bar(_selected_rows(bnk_sem, selected_bnk_ids), fin_selected, params)
        else:
            bank_val, fin_sum, diff, is_exact = _render_selection_bar(bank_row, fin_selected, params)

    if bank_row is not None:
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
                st.rerun(scope="fragment")
        with btn_partial:
            if st.button("Conciliar parcial", key="mc_btn_partial", disabled=not can_partial, use_container_width=True):
                df_bnk, df_fin = _apply_match(df_bnk, df_fin, sel_bnk_id, selected_fin_ids, partial=True)
                st.session_state["df_bnk"] = df_bnk
                st.session_state["df_fin"] = df_fin
                _clear_selection(sel_bnk_id)
                st.rerun(scope="fragment")
        with btn_ignore_bnk:
            if st.button("Ignorar banco", key="mc_btn_ignore_bnk", use_container_width=True):
                df_bnk = _ignore_bank(df_bnk, selected_bnk_ids)
                st.session_state["df_bnk"] = df_bnk
                _clear_selection(sel_bnk_id)
                st.rerun(scope="fragment")
        with btn_ignore_fin:
            if st.button("Ignorar financeiro", key="mc_btn_ignore_fin", disabled=not selected_fin_ids, use_container_width=True):
                df_fin = _ignore_financeiro(df_fin, selected_fin_ids)
                st.session_state["df_fin"] = df_fin
                _clear_selection(sel_bnk_id)
                st.rerun(scope="fragment")
        with btn_clear:
            if st.button("Limpar seleção", key="mc_btn_clear", use_container_width=True):
                _clear_selection(sel_bnk_id)
                st.rerun(scope="fragment")
    elif len(selected_bnk_ids) > 1:
        # N banco → 1 financeiro: resumo já renderizado acima das listas.
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
                st.rerun(scope="fragment")
        with bulk_col:
            if st.button(f"Ignorar {len(selected_bnk_ids)} bancos", key="mc_btn_ignore_bnk_bulk", use_container_width=True):
                df_bnk = _ignore_bank(df_bnk, selected_bnk_ids)
                st.session_state["df_bnk"] = df_bnk
                _clear_selection(None)
                st.rerun(scope="fragment")
        with clear_col:
            if st.button("Limpar seleção", key="mc_btn_clear_bulk", use_container_width=True):
                _clear_selection(None)
                st.rerun(scope="fragment")
    else:
        st.info("Selecione uma linha do banco para ver sugestões, diferença e ações.")

    st.divider()
    _, nav_b, nav_c = st.columns([3, 1, 1])
    with nav_b:
        if st.button("Finalizar sem parcial", use_container_width=True, key="mc_nav_skip"):
            _clear_selection(st.session_state.get("manual_sel_bnk"))
            _finish(df_bnk, df_fin)
    with nav_c:
        has_pending = (df_bnk["_status"].astype(str) == STATUS_PENDENTE_PARCIAL).any()
        btn_label = "Seguir conciliação" if has_pending else "Finalizar"
        if st.button(btn_label, type="primary", use_container_width=True, key="mc_nav_next"):
            if has_pending:
                with st.spinner("Rodando Processo 5..."):
                    df_bnk, df_fin = _run_process_5(df_bnk, df_fin, params)
            _clear_selection(st.session_state.get("manual_sel_bnk"))
            _finish(df_bnk, df_fin)


def step_manual_conciliator(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: ConciliacaoParams,
) -> tuple:
    """
    Renderiza o conciliador manual.
    Retorna (df_bnk, df_fin, finished: bool).
    """
    st.session_state["df_bnk"] = df_bnk
    st.session_state["df_fin"] = df_fin

    if st.session_state.pop("manual_finished", False):
        return df_bnk, df_fin, True

    _manual_css()
    st.subheader("Conciliador Manual")
    _manual_workspace(params)

    return st.session_state["df_bnk"], st.session_state["df_fin"], False
