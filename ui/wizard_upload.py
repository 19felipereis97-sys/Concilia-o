"""
Etapas do wizard: upload e configuração de cabeçalho.
"""
from __future__ import annotations
import io
import datetime
import streamlit as st
import pandas as pd

from core.io_excel import get_sheet_names, preview_raw, get_columns


def _to_display(df: pd.DataFrame) -> pd.DataFrame:
    """Converte colunas object para str antes de passar ao st.dataframe.
    Formata datetime como DD/MM/YYYY para evitar ArrowTypeError no Streamlit."""
    def _fmt(v):
        if isinstance(v, datetime.datetime):
            return v.strftime("%d/%m/%Y")
        if isinstance(v, datetime.date):
            return v.strftime("%d/%m/%Y")
        return v
    return df.apply(lambda col: col.map(_fmt) if col.dtype == object else col)


def step_upload_extrato():
    st.subheader("Etapa 1 — Upload do Extrato Bancário")
    st.info("Carregue o arquivo do extrato bancário (.xlsx, .xls, .csv).")
    file = st.file_uploader(
        "Extrato Bancário",
        type=["xlsx", "xls", "csv"],
        key="file_extrato",
    )
    if file is not None:
        suffix = "." + file.name.rsplit(".", 1)[-1].lower()
        st.session_state["extrato_file"] = file.read()
        st.session_state["extrato_suffix"] = suffix
        st.session_state["extrato_name"] = file.name
        st.success(f"Arquivo carregado: {file.name}")
        return True
    return False


def step_upload_financeiro():
    st.subheader("Etapa 2 — Upload do Financeiro")

    modo_label = st.radio(
        "O movimento financeiro está em:",
        [
            "Arquivo único com Receitas e Despesas",
            "Arquivo único — Somente Recebimentos",
            "Arquivo único — Somente Pagamentos",
            "Dois arquivos separados (Recebimentos e Pagamentos)",
        ],
        key="fin_upload_modo",
    )

    if modo_label == "Dois arquivos separados (Recebimentos e Pagamentos)":
        st.session_state["fin_modalidade_str"] = "SEPARADOS"
    elif "Recebimentos" in modo_label:
        st.session_state["fin_modalidade_str"] = "RECEBIMENTOS"
    elif "Pagamentos" in modo_label:
        st.session_state["fin_modalidade_str"] = "PAGAMENTOS"
    else:
        st.session_state["fin_modalidade_str"] = "COMPLETO"

    if st.session_state["fin_modalidade_str"] == "SEPARADOS":
        st.info("Carregue os dois arquivos: um de recebimentos e um de pagamentos.")
        col1, col2 = st.columns(2)

        with col1:
            st.caption("Arquivo de Recebimentos")
            f1 = st.file_uploader(
                "Recebimentos",
                type=["xlsx", "xls", "csv"],
                key="file_fin_rec",
                label_visibility="collapsed",
            )
            if f1 is not None:
                st.session_state["fin_file"] = f1.read()
                st.session_state["fin_suffix"] = "." + f1.name.rsplit(".", 1)[-1].lower()
                st.session_state["fin_name"] = f1.name
                st.success(f"Recebimentos: {f1.name}")
            elif "fin_name" in st.session_state and st.session_state.get("fin_modalidade_str") == "SEPARADOS":
                st.info(f"Carregado anteriormente: {st.session_state.get('fin_name', '')}")

        with col2:
            st.caption("Arquivo de Pagamentos")
            f2 = st.file_uploader(
                "Pagamentos",
                type=["xlsx", "xls", "csv"],
                key="file_fin_pag",
                label_visibility="collapsed",
            )
            if f2 is not None:
                st.session_state["fin2_file"] = f2.read()
                st.session_state["fin2_suffix"] = "." + f2.name.rsplit(".", 1)[-1].lower()
                st.session_state["fin2_name"] = f2.name
                st.success(f"Pagamentos: {f2.name}")
            elif "fin2_name" in st.session_state:
                st.info(f"Carregado anteriormente: {st.session_state.get('fin2_name', '')}")

        ok1 = "fin_file" in st.session_state
        ok2 = "fin2_file" in st.session_state
        if not ok1 or not ok2:
            missing = []
            if not ok1:
                missing.append("recebimentos")
            if not ok2:
                missing.append("pagamentos")
            st.warning(f"Aguardando arquivo(s): {', '.join(missing)}.")
        return ok1 and ok2
    else:
        st.info("Carregue o arquivo financeiro (contas a pagar/receber, SISPAG, etc.).")
        file = st.file_uploader(
            "Sistema Financeiro",
            type=["xlsx", "xls", "csv"],
            key="file_financeiro",
        )
        if file is not None:
            suffix = "." + file.name.rsplit(".", 1)[-1].lower()
            st.session_state["fin_file"] = file.read()
            st.session_state["fin_suffix"] = suffix
            st.session_state["fin_name"] = file.name
            st.success(f"Arquivo carregado: {file.name}")
            return True
        if "fin_file" in st.session_state:
            st.info(f"Carregado anteriormente: {st.session_state.get('fin_name', '')}")
            return True
        return False


def step_header_config_extrato():
    st.subheader("Etapa 3 — Configuração do Extrato")
    if "extrato_file" not in st.session_state:
        st.warning("Nenhum arquivo de extrato carregado.")
        return False

    raw = st.session_state["extrato_file"]
    suffix = st.session_state.get("extrato_suffix", ".xlsx")
    file_obj = io.BytesIO(raw)

    if suffix != ".csv":
        try:
            sheets = get_sheet_names(file_obj, suffix=suffix)
        except Exception as e:
            st.error(f"Não foi possível ler o arquivo do extrato: {e}")
            return False
        if "tmp_extrato_sheet" not in st.session_state and "extrato_sheet" in st.session_state:
            st.session_state["tmp_extrato_sheet"] = st.session_state["extrato_sheet"]
        sheet = st.selectbox("Aba do extrato", sheets, key="tmp_extrato_sheet")
        st.session_state["extrato_sheet"] = sheet
    else:
        sheet = "csv"
        st.session_state["extrato_sheet"] = sheet

    if "tmp_extrato_skip" not in st.session_state and "extrato_skip" in st.session_state:
        st.session_state["tmp_extrato_skip"] = int(st.session_state["extrato_skip"])
    skip = st.number_input("Linhas de cabeçalho a ignorar (antes do header real)", min_value=0, max_value=20, value=0, key="tmp_extrato_skip")
    st.session_state["extrato_skip"] = int(skip)

    _this_year = datetime.date.today().year
    if "tmp_default_year" not in st.session_state and "default_year" in st.session_state:
        st.session_state["tmp_default_year"] = int(st.session_state["default_year"])
    ano = st.number_input("Ano padrão (para datas no formato DD/MM sem ano)", min_value=2000, max_value=2099, value=_this_year, key="tmp_default_year")
    st.session_state["default_year"] = int(ano)

    file_obj.seek(0)
    try:
        preview = preview_raw(file_obj, sheet_name=sheet if sheet != "csv" else None, suffix=suffix, n_rows=8)
        st.write("**Pré-visualização (primeiras 8 linhas brutas):**")
        st.dataframe(_to_display(preview), width='stretch')
    except Exception as e:
        st.error(f"Erro ao ler preview: {e}")
        return False

    return True


def _config_fin_single(
    file_key: str,
    suffix_key: str,
    sheet_key: str,
    skip_key: str,
    tmp_sheet_key: str,
    tmp_skip_key: str,
    label: str = "",
) -> bool:
    """Configura aba e skip de um arquivo financeiro. Retorna True se ok."""
    raw = st.session_state.get(file_key)
    if raw is None:
        st.warning(f"Arquivo '{label}' não carregado.")
        return False

    suffix = st.session_state.get(suffix_key, ".xlsx")
    file_obj = io.BytesIO(raw)

    if suffix != ".csv":
        try:
            sheets = get_sheet_names(file_obj, suffix=suffix)
        except Exception as e:
            st.error(f"Não foi possível ler o arquivo{' — ' + label if label else ''}: {e}")
            return False
        if tmp_sheet_key not in st.session_state and sheet_key in st.session_state:
            st.session_state[tmp_sheet_key] = st.session_state[sheet_key]
        caption = f"Aba — {label}" if label else "Aba"
        sheet = st.selectbox(caption, sheets, key=tmp_sheet_key)
        st.session_state[sheet_key] = sheet
    else:
        sheet = "csv"
        st.session_state[sheet_key] = sheet

    if tmp_skip_key not in st.session_state and skip_key in st.session_state:
        st.session_state[tmp_skip_key] = int(st.session_state[skip_key])
    caption_skip = f"Linhas de cabeçalho a ignorar — {label}" if label else "Linhas de cabeçalho a ignorar"
    skip = st.number_input(caption_skip, min_value=0, max_value=20, value=0, key=tmp_skip_key)
    st.session_state[skip_key] = int(skip)

    file_obj.seek(0)
    try:
        preview = preview_raw(file_obj, sheet_name=sheet if sheet != "csv" else None, suffix=suffix, n_rows=8)
        title = f"**Pré-visualização — {label}:**" if label else "**Pré-visualização:**"
        st.write(title)
        st.dataframe(_to_display(preview), width='stretch')
    except Exception as e:
        st.error(f"Erro ao ler preview ({label}): {e}")
        return False

    return True


def step_header_config_financeiro():
    st.subheader("Etapa 4 — Configuração do Financeiro")
    if "fin_file" not in st.session_state:
        st.warning("Nenhum arquivo financeiro carregado.")
        return False

    modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")

    if modalidade_str == "SEPARADOS":
        if "fin2_file" not in st.session_state:
            st.warning("Arquivo de pagamentos não carregado. Volte à etapa 2.")
            return False

        ok1 = _config_fin_single(
            "fin_file", "fin_suffix", "fin_sheet", "fin_skip",
            "tmp_fin_sheet", "tmp_fin_skip", "Recebimentos",
        )
        st.divider()
        ok2 = _config_fin_single(
            "fin2_file", "fin2_suffix", "fin2_sheet", "fin2_skip",
            "tmp_fin2_sheet", "tmp_fin2_skip", "Pagamentos",
        )

        _this_year = datetime.date.today().year
        if "tmp_default_year" not in st.session_state and "default_year" in st.session_state:
            st.session_state["tmp_default_year"] = int(st.session_state["default_year"])
        ano = st.number_input("Ano padrão (para datas no formato DD/MM sem ano)", min_value=2000, max_value=2099, value=_this_year, key="tmp_default_year")
        st.session_state["default_year"] = int(ano)

        return ok1 and ok2
    else:
        raw = st.session_state["fin_file"]
        suffix = st.session_state.get("fin_suffix", ".xlsx")
        file_obj = io.BytesIO(raw)

        if suffix != ".csv":
            try:
                sheets = get_sheet_names(file_obj, suffix=suffix)
            except Exception as e:
                st.error(f"Não foi possível ler o arquivo financeiro: {e}")
                return False
            if "tmp_fin_sheet" not in st.session_state and "fin_sheet" in st.session_state:
                st.session_state["tmp_fin_sheet"] = st.session_state["fin_sheet"]
            sheet = st.selectbox("Aba do financeiro", sheets, key="tmp_fin_sheet")
            st.session_state["fin_sheet"] = sheet
        else:
            sheet = "csv"
            st.session_state["fin_sheet"] = sheet

        if "tmp_fin_skip" not in st.session_state and "fin_skip" in st.session_state:
            st.session_state["tmp_fin_skip"] = int(st.session_state["fin_skip"])
        skip = st.number_input("Linhas de cabeçalho a ignorar", min_value=0, max_value=20, value=0, key="tmp_fin_skip")
        st.session_state["fin_skip"] = int(skip)

        _this_year = datetime.date.today().year
        if "tmp_default_year" not in st.session_state and "default_year" in st.session_state:
            st.session_state["tmp_default_year"] = int(st.session_state["default_year"])
        ano = st.number_input("Ano padrão (para datas no formato DD/MM sem ano)", min_value=2000, max_value=2099, value=_this_year, key="tmp_default_year")
        st.session_state["default_year"] = int(ano)

        file_obj.seek(0)
        try:
            preview = preview_raw(file_obj, sheet_name=sheet if sheet != "csv" else None, suffix=suffix, n_rows=8)
            st.write("**Pré-visualização:**")
            st.dataframe(_to_display(preview), width='stretch')
        except Exception as e:
            st.error(f"Erro ao ler preview: {e}")
            return False

        return True
