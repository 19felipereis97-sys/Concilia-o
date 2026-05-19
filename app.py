"""
Aplicativo principal Streamlit - Sistema Universal de Conciliação Bancária.
"""
from __future__ import annotations
import hashlib
import io
import time
from pathlib import Path
import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="Conciliador Bancário",
    page_icon="random",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core.params import ConciliacaoParams
from plan.client_store import (
    init_db, list_clientes_display,
    get_cliente_id, get_depara, upsert_depara,
    update_depara_batch, import_depara_stream,
    get_cliente_by_id, get_clientes_do_grupo, replicate_depara,
    get_conta_banco, set_conta_banco,
    log_acao, log_depara_change, change_password,
)
from plan.planilha_contabil import export_depara_csv, get_depara_dict
from core.normalize import (
    normalize_extrato, normalize_financeiro,
    STATUS_SEM_PAREAMENTO, STATUS_IGNORADO_SEM_PAR,
)
from core.engine import run_engine
from core.manual_review import apply_review_decisions
from core.report_builder import build_report
from core.background_jobs import (
    cancel_job,
    load_result,
    read_status,
    start_conciliation_job,
)
from ui.wizard_upload import (
    step_upload_extrato, step_upload_financeiro,
    step_header_config_extrato, step_header_config_financeiro,
)
from ui.wizard_mapping import (
    step_mapping_extrato, step_financeiro_modalidade,
    step_mapping_financeiro, step_params,
)
from ui.wizard_review import step_review
from ui.manual_conciliator import step_manual_conciliator
from ui.components import progress_bar
from ui.login import show_login, show_change_password_required
from ui.admin import show_admin_panel
from core.wizard_persistence import apply_wizard_config

init_db()

# Pré-preenche session_state com a config salva (apenas uma vez por sessão)
if "_wizard_config_loaded" not in st.session_state:
    apply_wizard_config(st.session_state)
    st.session_state["_wizard_config_loaded"] = True

# ── Normalização incremental ───────────────────────────────────────────────────

_DEFAULT_DISCARD = ["SDO", "SALDO", "S/D", "SALDO ANTERIOR", "SALDO DO DIA"]


def _norm_bnk_fp(discard, default_year, hist_sep) -> tuple:
    raw = st.session_state.get("extrato_file", b"")
    h = hashlib.md5(raw).hexdigest()[:12] if raw else ""
    return (
        h,
        st.session_state.get("extrato_suffix"),
        str(st.session_state.get("extrato_mapping")),
        int(default_year),
        tuple(discard),
        str(hist_sep),
    )


def _norm_fin_fp(discard, default_year) -> tuple:
    raw1 = st.session_state.get("fin_file", b"")
    raw2 = st.session_state.get("fin2_file", b"")
    h1 = hashlib.md5(raw1).hexdigest()[:12] if raw1 else ""
    h2 = hashlib.md5(raw2).hexdigest()[:12] if raw2 else ""
    return (
        h1, h2,
        st.session_state.get("fin_suffix"),
        st.session_state.get("fin2_suffix"),
        str(st.session_state.get("fin_mapping")),
        str(st.session_state.get("fin2_mapping")),
        st.session_state.get("fin_modalidade_str", "COMPLETO"),
        int(default_year),
        tuple(discard),
    )


def _normalizar_extrato_incremental(params: ConciliacaoParams | None = None) -> None:
    """Normaliza o extrato apenas se os inputs mudaram desde a última execução."""
    discard = params.discard_patterns if params else _DEFAULT_DISCARD
    default_year = params.default_year if params else int(st.session_state.get("default_year", 0))
    hist_sep = params.hist_separator if params else " - "

    fp = _norm_bnk_fp(discard, default_year, hist_sep)
    if st.session_state.get("_norm_bnk_fp") == fp:
        return  # cache hit — df_bnk já é válido

    ext_mapping = st.session_state.get("extrato_mapping")
    extrato_file = st.session_state.get("extrato_file")
    if ext_mapping is None or extrato_file is None:
        return

    norm_params = ConciliacaoParams(
        discard_patterns=list(discard),
        default_year=default_year,
        hist_separator=hist_sep,
    )
    t0 = time.perf_counter()
    df_bnk = normalize_extrato(
        io.BytesIO(extrato_file),
        ext_mapping,
        norm_params,
        suffix=st.session_state.get("extrato_suffix", ".xlsx"),
    )
    _perf_add("Normalização do extrato", t0, f"{len(df_bnk)} movimento(s)")
    st.session_state["df_bnk"] = df_bnk
    st.session_state["_norm_bnk_fp"] = fp


def _normalizar_financeiro_incremental(params: ConciliacaoParams | None = None) -> None:
    """Normaliza o financeiro apenas se os inputs mudaram desde a última execução."""
    discard = params.discard_patterns if params else _DEFAULT_DISCARD
    default_year = params.default_year if params else int(st.session_state.get("default_year", 0))

    fp = _norm_fin_fp(discard, default_year)
    if st.session_state.get("_norm_fin_fp") == fp:
        return  # cache hit

    fin_mapping = st.session_state.get("fin_mapping")
    fin_file = st.session_state.get("fin_file")
    if fin_mapping is None or fin_file is None:
        return

    norm_params = ConciliacaoParams(
        discard_patterns=list(discard),
        default_year=default_year,
    )
    modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")

    t0 = time.perf_counter()
    df_fin = normalize_financeiro(
        io.BytesIO(fin_file),
        fin_mapping,
        norm_params,
        suffix=st.session_state.get("fin_suffix", ".xlsx"),
    )
    _perf_add("Normalização do financeiro", t0, f"{len(df_fin)} movimento(s)")

    if modalidade_str == "SEPARADOS":
        fin2_mapping = st.session_state.get("fin2_mapping")
        fin2_file = st.session_state.get("fin2_file")
        if fin2_mapping and fin2_file:
            t0 = time.perf_counter()
            df_fin2 = normalize_financeiro(
                io.BytesIO(fin2_file),
                fin2_mapping,
                norm_params,
                suffix=st.session_state.get("fin2_suffix", ".xlsx"),
            )
            n_fin1 = len(df_fin)
            df_fin2["_id"] = [
                f"FIN_{int(x.rsplit('_', 1)[-1]) + n_fin1:04d}" if x.startswith("FIN_") else x
                for x in df_fin2["_id"]
            ]
            df_fin = pd.concat([df_fin, df_fin2], ignore_index=True)
            _perf_add("Normalização do financeiro 2", t0, f"{len(df_fin2)} movimento(s)")

    st.session_state["df_fin"] = df_fin
    st.session_state["_norm_fin_fp"] = fp


@st.cache_data(ttl=300, show_spinner=False)
def _cached_clientes_display(apenas_ativos: bool = True) -> list[dict]:
    return list_clientes_display(apenas_ativos=apenas_ativos)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_depara_rows(cliente_id: int) -> list[dict]:
    return get_depara(cliente_id)


def _clear_depara_cache():
    _cached_depara_rows.clear()


def _clear_clientes_cache():
    _cached_clientes_display.clear()


def _perf_add(etapa: str, inicio: float, extra: str = ""):
    timings = [
        item for item in st.session_state.setdefault("performance_timings", [])
        if item.get("Etapa") != etapa
    ]
    timings.append({
        "Etapa": etapa,
        "Tempo (s)": round(time.perf_counter() - inicio, 3),
        "Detalhes": extra,
    })
    st.session_state["performance_timings"] = timings


def _render_performance_timings():
    timings = st.session_state.get("performance_timings", [])
    if not timings:
        return

    sorted_t = sorted(timings, key=lambda x: float(x["Tempo (s)"]))
    total = sum(float(t["Tempo (s)"]) for t in timings)
    mais_demorado = sorted_t[-1]
    mais_rapido = sorted_t[0]

    with st.expander(f"Diagnóstico da execução — {total:.3f}s no total", expanded=False):

        # ── Resumo de tempos ──────────────────────────────────────────────────
        c1, c2, c3 = st.columns(3)
        c1.metric("Tempo total", f"{total:.3f}s")
        c2.metric(
            "Processo mais demorado",
            f"{float(mais_demorado['Tempo (s)']):.3f}s",
            delta=mais_demorado["Etapa"],
            delta_color="off",
        )
        if mais_rapido["Etapa"] != mais_demorado["Etapa"]:
            c3.metric(
                "Processo mais rápido",
                f"{float(mais_rapido['Tempo (s)']):.3f}s",
                delta=mais_rapido["Etapa"],
                delta_color="off",
            )

        st.markdown("**Detalhamento por etapa**")
        st.dataframe(pd.DataFrame(timings), hide_index=True, use_container_width=True)


def _render_agent_report():
    report = st.session_state.get("agent_report")
    if not report:
        return
    resumo = report.get("resumo", {})
    findings = report.get("findings", [])
    with st.expander("Agentes de supervisão da execução", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Banco", resumo.get("linhas_banco", 0))
        c2.metric("Financeiro", resumo.get("linhas_financeiro", 0))
        c3.metric("N:1", resumo.get("n_to_one", "inativo"))
        c4.metric("Alertas", len([f for f in findings if f.get("nivel") in ("atenção", "crítico")]))
        st.caption(
            f"Período banco: {resumo.get('periodo_banco', '')} | "
            f"Período financeiro: {resumo.get('periodo_financeiro', '')}"
        )
        if findings:
            st.dataframe(pd.DataFrame(findings), hide_index=True, width="stretch")
        else:
            st.success("Nenhum alerta relevante identificado pelos agentes.")


# ── Sidebar ────────────────────────────────────────────────────────────────────

def sidebar() -> str:
    with st.sidebar:
        # ── Logo ──────────────────────────────────────────────────────────────
        logo_path = Path(__file__).parent / "assets" / "logo_JCA.png_name_20221221-21843-1bcb5tg.png"
        if logo_path.exists():
            st.markdown(
                f'<div style="text-align:center">'
                f'<img src="data:image/png;base64,{__import__("base64").b64encode(logo_path.read_bytes()).decode()}" width="160">'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown("**JCA Contadores Associados**")

        st.divider()

        # ── Card do usuário ───────────────────────────────────────────────────
        email = st.session_state.get("usuario_email", "")
        perfil = st.session_state.get("usuario_perfil", "")
        nome = st.session_state.get("usuario_nome", "").strip()
        depto = st.session_state.get("usuario_departamento", "").strip()

        perfil_label = "Administrador" if perfil == "admin" else "Operacional"
        cor = "#FF9500" if perfil == "admin" else "#1565C0"
        inicial = (nome or email)[0].upper() if (nome or email) else "?"
        display_name = nome if nome else email
        depto_html = (
            f"<div style='font-size:0.72em;color:rgba(120,120,120,0.95);margin-top:3px;"
            f"letter-spacing:0.06em;text-transform:uppercase'>{depto}</div>"
            if depto else ""
        )

        st.markdown(
            f"""
            <div style="display:flex;flex-direction:column;align-items:center;
                        padding:14px 10px 16px;margin-bottom:2px;
                        background:var(--secondary-background-color);
                        border-radius:14px;
                        border:1px solid rgba(120,120,120,0.18);
                        gap:8px;">
                <div style="width:54px;height:54px;border-radius:50%;
                            background:linear-gradient(135deg,{cor},{cor}99);
                            display:flex;align-items:center;justify-content:center;
                            font-size:1.45em;font-weight:800;color:white;
                            box-shadow:0 4px 14px {cor}55;flex-shrink:0;">
                    {inicial}
                </div>
                <div style="text-align:center;line-height:1.35;">
                    <div style="font-weight:700;font-size:0.88em;color:var(--text-color);
                                letter-spacing:0.04em;text-transform:uppercase;">
                        {display_name}
                    </div>
                </div>
                <span style="background:{cor}1a;color:{cor};
                             padding:3px 13px;border-radius:20px;
                             font-size:0.70em;font-weight:700;
                             border:1px solid {cor}44;letter-spacing:0.05em;
                             text-transform:uppercase;">
                    {perfil_label}
                </span>
                {depto_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()

        opcoes = ["Conciliação Contábil", "De x Para Geral", "Minha Conta"]
        if perfil == "admin":
            opcoes.append("Configurações Gerais")

        pagina = st.radio("Navegação", opcoes, key="nav_pagina")

        st.divider()
        if st.button("Sair", key="btn_logout", use_container_width=True):
            log_acao(email, "LOGOUT", "")
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    return pagina


# ── Helpers visuais ────────────────────────────────────────────────────────────

def _brl(value: float) -> str:
    """Formata número no padrão brasileiro: R$ 1.234,56"""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _value_card(label: str, value: float):
    """Card colorido: verde para valores >= 0, vermelho para negativos."""
    if value >= 0:
        color, bg = "#21C354", "#21C35418"
    else:
        color, bg = "#FF4B4B", "#FF4B4B18"
    fmt = _brl(value)
    st.markdown(
        f"""<div style="background:{bg};border-left:5px solid {color};
        padding:14px 18px;border-radius:8px;margin-bottom:10px;">
        <p style="margin:0;font-size:0.75em;color:#888;font-weight:600;
        text-transform:uppercase;letter-spacing:0.5px">{label}</p>
        <p style="margin:6px 0 0;font-size:1.5em;font-weight:700;color:{color}">{fmt}</p>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_balance_comparison(bw: dict):
    """Renderiza comparativo de saldos visual na Etapa 8."""
    diverge_any = bw["diverge_deb"] or bw["diverge_cre"]
    with st.expander("📊 Comparativo de Saldos — Extrato vs Financeiro", expanded=diverge_any):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**🏦 Extrato Bancário**")
            _value_card("Débitos (Saídas)", bw["bnk_deb"])
            _value_card("Créditos (Entradas)", bw["bnk_cre"])
        with col2:
            st.markdown("**💼 Sistema Financeiro**")
            _value_card("Pagamentos (Saídas)", bw["fin_neg"])
            _value_card("Recebimentos (Entradas)", bw["fin_pos"])

        st.divider()
        st.markdown("**Diferenças Identificadas**")
        if not diverge_any:
            st.success("✅ Totais dentro da tolerância de 1% — sem divergências.")
        else:
            if bw["diverge_deb"]:
                diff = bw["bnk_deb"] - bw["fin_neg"]
                _value_card(
                    f"Δ Saídas — Banco {_brl(bw['bnk_deb'])}  ×  Financeiro {_brl(bw['fin_neg'])}",
                    diff,
                )
                st.warning(
                    f"Débitos bancários e pagamentos financeiros divergem em **{_brl(abs(diff))}**. "
                    "Verifique se os períodos ou arquivos correspondem."
                )
            if bw["diverge_cre"]:
                diff = bw["bnk_cre"] - bw["fin_pos"]
                _value_card(
                    f"Δ Entradas — Banco {_brl(bw['bnk_cre'])}  ×  Financeiro {_brl(bw['fin_pos'])}",
                    diff,
                )
                st.warning(
                    f"Créditos bancários e recebimentos financeiros divergem em **{_brl(abs(diff))}**. "
                    "Verifique se os períodos ou arquivos correspondem."
                )


# ── Etapa 1 ────────────────────────────────────────────────────────────────────

def _step_cliente_conta_banco() -> bool:
    st.subheader("Cliente e banco")

    clientes = _cached_clientes_display()
    if not clientes:
        st.warning("Cadastre uma empresa em Configurações Gerais antes de iniciar.")
        return False

    labels = {c["id"]: c["label"] for c in clientes}
    by_id = {c["id"]: c for c in clientes}
    ids = list(labels)
    cliente_id = st.selectbox(
        "Empresa / Cliente",
        ids,
        index=None,
        key="wiz_cli_sel_id_empty_default",
        placeholder="Selecione uma empresa",
        format_func=lambda cid: labels.get(cid, str(cid)),
    )
    cliente_fluxo = by_id.get(cliente_id, {}).get("nome", "")
    st.session_state["wiz_cli_saved_id"] = cliente_id

    st.session_state["cliente_conciliacao"] = cliente_fluxo
    st.session_state["cliente_conciliacao_id"] = cliente_id

    conta_ref = st.session_state.get("cliente_conta_banco_ref")
    conta_atual = st.session_state.get("conta_banco_conciliacao")
    if conta_ref != cliente_fluxo:
        conta_atual = ""
        st.session_state["cliente_conta_banco_ref"] = cliente_fluxo
        st.session_state["conta_banco_conciliacao_input"] = ""

    conta_banco = st.text_input(
        "Conta contábil do banco (ex.: 56)",
        value=conta_atual or "",
        key="conta_banco_conciliacao_input",
        placeholder="Informe o código reduzido do banco",
    ).strip()
    st.session_state["conta_banco_conciliacao"] = conta_banco

    if cliente_id and conta_banco:
        set_conta_banco(cliente_id, conta_banco)

    if not cliente_id:
        st.warning("Selecione um cliente válido para vincular o De x Para.")
        return False
    if not conta_banco:
        st.warning("Informe a conta contábil do banco antes de avançar.")
        return False
    return True


# ── Wizard de conciliação ──────────────────────────────────────────────────────

def wizard_page():
    st.title("Conciliação Contábil")

    if "wiz_step" not in st.session_state:
        st.session_state["wiz_step"] = 1

    step = st.session_state["wiz_step"]
    progress_bar(step, 8)

    if step == 1:
        cliente_ok = _step_cliente_conta_banco()
        ok = step_upload_extrato()
        if ok and cliente_ok and st.button("Próximo", key="wiz_1_next"):
            st.session_state["wiz_step"] = 2
            st.rerun()

    elif step == 2:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 1; st.rerun()
        ok = step_upload_financeiro()
        if ok and st.button("Próximo", key="wiz_2_next"):
            st.session_state["wiz_step"] = 3
            st.rerun()

    elif step == 3:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 2; st.rerun()
        ok = step_header_config_extrato()
        if ok and st.button("Próximo", key="wiz_3_next"):
            st.session_state["wiz_step"] = 4
            st.rerun()

    elif step == 4:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 3; st.rerun()
        ok = step_header_config_financeiro()
        if ok and st.button("Próximo", key="wiz_4_next"):
            st.session_state["wiz_step"] = 5
            st.rerun()

    elif step == 5:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 4; st.rerun()
        ok = step_mapping_extrato()
        if ok and st.button("Próximo", key="wiz_5_next"):
            with st.spinner("Processando extrato..."):
                _normalizar_extrato_incremental()
            st.session_state["wiz_step"] = 6
            st.rerun()

    elif step == 6:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 5; st.rerun()
        df_bnk_ready = st.session_state.get("df_bnk")
        if df_bnk_ready is not None and not df_bnk_ready.empty:
            st.info(f"Extrato processado: {len(df_bnk_ready)} movimentos prontos.")
        modalidade = step_financeiro_modalidade()
        ok = step_mapping_financeiro(modalidade)
        if ok and st.button("Próximo", key="wiz_6_next"):
            with st.spinner("Processando financeiro..."):
                _normalizar_financeiro_incremental()
            st.session_state["wiz_step"] = 7
            st.rerun()

    elif step == 7:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 6; st.rerun()
        df_bnk_ready = st.session_state.get("df_bnk")
        df_fin_ready = st.session_state.get("df_fin")
        if df_bnk_ready is not None and not df_bnk_ready.empty:
            st.success(f"Extrato: {len(df_bnk_ready)} movimentos prontos.")
        if df_fin_ready is not None and not df_fin_ready.empty:
            st.success(f"Financeiro: {len(df_fin_ready)} movimentos prontos.")
        step_params()
        if st.session_state.get("conciliation_job_id"):
            _render_conciliation_job()
        elif st.button("Executar conciliação", key="wiz_7_run", type="primary"):
            _iniciar_conciliacao_job()

    elif step == 8:
        _etapa_revisao_download()


def _compute_balance_warning(df_bnk: pd.DataFrame, df_fin: pd.DataFrame, modalidade_str: str) -> dict:
    bnk_deb = float(df_bnk.loc[df_bnk["_valor"] < 0, "_valor"].sum())
    bnk_cre = float(df_bnk.loc[df_bnk["_valor"] > 0, "_valor"].sum())
    fin_neg = float(df_fin.loc[df_fin["_valor"] < 0, "_valor"].sum())
    fin_pos = float(df_fin.loc[df_fin["_valor"] > 0, "_valor"].sum())

    resultado = {
        "bnk_deb": bnk_deb, "bnk_cre": bnk_cre,
        "fin_neg": fin_neg, "fin_pos": fin_pos,
        "modalidade": modalidade_str,
        "diverge_deb": False, "diverge_cre": False,
    }
    tol_rel = 0.01
    if bnk_deb != 0 and abs(fin_neg) > 0:
        resultado["diverge_deb"] = abs((bnk_deb - fin_neg) / bnk_deb) > tol_rel
    if bnk_cre != 0 and fin_pos > 0:
        resultado["diverge_cre"] = abs((bnk_cre - fin_pos) / bnk_cre) > tol_rel
    return resultado


def _iniciar_conciliacao_job():
    with st.spinner("Preparando conciliação em segundo plano..."):
        try:
            params = st.session_state["params"]
            modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")
            status_box = st.empty()

            # Re-normaliza apenas se discard_patterns ou default_year mudaram
            # desde a normalização incremental feita nas etapas 5 e 6.
            status_box.info("Checkpoint: normalização do extrato.")
            _normalizar_extrato_incremental(params)
            status_box.info("Checkpoint: normalização do financeiro.")
            _normalizar_financeiro_incremental(params)

            df_bnk = st.session_state.get("df_bnk", pd.DataFrame())
            df_fin = st.session_state.get("df_fin", pd.DataFrame())

            if df_bnk.empty or df_fin.empty:
                st.error("Dados não normalizados. Volte e verifique os mapeamentos.")
                return

            job_id = start_conciliation_job(df_bnk, df_fin, params, modalidade_str)
            st.session_state["conciliation_job_id"] = job_id
            st.session_state["performance_timings"] = []
            status_box.info("Checkpoint: job iniciado em segundo plano.")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao iniciar a conciliação em segundo plano: {e}")


def _render_conciliation_job():
    job_id = st.session_state.get("conciliation_job_id")
    if not job_id:
        return

    status = read_status(job_id)
    state = status.get("status", "")
    message = status.get("message", "")

    if state in {"queued", "running"}:
        st.info(message or "Conciliação em andamento.")
        progress_value = int(status.get("progress", 0) or 0)
        st.progress(min(max(progress_value, 0), 100), text=f"{progress_value}% - {message}")
        stage = status.get("stage")
        if stage:
            st.caption(f"Checkpoint atual: {stage}")
        st.caption(f"Job: {job_id}")
        if st.button("Cancelar conciliação", key="job_cancel"):
            cancel_job(job_id)
            st.rerun()
        time.sleep(1)
        st.rerun()
        return

    if state == "done":
        try:
            result = load_result(job_id)
            st.session_state["df_bnk"] = result["df_bnk"]
            st.session_state["df_fin"] = result["df_fin"]
            st.session_state["review_cards"] = result["review_cards"]
            st.session_state["balance_warning"] = result["balance_warning"]
            st.session_state["performance_timings"] = result["performance_timings"]
            st.session_state["agent_report"] = result["agent_report"]

            cliente_nome = st.session_state.get("cliente_conciliacao", "desconhecido")
            log_acao(
                st.session_state.get("usuario_email", "desconhecido"),
                "CONCILIACAO_REALIZADA",
                f"cliente={cliente_nome};job={job_id}",
            )

            st.session_state.pop("conciliation_job_id", None)
            st.session_state["wiz_step"] = 8
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao carregar resultado da conciliação: {e}")
        return

    if state == "cancelled":
        st.warning(message or "Conciliação cancelada.")
        if st.button("Liberar nova execução", key="job_clear_cancelled"):
            st.session_state.pop("conciliation_job_id", None)
            st.rerun()
        return

    st.error(message or "A conciliação em segundo plano falhou.")
    if st.button("Liberar nova execução", key="job_clear_error"):
        st.session_state.pop("conciliation_job_id", None)
        st.rerun()


def _metrics_bar(df_bnk: pd.DataFrame) -> None:
    if df_bnk.empty or "_status" not in df_bnk.columns:
        return
    conciliados      = df_bnk["_status"].isin(["CONCILIADO", "CONCILIADO_MANUAL"]).sum()
    sem_par          = (df_bnk["_status"] == "SEM_PAREAMENTO").sum()
    revisar          = df_bnk["_status"].isin(["REVISAR", "REVISAR_COLISAO"]).sum()
    parciais         = df_bnk["_status"].isin(["PARCIALMENTE CONCILIADO", "PENDENTE DE CONCILIAÇÃO PARCIAL"]).sum()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Conciliados",  int(conciliados))
    c2.metric("Sem par",      int(sem_par))
    c3.metric("A revisar",    int(revisar))
    c4.metric("Parciais",     int(parciais))
    c5.metric("Total banco",  len(df_bnk))


def _release_all_revisar(df_bnk: pd.DataFrame, df_fin: pd.DataFrame) -> tuple:
    """Libera todos os REVISAR/REVISAR_COLISAO não resolvidos de volta ao pool livre."""
    bnk_mask = df_bnk["_status"].isin(["REVISAR", "REVISAR_COLISAO"])
    df_bnk.loc[bnk_mask, "_status"]  = "SEM_PAREAMENTO"
    df_bnk.loc[bnk_mask, "_metodo"]  = ""
    df_bnk.loc[bnk_mask, "_ids_fin"] = ""
    fin_mask = df_fin["_status"].astype(str) == "REVISAR"
    df_fin.loc[fin_mask, "_status"] = "IGNORADO_SEM_CONTRAPARTIDA"
    df_fin.loc[fin_mask, "_metodo"] = ""
    df_fin.loc[fin_mask, "_id_bnk"] = ""
    return df_bnk, df_fin


def _restore_review_released(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    protected_bnk: set,
    protected_fin: set,
) -> tuple:
    """
    Garante que lançamentos liberados pelo usuário na revisão
    (ignorados ou sem decisão) permaneçam disponíveis para a
    conciliação manual, mesmo que run_engine os tenha re-bloqueado.
    """
    bnk_pos = {str(v): i for v, i in zip(df_bnk["_id"], df_bnk.index)}
    fin_pos = {str(v): i for v, i in zip(df_fin["_id"], df_fin.index)}

    for id_b in protected_bnk:
        bi = bnk_pos.get(str(id_b))
        if bi is None:
            continue
        if str(df_bnk.at[bi, "_status"]) == STATUS_SEM_PAREAMENTO:
            continue  # já livre, ok
        # Motor re-bloqueou ou re-conciliou — desfaz o vínculo
        ids_fin_str = str(df_bnk.at[bi, "_ids_fin"] or "")
        for id_f in [x.strip() for x in ids_fin_str.split(";") if x.strip()]:
            fi = fin_pos.get(id_f)
            if fi is not None:
                fin_bnk_ref = {x.strip() for x in str(df_fin.at[fi, "_id_bnk"] or "").split(";") if x.strip()}
                if str(id_b) in fin_bnk_ref:
                    df_fin.at[fi, "_status"] = STATUS_IGNORADO_SEM_PAR
                    df_fin.at[fi, "_metodo"] = ""
                    df_fin.at[fi, "_id_bnk"] = ""
        df_bnk.at[bi, "_status"] = STATUS_SEM_PAREAMENTO
        df_bnk.at[bi, "_metodo"] = ""
        df_bnk.at[bi, "_ids_fin"] = ""

    for id_f in protected_fin:
        fi = fin_pos.get(str(id_f))
        if fi is None:
            continue
        if str(df_fin.at[fi, "_status"]) == STATUS_IGNORADO_SEM_PAR:
            continue  # já livre, ok
        # Motor re-bloqueou — desfaz
        ids_bnk_str = str(df_fin.at[fi, "_id_bnk"] or "")
        for id_b in [x.strip() for x in ids_bnk_str.split(";") if x.strip()]:
            bi = bnk_pos.get(id_b)
            if bi is not None:
                bnk_fin_ref = {x.strip() for x in str(df_bnk.at[bi, "_ids_fin"] or "").split(";") if x.strip()}
                if str(id_f) in bnk_fin_ref:
                    df_bnk.at[bi, "_status"] = STATUS_SEM_PAREAMENTO
                    df_bnk.at[bi, "_metodo"] = ""
                    df_bnk.at[bi, "_ids_fin"] = ""
        df_fin.at[fi, "_status"] = STATUS_IGNORADO_SEM_PAR
        df_fin.at[fi, "_metodo"] = ""
        df_fin.at[fi, "_id_bnk"] = ""

    return df_bnk, df_fin


def _render_download_section(df_bnk: pd.DataFrame, df_fin: pd.DataFrame) -> None:
    cliente_fluxo = st.session_state.get("cliente_conciliacao")
    cliente_id    = st.session_state.get("cliente_conciliacao_id")
    if not cliente_id and cliente_fluxo:
        cliente_id = get_cliente_id(cliente_fluxo)
    conta_banco = st.session_state.get("conta_banco_conciliacao", "")
    if not conta_banco and cliente_id:
        conta_banco = get_conta_banco(cliente_id)

    depara_dict = {}
    if cliente_id:
        t0 = time.perf_counter()
        depara_rows = _cached_depara_rows(cliente_id)
        depara_dict = get_depara_dict(depara_rows)
        _perf_add("Carga do De x Para", t0, f"{len(depara_rows)} regra(s)")
    else:
        st.warning("Selecione um cliente na etapa 1 para aplicar o De x Para.")

    hist_mode = st.radio(
        "Histórico na aba Importação Alterdata",
        ["Banco + Financeiro", "Somente bancário", "Somente financeiro"],
        index=0, horizontal=True, key="alterdata_hist_mode",
    )
    if st.button("Gerar Relatório Excel", type="primary", key="btn_gerar"):
        if not cliente_id:
            st.error("Selecione um cliente na etapa 1 antes de gerar o relatório com De x Para.")
            return
        if not conta_banco.strip():
            st.error("Informe a conta contábil do banco na etapa 1.")
            return
        with st.spinner("Gerando relatório..."):
            t0 = time.perf_counter()
            xlsx_bytes = build_report(df_bnk, df_fin, depara_dict, conta_banco=conta_banco, hist_mode=hist_mode)
            _perf_add("Geração do Excel", t0, f"{len(df_bnk)} banco / {len(df_fin)} financeiro")
            log_acao(st.session_state.get("usuario_email", "desconhecido"), "RELATORIO_GERADO", f"cliente={cliente_fluxo}")
            _cli_data   = get_cliente_by_id(cliente_id)
            _codigo     = (_cli_data.get("codigo_interno") or "").strip() if _cli_data else ""
            _nome_trunc = ((_cli_data.get("nome") or "").strip()[:20] if _cli_data else "")
            _fname      = f"Conciliacao_{_codigo} - {_nome_trunc}.xlsx" if _codigo else "relatorio_conciliacao.xlsx"
            st.download_button(
                label="Baixar Relatório (.xlsx)", data=xlsx_bytes,
                file_name=_fname, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    _render_agent_report()
    _render_performance_timings()


def _etapa_revisao_download():
    df_bnk = st.session_state.get("df_bnk", pd.DataFrame())
    df_fin = st.session_state.get("df_fin", pd.DataFrame())
    cards  = st.session_state.get("review_cards", [])
    params = st.session_state.get("params")
    phase  = st.session_state.get("review_phase", "B")

    _metrics_bar(df_bnk)

    bw = st.session_state.get("balance_warning")
    if bw:
        _render_balance_comparison(bw)

    # ── Fase B — Revisão de ambiguidades ──────────────────────────────────────
    if phase == "B":
        if cards:
            cards = step_review(cards)
            st.session_state["review_cards"] = cards
        else:
            st.success("Nenhuma ambiguidade encontrada — pode avançar direto para a conciliação manual.")

        st.divider()
        col_adv, col_skip = st.columns([2, 1])
        with col_adv:
            if st.button("Aplicar decisões e avançar para Conciliação Manual",
                         type="primary", key="btn_advance_manual", use_container_width=True):
                with st.spinner("Aplicando decisões e re-executando motor..."):
                    # IDs de lançamentos NÃO conciliados na revisão — devem
                    # ficar livres para a conciliação manual mesmo após run_engine.
                    protected_bnk = {
                        c.id_bnk for c in cards
                        if c.id_bnk and not (c.decisao == "conciliar" and c.selecao_pre)
                    }
                    protected_fin = {
                        c.id_fin for c in cards
                        if c.id_fin and not (c.decisao == "conciliar" and c.selecao_pre)
                    }
                    df_bnk, df_fin = apply_review_decisions(df_bnk, df_fin, cards)
                    df_bnk, df_fin = _release_all_revisar(df_bnk, df_fin)
                    if params is not None:
                        df_bnk, df_fin = run_engine(df_bnk, df_fin, params, include_partial=False)
                        df_bnk, df_fin = _restore_review_released(
                            df_bnk, df_fin, protected_bnk, protected_fin
                        )
                    st.session_state["df_bnk"]      = df_bnk
                    st.session_state["df_fin"]       = df_fin
                    st.session_state["review_phase"] = "D"
                    st.session_state.pop("manual_sel_bnk", None)
                st.rerun()
        with col_skip:
            if st.button("Pular para Download", key="btn_skip_download", use_container_width=True):
                st.session_state["review_phase"] = "done"
                st.rerun()

    # ── Fase D — Conciliador manual ───────────────────────────────────────────
    elif phase == "D":
        if params is None:
            st.error("Parâmetros não encontrados. Volte à Etapa 7.")
            return
        df_bnk, df_fin, finished = step_manual_conciliator(df_bnk, df_fin, params)
        st.session_state["df_bnk"] = df_bnk
        st.session_state["df_fin"] = df_fin
        if finished:
            st.session_state["review_phase"] = "done"
            st.rerun()

    # ── Fase done — Download ──────────────────────────────────────────────────
    elif phase == "done":
        if st.button("↩ Voltar para Conciliação Manual", key="btn_volta_manual"):
            st.session_state["review_phase"] = "D"
            st.rerun()
        st.divider()
        _render_download_section(df_bnk, df_fin)

    # ── Nova contabilização (sempre disponível) ───────────────────────────────
    st.divider()
    if st.button("Nova contabilização", key="btn_nova"):
        for k in [
            "df_bnk", "df_fin", "review_cards", "wiz_step",
            "extrato_file", "fin_file", "fin2_file",
            "extrato_mapping", "fin_mapping", "fin2_mapping",
            "fin_modalidade_str", "params", "balance_warning",
            "_norm_bnk_fp", "_norm_fin_fp", "performance_timings",
            "agent_report", "review_phase", "manual_sel_bnk",
            "cliente_conciliacao", "cliente_conciliacao_id",
            "cliente_conciliacao_select", "conta_banco_conciliacao",
            "conta_banco_conciliacao_input", "cliente_conta_banco_ref",
            "wiz_cli_busca", "wiz_cli_sel", "wiz_cli_sel_id", "wiz_cli_sel_val", "wiz_cli_saved_id",
        ]:
            st.session_state.pop(k, None)
        st.rerun()


# ── De x Para Geral ────────────────────────────────────────────────────────────

def _render_depara_replication(
    cliente_id: int,
    cliente: str,
    cliente_label: str,
    rows: list[dict],
    usuario_email: str,
    clientes: list[dict],
):
    st.divider()
    st.subheader("Replicar De x Para")

    if not rows:
        st.info("Cadastre ou importe regras nesta empresa antes de replicar.")
        return

    modo = st.radio(
        "Forma de replicação",
        ["Mesclar/atualizar regras no destino", "Substituir toda a base do destino"],
        horizontal=True,
        key=f"dp_rep_modo_{cliente_id}",
    )
    substituir = modo.startswith("Substituir")

    destino_opts = [c for c in clientes if c["id"] != cliente_id]
    destino_labels = {c["id"]: c["label"] for c in destino_opts}
    destino_ids = list(destino_labels)

    col_emp, col_grp = st.columns(2)
    with col_emp:
        st.markdown("**Para outra empresa**")
        if destino_ids:
            destino_id = st.selectbox(
                "Empresa destino",
                destino_ids,
                key=f"dp_rep_dest_{cliente_id}",
                format_func=lambda cid: destino_labels.get(cid, str(cid)),
            )
            if st.button("Replicar para empresa", key=f"dp_rep_emp_{cliente_id}"):
                res = replicate_depara(cliente_id, [destino_id], substituir=substituir)
                _clear_depara_cache()
                destino = get_cliente_by_id(destino_id) or {}
                destino_nome = destino.get("nome", str(destino_id))
                detalhes = (
                    f"origem={cliente_label};destino={destino.get('label', destino_nome)};"
                    f"modo={'substituir' if substituir else 'mesclar'};regras={res['regras_origem']}"
                )
                log_depara_change(destino_id, destino_nome, usuario_email, "REPLICACAO_RECEBIDA", detalhes)
                log_depara_change(cliente_id, cliente, usuario_email, "REPLICACAO_ENVIADA", detalhes)
                log_acao(usuario_email, "DEPARA_REPLICADO", detalhes)
                st.success(f"De x Para replicado para {destino.get('label', destino_nome)}.")
                st.rerun()
        else:
            st.info("Não há outra empresa cadastrada para receber as regras.")

    with col_grp:
        st.markdown("**Para o grupo da empresa**")
        grupo_membros = [c for c in get_clientes_do_grupo(cliente_id) if c["id"] != cliente_id]
        if grupo_membros:
            grupo = grupo_membros[0].get("grupo", "")
            st.caption(f"Grupo: {grupo}")
            st.caption(f"{len(grupo_membros)} empresa(s) destino.")
            if st.button("Replicar para grupo", key=f"dp_rep_grupo_{cliente_id}"):
                destino_grupo_ids = [c["id"] for c in grupo_membros]
                res = replicate_depara(cliente_id, destino_grupo_ids, substituir=substituir)
                _clear_depara_cache()
                detalhes = (
                    f"origem={cliente_label};grupo={grupo};"
                    f"modo={'substituir' if substituir else 'mesclar'};"
                    f"empresas={res['empresas']};regras={res['regras_origem']}"
                )
                for destino in grupo_membros:
                    log_depara_change(destino["id"], destino["nome"], usuario_email, "REPLICACAO_RECEBIDA", detalhes)
                log_depara_change(cliente_id, cliente, usuario_email, "REPLICACAO_GRUPO_ENVIADA", detalhes)
                log_acao(usuario_email, "DEPARA_REPLICADO_GRUPO", detalhes)
                st.success(f"De x Para replicado para {res['empresas']} empresa(s) do grupo.")
                st.rerun()
        else:
            st.info("Esta empresa não possui outras empresas ativas no mesmo grupo.")


def depara_page():
    st.title("De x Para Geral")

    usuario_email = st.session_state.get("usuario_email", "desconhecido")

    clientes = _cached_clientes_display()
    if not clientes:
        st.info("Nenhuma empresa cadastrada. Acesse **Configurações Gerais** para adicionar.")
        return

    labels = {c["id"]: c["label"] for c in clientes}
    by_id = {c["id"]: c for c in clientes}
    ids = list(labels)
    cliente_id = st.selectbox(
        "Selecionar empresa",
        ids,
        index=None,
        key="dp_cli_sel_id_empty_default",
        placeholder="Selecione uma empresa",
        format_func=lambda cid: labels.get(cid, str(cid)),
    )
    cliente_row = by_id.get(cliente_id, {})
    cliente = cliente_row.get("nome", "")
    cliente_label = cliente_row.get("label", cliente)
    st.session_state["dp_cli_saved_id"] = cliente_id

    if not cliente_id:
        return

    st.divider()
    st.subheader(f"De x Para - {cliente_label}")

    rows = _cached_depara_rows(cliente_id)
    df = pd.DataFrame(rows, columns=["classif", "conta_contabil"])
    df_display = df.rename(columns={
        "classif": "Classificação financeira",
        "conta_contabil": "Conta contábil",
    })

    edit_key = f"dp_edit_mode_{cliente_id}"
    if edit_key not in st.session_state:
        st.session_state[edit_key] = False

    c1, c2 = st.columns([1, 5])
    with c1:
        label = "Cancelar edição" if st.session_state[edit_key] else "Editar"
        if st.button(label, key=f"dp_toggle_edit_{cliente_id}"):
            st.session_state[edit_key] = not st.session_state[edit_key]
            st.rerun()
    with c2:
        if rows:
            csv_bytes = export_depara_csv(rows)
            st.download_button("Exportar CSV", csv_bytes, "depara.csv", "text/csv")

    if st.session_state[edit_key]:
        edited = st.data_editor(
            df_display,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            key=f"dp_editor_{cliente_id}",
        )
        if st.button("Salvar alterações", type="primary", key=f"dp_save_grid_{cliente_id}"):
            old_rows = get_depara(cliente_id)
            rows_to_save = edited.rename(columns={
                "Classificação financeira": "classif",
                "Conta contábil": "conta_contabil",
            }).to_dict("records")
            update_depara_batch(cliente_id, rows_to_save)
            _clear_depara_cache()

            # Computa diff para o log
            old_map = {r["classif"]: r["conta_contabil"] for r in old_rows}
            new_map = {
                str(r.get("classif", "")).strip(): str(r.get("conta_contabil", "")).strip()
                for r in rows_to_save
                if str(r.get("classif", "")).strip()
            }
            adicionadas = len([k for k in new_map if k not in old_map])
            removidas = len([k for k in old_map if k not in new_map])
            alteradas = len([k for k in new_map if k in old_map and new_map[k] != old_map[k]])
            detalhes = f"adicionadas={adicionadas};removidas={removidas};alteradas={alteradas}"

            log_depara_change(cliente_id, cliente, usuario_email, "BATCH_REPLACE", detalhes)
            log_acao(usuario_email, "DEPARA_ALTERADO", f"cliente={cliente};{detalhes}")

            st.session_state[edit_key] = False
            st.success("Base De x Para salva.")
            st.rerun()
    elif rows:
        st.dataframe(df_display, width="stretch", hide_index=True)
    else:
        st.info("Nenhuma regra De x Para cadastrada para este cliente.")

    st.divider()
    st.subheader("Adicionar regra manual")
    col1, col2 = st.columns(2)
    with col1:
        classif = st.text_input("Classificação financeira", key="dp_classif")
    with col2:
        conta = st.text_input("Conta contábil", key="dp_conta")
    if st.button("Adicionar regra", key="dp_add"):
        if not classif.strip():
            st.error("Informe a classificação financeira.")
        else:
            upsert_depara(cliente_id, classif.strip(), conta.strip())
            _clear_depara_cache()
            log_depara_change(
                cliente_id, cliente, usuario_email, "UPSERT_MANUAL",
                f"classif={classif.strip()};conta={conta.strip()}",
            )
            log_acao(usuario_email, "DEPARA_ALTERADO", f"cliente={cliente};classif={classif.strip()}")
            st.success("Regra salva.")
            st.rerun()

    st.divider()
    st.subheader("Importar planilha ou CSV")
    st.caption("Use a primeira coluna para a classificação financeira e a segunda para a conta contábil.")
    up = st.file_uploader("Upload", type=["csv", "xlsx", "xls"], key="dp_file_up")
    if up and st.button("Importar", key="dp_file_btn"):
        try:
            suffix = Path(up.name).suffix.lower()
            count = import_depara_stream(cliente_id, up.read(), suffix)
            _clear_depara_cache()
            log_depara_change(
                cliente_id, cliente, usuario_email, "IMPORTACAO",
                f"arquivo={up.name};registros={count}",
            )
            log_acao(usuario_email, "DEPARA_ALTERADO", f"cliente={cliente};importacao={up.name};registros={count}")
            st.success(f"{count} regra(s) importada(s).")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao importar De x Para: {e}")
    _render_depara_replication(cliente_id, cliente, cliente_label, rows, usuario_email, clientes)



# ── Minha Conta ────────────────────────────────────────────────────────────────

def minha_conta_page():
    email = st.session_state.get("usuario_email", "")
    perfil = st.session_state.get("usuario_perfil", "")
    nome = st.session_state.get("usuario_nome", "").strip()
    depto = st.session_state.get("usuario_departamento", "").strip()

    perfil_label = "Administrador" if perfil == "admin" else "Operacional"
    cor = "#FF9500" if perfil == "admin" else "#1565C0"
    display_name = nome if nome else email
    inicial = display_name[0].upper() if display_name else "?"
    depto_html = (
        f"<div style='font-size:0.74em;color:#888;margin-top:5px;"
        f"letter-spacing:0.06em;text-transform:uppercase'>{depto}</div>"
        if depto else ""
    )

    st.markdown(
        f"""<div style="border:1px solid rgba(255,255,255,0.07);border-radius:14px;
        padding:22px 28px;display:flex;align-items:center;gap:22px;
        margin-bottom:28px;background:rgba(255,255,255,0.04);">
            <div style="width:64px;height:64px;border-radius:50%;
                        background:linear-gradient(135deg,{cor},{cor}99);
                        display:flex;align-items:center;justify-content:center;
                        font-size:1.8em;font-weight:800;color:white;flex-shrink:0;
                        box-shadow:0 4px 16px {cor}55;">
                {inicial}
            </div>
            <div>
                <p style="margin:0 0 2px;font-size:1.0em;font-weight:700;
                          letter-spacing:0.04em;text-transform:uppercase;">
                    {display_name}
                </p>
                <p style="margin:0 0 8px;font-size:0.8em;color:#888;">{email}</p>
                <span style="background:{cor}1a;color:{cor};padding:3px 13px;
                             border-radius:20px;font-size:0.70em;font-weight:700;
                             border:1px solid {cor}44;letter-spacing:0.05em;
                             text-transform:uppercase;">
                    {perfil_label}
                </span>
                {depto_html}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    st.subheader("Alterar senha")

    with st.form("form_minha_conta"):
        atual = st.text_input("Senha atual", type="password", key="mc_atual")
        nova = st.text_input("Nova senha", type="password", key="mc_nova")
        confirma = st.text_input("Confirmar nova senha", type="password", key="mc_confirma")
        submitted = st.form_submit_button("Alterar senha", type="primary")

    if submitted:
        if len(nova) < 6:
            st.error("A nova senha deve ter pelo menos 6 caracteres.")
        elif nova != confirma:
            st.error("As senhas não coincidem.")
        elif change_password(email, atual, nova):
            log_acao(email, "TROCA_SENHA", "Senha alterada pelo próprio usuário")
            st.success("Senha alterada com sucesso!")
        else:
            st.error("Senha atual incorreta.")


# ── Estilos globais ────────────────────────────────────────────────────────────

def _inject_global_styles():
    st.markdown(
        """
        <style>
        /* Alinha todos os itens de uma linha de colunas pela base, garantindo que
           botões (sem label) fiquem nivelados com inputs, selects e outros controles
           que possuem label acima. */
        div[data-testid="stHorizontalBlock"] {
            align-items: flex-end;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    _inject_global_styles()

    # Gate de autenticação
    if "usuario_email" not in st.session_state:
        authenticated = show_login()
        if authenticated:
            st.rerun()
        return

    # Troca de senha obrigatória (após reset pelo admin)
    if st.session_state.get("troca_senha"):
        show_change_password_required()
        return

    # App normal
    pagina = sidebar()

    if pagina == "Conciliação Contábil":
        wizard_page()
    elif pagina == "De x Para Geral":
        depara_page()
    elif pagina == "Minha Conta":
        minha_conta_page()
    elif pagina == "Configurações Gerais":
        if st.session_state.get("usuario_perfil") == "admin":
            show_admin_panel()
        else:
            st.error("Acesso restrito a administradores.")


if __name__ == "__main__":
    main()
