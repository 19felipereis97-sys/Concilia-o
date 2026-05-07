"""
Etapas do wizard: mapeamento de colunas e parâmetros.
"""
from __future__ import annotations
import io
import json
import streamlit as st

from core.io_excel import get_columns
from core.mapping import (
    ExtratoMapping, FinanceiroMapping,
    ValorModalidade, FinanceiroModalidade,
)
from core.params import ConciliacaoParams
from core.wizard_persistence import get_wizard_config_snapshot, save_wizard_config
from plan.client_store import log_acao, upsert_conciliacao_template


@st.cache_data(show_spinner=False)
def _cached_get_columns(raw: bytes, sheet_name, skip_rows: int, suffix: str) -> list:
    return get_columns(io.BytesIO(raw), sheet_name=sheet_name, skip_rows=skip_rows, suffix=suffix)

_MODALIDADE_STR_MAP = {
    "COMPLETO":      FinanceiroModalidade.COMPLETO,
    "RECEBIMENTOS":  FinanceiroModalidade.RECEBIMENTOS,
    "PAGAMENTOS":    FinanceiroModalidade.PAGAMENTOS,
    "SEPARADOS":     FinanceiroModalidade.SEPARADOS,
}


def _get_cols(file_key: str, sheet_key: str, skip_key: str, suffix_key: str):
    raw = st.session_state.get(file_key)
    if raw is None:
        st.warning(f"Arquivo não encontrado em session_state['{file_key}']. Volte e recarregue o arquivo.")
        return []
    suffix = st.session_state.get(suffix_key, ".xlsx")
    sheet = st.session_state.get(sheet_key)
    skip = st.session_state.get(skip_key, 0)
    if sheet is None and suffix != ".csv":
        st.warning(f"Aba não configurada ('{sheet_key}' ausente). Volte à etapa de configuração.")
    try:
        return _cached_get_columns(raw, sheet, skip, suffix)
    except Exception as e:
        st.error(f"Erro ao ler colunas de '{file_key}' (sheet={sheet!r}, skip={skip}): {e}")
        return []


def step_mapping_extrato() -> bool:
    st.subheader("Etapa 5 — Mapeamento do Extrato Bancário")
    cols = _get_cols("extrato_file", "extrato_sheet", "extrato_skip", "extrato_suffix")
    if not cols:
        st.warning("Não foi possível ler colunas do extrato. Revise as etapas anteriores.")
        return False

    opcoes = ["(não mapeado)"] + cols

    col_data = st.selectbox("Coluna de DATA", opcoes, key="bnk_col_data")
    hist_cols = st.multiselect("Colunas de HISTÓRICO (podem ser várias)", cols, key="bnk_col_hist")
    valor_mod = st.radio(
        "Formato do valor",
        ["Coluna única (com sinal)", "Duas colunas (pagamentos e recebimentos)"],
        key="bnk_valor_mod",
    )

    col_valor = col_deb = col_cre = None
    if valor_mod.startswith("Coluna única"):
        col_valor = st.selectbox("Coluna de VALOR", opcoes, key="bnk_col_valor")
    else:
        col_deb = st.selectbox("Coluna de PAGAMENTOS / SAÍDAS (será negativo)", opcoes, key="bnk_col_deb")
        col_cre = st.selectbox("Coluna de RECEBIMENTOS / ENTRADAS (será positivo)", opcoes, key="bnk_col_cre")

    if col_data == "(não mapeado)" or not hist_cols:
        st.info("Selecione ao menos a coluna de data e uma coluna de histórico para continuar.")
        return False
    if valor_mod.startswith("Coluna única") and col_valor == "(não mapeado)":
        st.info("Selecione a coluna de valor para continuar.")
        return False
    if not valor_mod.startswith("Coluna única") and (
        col_deb == "(não mapeado)" or col_cre == "(não mapeado)"
    ):
        st.info("Selecione as colunas de pagamentos/saídas e recebimentos/entradas para continuar.")
        return False

    mod = ValorModalidade.COLUNA_UNICA if valor_mod.startswith("Coluna única") else ValorModalidade.DOIS_COLUNAS
    mapping = ExtratoMapping(
        col_data=col_data,
        col_historico=hist_cols,
        valor_modalidade=mod,
        col_valor=col_valor if col_valor != "(não mapeado)" else None,
        col_debito=col_deb if col_deb and col_deb != "(não mapeado)" else None,
        col_credito=col_cre if col_cre and col_cre != "(não mapeado)" else None,
        skip_rows=st.session_state.get("extrato_skip", 0),
        sheet_name=st.session_state.get("extrato_sheet"),
    )
    st.session_state["extrato_mapping"] = mapping
    return True


def step_financeiro_modalidade() -> FinanceiroModalidade:
    """Retorna a modalidade já escolhida na etapa de upload (Etapa 2)."""
    modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")
    return _MODALIDADE_STR_MAP.get(modalidade_str, FinanceiroModalidade.COMPLETO)


def _build_fin_mapping_ui(
    cols: list,
    prefix: str,
    modalidade: FinanceiroModalidade,
    mapping_key: str,
    skip_key: str,
    sheet_key: str,
) -> bool:
    """Renderiza UI de mapeamento para um arquivo financeiro e salva o mapping."""
    opcoes = ["(não mapeado)"] + cols

    col_data = st.selectbox("Coluna de DATA", opcoes, key=f"{prefix}col_data")
    hist_cols = st.multiselect("Colunas de HISTÓRICO", cols, key=f"{prefix}col_hist")
    hist_prefix_val = st.text_input("Prefixo do histórico (opcional)", key=f"{prefix}hist_prefix", value="")
    hist_sep = st.text_input("Separador do histórico", key=f"{prefix}hist_sep", value=" - ")

    valor_mod = st.radio(
        "Formato do valor",
        ["Coluna única", "Duas colunas (pagamentos e recebimentos)"],
        key=f"{prefix}valor_mod",
    )
    col_valor = col_deb = col_cre = None
    if valor_mod == "Coluna única":
        col_valor = st.selectbox("Coluna de VALOR", opcoes, key=f"{prefix}col_valor")
    else:
        col_deb = st.selectbox("Coluna de PAGAMENTOS / SAÍDAS (será negativo)", opcoes, key=f"{prefix}col_deb")
        col_cre = st.selectbox("Coluna de RECEBIMENTOS / ENTRADAS (será positivo)", opcoes, key=f"{prefix}col_cre")

    col_classif = st.selectbox("Coluna de CLASSIFICAÇÃO CONTÁBIL (opcional)", opcoes, key=f"{prefix}col_classif")

    if col_data == "(não mapeado)" or not hist_cols:
        st.info("Selecione ao menos a coluna de data e uma de histórico.")
        return False
    if valor_mod == "Coluna única" and col_valor == "(não mapeado)":
        st.info("Selecione a coluna de valor para continuar.")
        return False
    if valor_mod != "Coluna única" and (
        col_deb == "(não mapeado)" or col_cre == "(não mapeado)"
    ):
        st.info("Selecione as colunas de pagamentos/saídas e recebimentos/entradas para continuar.")
        return False

    mod = ValorModalidade.COLUNA_UNICA if valor_mod == "Coluna única" else ValorModalidade.DOIS_COLUNAS
    mapping = FinanceiroMapping(
        col_data=col_data,
        col_historico=hist_cols,
        valor_modalidade=mod,
        col_valor=col_valor if col_valor and col_valor != "(não mapeado)" else None,
        col_debito=col_deb if col_deb and col_deb != "(não mapeado)" else None,
        col_credito=col_cre if col_cre and col_cre != "(não mapeado)" else None,
        col_classificacao=col_classif if col_classif != "(não mapeado)" else None,
        skip_rows=st.session_state.get(skip_key, 0),
        sheet_name=st.session_state.get(sheet_key),
        modalidade=modalidade,
        hist_prefix=hist_prefix_val,
        hist_separator=hist_sep or " - ",
    )
    st.session_state[mapping_key] = mapping
    return True


def step_mapping_financeiro(modalidade: FinanceiroModalidade) -> bool:
    st.subheader("Etapa 6 — Mapeamento do Financeiro")

    if modalidade == FinanceiroModalidade.SEPARADOS:
        st.info("Configure o mapeamento para cada arquivo separadamente.")

        st.markdown("#### Recebimentos")
        cols1 = _get_cols("fin_file", "fin_sheet", "fin_skip", "fin_suffix")
        if not cols1:
            st.warning("Não foi possível ler colunas do arquivo de recebimentos.")
            return False
        ok1 = _build_fin_mapping_ui(
            cols1, prefix="rec_",
            modalidade=FinanceiroModalidade.RECEBIMENTOS,
            mapping_key="fin_mapping",
            skip_key="fin_skip",
            sheet_key="fin_sheet",
        )

        st.divider()

        st.markdown("#### Pagamentos")
        cols2 = _get_cols("fin2_file", "fin2_sheet", "fin2_skip", "fin2_suffix")
        if not cols2:
            st.warning("Não foi possível ler colunas do arquivo de pagamentos.")
            return False
        ok2 = _build_fin_mapping_ui(
            cols2, prefix="pag_",
            modalidade=FinanceiroModalidade.PAGAMENTOS,
            mapping_key="fin2_mapping",
            skip_key="fin2_skip",
            sheet_key="fin2_sheet",
        )

        return ok1 and ok2

    # Modalidade de arquivo único
    cols = _get_cols("fin_file", "fin_sheet", "fin_skip", "fin_suffix")
    if not cols:
        st.warning("Não foi possível ler colunas do financeiro.")
        return False

    return _build_fin_mapping_ui(
        cols, prefix="fin_",
        modalidade=modalidade,
        mapping_key="fin_mapping",
        skip_key="fin_skip",
        sheet_key="fin_sheet",
    )


def step_params() -> ConciliacaoParams:
    st.subheader("Etapa 7 — Parâmetros da Conciliação")
    with st.expander("Tolerâncias e janela de datas", expanded=False):
        tol = st.number_input("Tolerância de valor (centavos)", min_value=0, max_value=100, value=0, key="param_tol")
        max_group = st.number_input(
            "Tamanho máximo do grupo (1:N)",
            min_value=2, value=9999, key="param_group",
            help="Máximo de lançamentos financeiros que podem se combinar num único pareamento.",
        )
        max_candidates = st.number_input(
            "Candidatos maximos por grupo (1:N)",
            min_value=2, value=9999, key="param_max_candidates",
            help="Limite de candidatos avaliados em cada busca 1:N. Reduza para acelerar bases grandes.",
        )
        enable_n_to_one = st.checkbox(
            "Ativar confronto N:1 (varios bancos para um financeiro)",
            value=False,
            key="param_enable_n_to_one",
            help="Use somente quando o extrato agrupa ou divide pagamentos de forma que varios lancamentos bancarios devam fechar um financeiro.",
        )
        n_to_one_candidates = st.number_input(
            "Candidatos maximos por grupo (N:1)",
            min_value=2, value=int(max_candidates), key="param_n_to_one_candidates",
            help="Limite de candidatos bancarios avaliados em cada busca N:1.",
            disabled=not enable_n_to_one,
        )
        combo_timeout = st.number_input(
            "Tempo máximo por busca combinatória (s)",
            min_value=0.0, max_value=60.0, value=1.0, step=0.5, key="param_combo_timeout",
            help="Use 0 para não interromper a busca por tempo.",
        )
        offsets_str = st.text_input(
            "Offsets de data (vírgula, ex: 0,1,-1,2,-2)",
            value="0,1,-1,2,-2",
            key="param_offsets",
        )
        try:
            offsets = [int(x.strip()) for x in offsets_str.split(",") if x.strip()]
        except ValueError:
            offsets = [0, 1, -1, 2, -2]

    with st.expander("Padrões de descarte", expanded=False):
        patterns_str = st.text_area(
            "Prefixos de histórico a ignorar (um por linha)",
            value="SDO\nSALDO\nS/D\nSALDO ANTERIOR\nSALDO DO DIA",
            key="param_discard",
        )
        patterns = [p.strip() for p in patterns_str.splitlines() if p.strip()]

    # Separador preferido para params — usa rec_ se SEPARADOS, senão fin_
    modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")
    if modalidade_str == "SEPARADOS":
        hist_sep = st.session_state.get("rec_hist_sep", " - ")
        hist_pfx = st.session_state.get("rec_hist_prefix", "")
    else:
        hist_sep = st.session_state.get("fin_hist_sep", " - ")
        hist_pfx = st.session_state.get("fin_hist_prefix", "")

    params = ConciliacaoParams(
        date_offsets=offsets,
        max_group_size=int(max_group),
        max_candidates_per_group=int(max_candidates),
        n_to_one_max_candidates=int(n_to_one_candidates),
        value_tolerance_cents=int(tol),
        combo_timeout_sec=float(combo_timeout),
        discard_patterns=patterns,
        hist_separator=hist_sep or " - ",
        hist_prefix=hist_pfx,
        default_year=int(st.session_state.get("default_year", 0)),
        enable_n_to_one=bool(enable_n_to_one),
    )
    st.session_state["params"] = params
    save_wizard_config(st.session_state)
    _save_cliente_banco_template()
    return params


def _save_cliente_banco_template() -> None:
    cliente_id = st.session_state.get("cliente_conciliacao_id")
    if not cliente_id:
        return
    banco_nome = str(st.session_state.get("banco_layout_conciliacao", "")).strip()
    if not banco_nome:
        return

    usuario = st.session_state.get("usuario_email", "")
    snapshot = get_wizard_config_snapshot(st.session_state)
    signature = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    sig_key = f"_template_saved_sig_{cliente_id}_{banco_nome}"
    if st.session_state.get(sig_key) == signature:
        return

    template_id = upsert_conciliacao_template(
        int(cliente_id),
        banco_nome,
        "Padrao",
        snapshot,
        usuario,
    )
    st.session_state[sig_key] = signature
    if not st.session_state.get(f"_template_saved_notice_{template_id}"):
        st.caption(f"Template salvo para este cliente/banco: {banco_nome}.")
        st.session_state[f"_template_saved_notice_{template_id}"] = True
    log_acao(
        usuario or "desconhecido",
        "TEMPLATE_CONCILIACAO_SALVO",
        f"cliente_id={cliente_id};template_id={template_id};banco={banco_nome}",
    )
