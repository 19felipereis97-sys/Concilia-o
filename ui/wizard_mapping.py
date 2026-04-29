"""
Etapas do wizard: mapeamento de colunas e parâmetros.
"""
from __future__ import annotations
import io
import streamlit as st

from core.io_excel import get_columns
from core.mapping import (
    ExtratoMapping, FinanceiroMapping,
    ValorModalidade, FinanceiroModalidade,
)
from core.params import ConciliacaoParams

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
        cols = get_columns(io.BytesIO(raw), sheet_name=sheet, skip_rows=skip, suffix=suffix)
        return cols
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
        ["Coluna única (com sinal ou positivo=crédito)", "Duas colunas (débito e crédito)"],
        key="bnk_valor_mod",
    )

    col_valor = col_deb = col_cre = None
    if valor_mod.startswith("Coluna única"):
        col_valor = st.selectbox("Coluna de VALOR", opcoes, key="bnk_col_valor")
    else:
        col_deb = st.selectbox("Coluna de DÉBITO", opcoes, key="bnk_col_deb")
        col_cre = st.selectbox("Coluna de CRÉDITO", opcoes, key="bnk_col_cre")

    if col_data == "(não mapeado)" or not hist_cols:
        st.info("Selecione ao menos a coluna de data e uma coluna de histórico para continuar.")
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
        ["Coluna única", "Duas colunas (débito e crédito)"],
        key=f"{prefix}valor_mod",
    )
    col_valor = col_deb = col_cre = None
    if valor_mod == "Coluna única":
        col_valor = st.selectbox("Coluna de VALOR", opcoes, key=f"{prefix}col_valor")
    else:
        col_deb = st.selectbox("Coluna de DÉBITO", opcoes, key=f"{prefix}col_deb")
        col_cre = st.selectbox("Coluna de CRÉDITO", opcoes, key=f"{prefix}col_cre")

    col_classif = st.selectbox("Coluna de CLASSIFICAÇÃO CONTÁBIL (opcional)", opcoes, key=f"{prefix}col_classif")

    if col_data == "(não mapeado)" or not hist_cols:
        st.info("Selecione ao menos a coluna de data e uma de histórico.")
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
            "Tamanho máximo do grupo (N:1 / 1:N)",
            min_value=2, max_value=15, value=5, key="param_group",
            help="Máximo de lançamentos que podem se combinar num único pareamento.",
        )
        max_cand = st.number_input(
            "Candidatos máximos por grupo",
            min_value=5, max_value=50, value=30, key="param_max_cand",
            help="Limita o pool combinatório por data/sinal. Mantenha ≤ 30 ao usar grupos grandes (≥ 10).",
        )
        if int(max_group) >= 10 and int(max_cand) > 30:
            st.warning("Grupo ≥ 10 com candidatos > 30 pode tornar a conciliação lenta em planilhas grandes.")
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
        max_candidates_per_group=int(max_cand),
        value_tolerance_cents=int(tol),
        discard_patterns=patterns,
        hist_separator=hist_sep or " - ",
        hist_prefix=hist_pfx,
        default_year=int(st.session_state.get("default_year", 0)),
    )
    st.session_state["params"] = params
    return params
