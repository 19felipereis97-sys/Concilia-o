"""
Aplicativo principal Streamlit — Sistema Universal de Conciliação Bancária.
"""
from __future__ import annotations
import io
import streamlit as st
import pandas as pd

# ── configuração da página ────────────────────────────────────────────────────
st.set_page_config(
    page_title="Conciliador Bancário",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── imports internos ──────────────────────────────────────────────────────────
from plan.client_store import (
    init_db, list_clientes, create_cliente,
    get_cliente_id, get_depara, upsert_depara, delete_depara,
    import_depara_csv,
)
from plan.planilha_contabil import export_depara_csv, get_depara_dict
from core.normalize import normalize_extrato, normalize_financeiro
from core.engine import run_engine
from core.manual_review import build_review_queue, apply_review_decisions
from core.report_builder import build_report
from ui.wizard_upload import (
    step_upload_extrato, step_upload_financeiro,
    step_header_config_extrato, step_header_config_financeiro,
)
from ui.wizard_mapping import (
    step_mapping_extrato, step_financeiro_modalidade,
    step_mapping_financeiro, step_params,
)
from ui.wizard_review import step_review
from ui.components import fmt_valor, fmt_data, progress_bar

init_db()

# ── sidebar ───────────────────────────────────────────────────────────────────

def sidebar():
    with st.sidebar:
        pagina = st.radio(
            "Navegação",
            ["Nova Conciliação", "De-Para Contábil"],
            key="nav_pagina",
        )

        st.divider()
        clientes = list_clientes()
        if clientes:
            cliente = st.selectbox("Cliente", clientes, key="cliente_sel")
        else:
            st.info("Nenhum cliente cadastrado.")
            cliente = None

        novo_cliente = st.text_input("Novo cliente", key="novo_cliente")
        if st.button("Adicionar cliente") and novo_cliente:
            if create_cliente(novo_cliente):
                st.success(f"Cliente '{novo_cliente}' criado.")
                st.rerun()
            else:
                st.warning("Cliente já existe.")

        return pagina, cliente


# ── wizard de conciliação ─────────────────────────────────────────────────────

def wizard_page(cliente):
    st.title("Nova Conciliação")

    if "wiz_step" not in st.session_state:
        st.session_state["wiz_step"] = 1

    step = st.session_state["wiz_step"]
    progress_bar(step, 8)

    if step == 1:
        ok = step_upload_extrato()
        if ok and st.button("Próximo", key="wiz_1_next"):
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
            st.session_state["wiz_step"] = 6
            st.rerun()

    elif step == 6:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 5; st.rerun()
        modalidade = step_financeiro_modalidade()
        ok = step_mapping_financeiro(modalidade)
        if ok and st.button("Próximo", key="wiz_6_next"):
            st.session_state["wiz_step"] = 7
            st.rerun()

    elif step == 7:
        col1, _ = st.columns([1, 4])
        with col1:
            if st.button("Voltar"):
                st.session_state["wiz_step"] = 6; st.rerun()
        step_params()
        if st.button("Executar Conciliação", key="wiz_7_run", type="primary"):
            _executar_conciliacao(cliente)

    elif step == 8:
        _etapa_revisao_download(cliente)


def _compute_balance_warning(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    modalidade_str: str,
) -> dict:
    """
    Compara totais de débito/crédito entre extrato e financeiro.
    Retorna dict com os totais e flag de divergência para exibição na Etapa 8.
    """
    bnk_deb = float(df_bnk.loc[df_bnk["_valor"] < 0, "_valor"].sum())
    bnk_cre = float(df_bnk.loc[df_bnk["_valor"] > 0, "_valor"].sum())
    fin_neg = float(df_fin.loc[df_fin["_valor"] < 0, "_valor"].sum())
    fin_pos = float(df_fin.loc[df_fin["_valor"] > 0, "_valor"].sum())

    resultado = {
        "bnk_deb": bnk_deb,
        "bnk_cre": bnk_cre,
        "fin_neg": fin_neg,
        "fin_pos": fin_pos,
        "modalidade": modalidade_str,
        "diverge_deb": False,
        "diverge_cre": False,
    }

    tol_rel = 0.01  # 1% de tolerância relativa para emitir aviso
    if bnk_deb != 0 and abs(fin_neg) > 0:
        resultado["diverge_deb"] = abs((bnk_deb - fin_neg) / bnk_deb) > tol_rel
    if bnk_cre != 0 and fin_pos > 0:
        resultado["diverge_cre"] = abs((bnk_cre - fin_pos) / bnk_cre) > tol_rel

    return resultado


def _executar_conciliacao(cliente):
    with st.spinner("Normalizando e conciliando..."):
        try:
            extrato_bytes = st.session_state["extrato_file"]
            ext_suffix = st.session_state.get("extrato_suffix", ".xlsx")
            ext_mapping = st.session_state["extrato_mapping"]
            params = st.session_state["params"]

            df_bnk = normalize_extrato(io.BytesIO(extrato_bytes), ext_mapping, params, suffix=ext_suffix)

            modalidade_str = st.session_state.get("fin_modalidade_str", "COMPLETO")
            fin_bytes = st.session_state["fin_file"]
            fin_suffix = st.session_state.get("fin_suffix", ".xlsx")
            fin_mapping = st.session_state["fin_mapping"]

            df_fin = normalize_financeiro(io.BytesIO(fin_bytes), fin_mapping, params, suffix=fin_suffix)

            if modalidade_str == "SEPARADOS":
                fin2_bytes = st.session_state["fin2_file"]
                fin2_suffix = st.session_state.get("fin2_suffix", ".xlsx")
                fin2_mapping = st.session_state["fin2_mapping"]
                df_fin2 = normalize_financeiro(io.BytesIO(fin2_bytes), fin2_mapping, params, suffix=fin2_suffix)
                # Garante IDs únicos: desloca IDs auto-gerados do segundo arquivo
                n_fin1 = len(df_fin)
                df_fin2["_id"] = [
                    f"FIN_{int(x.rsplit('_', 1)[-1]) + n_fin1:04d}" if x.startswith("FIN_") else x
                    for x in df_fin2["_id"]
                ]
                df_fin = pd.concat([df_fin, df_fin2], ignore_index=True)

            st.session_state["balance_warning"] = _compute_balance_warning(
                df_bnk, df_fin, modalidade_str
            )

            df_bnk, df_fin = run_engine(df_bnk, df_fin, params)

            st.session_state["df_bnk"] = df_bnk
            st.session_state["df_fin"] = df_fin

            cards = build_review_queue(df_bnk, df_fin, params)
            st.session_state["review_cards"] = cards

            st.session_state["wiz_step"] = 8
            st.rerun()
        except Exception as e:
            st.error(f"Erro durante a conciliação: {e}")
            import traceback; st.text(traceback.format_exc())


def _etapa_revisao_download(cliente):
    df_bnk = st.session_state.get("df_bnk", pd.DataFrame())
    df_fin = st.session_state.get("df_fin", pd.DataFrame())
    cards = st.session_state.get("review_cards", [])

    col1, col2, col3, col4 = st.columns(4)
    if not df_bnk.empty and "_status" in df_bnk.columns:
        conciliados = (df_bnk["_status"] == "CONCILIADO").sum() + (df_bnk["_status"] == "CONCILIADO_MANUAL").sum()
        sem_par = (df_bnk["_status"] == "SEM_PAREAMENTO").sum()
        revisar = df_bnk["_status"].isin(["REVISAR", "REVISAR_COLISAO"]).sum()
        col1.metric("Conciliados", int(conciliados))
        col2.metric("Sem par", int(sem_par))
        col3.metric("A revisar", int(revisar))
        col4.metric("Total banco", len(df_bnk))

    bw = st.session_state.get("balance_warning")
    if bw:
        with st.expander("Comparativo de saldos (Extrato vs Financeiro)", expanded=bw["diverge_deb"] or bw["diverge_cre"]):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Débitos banco", f"R$ {bw['bnk_deb']:,.2f}")
            c2.metric("Pagamentos fin.", f"R$ {bw['fin_neg']:,.2f}")
            c3.metric("Créditos banco", f"R$ {bw['bnk_cre']:,.2f}")
            c4.metric("Recebimentos fin.", f"R$ {bw['fin_pos']:,.2f}")
            if bw["diverge_deb"]:
                st.warning(
                    f"Os débitos bancários (R$ {bw['bnk_deb']:,.2f}) diferem dos pagamentos "
                    f"financeiros (R$ {bw['fin_neg']:,.2f}). Verifique se os períodos ou arquivos correspondem."
                )
            if bw["diverge_cre"]:
                st.warning(
                    f"Os créditos bancários (R$ {bw['bnk_cre']:,.2f}) diferem dos recebimentos "
                    f"financeiros (R$ {bw['fin_pos']:,.2f}). Verifique se os períodos ou arquivos correspondem."
                )
            if not bw["diverge_deb"] and not bw["diverge_cre"]:
                st.success("Totais extrato e financeiro dentro da tolerância (1%).")

    cards = step_review(cards)
    st.session_state["review_cards"] = cards

    if cards and st.button("Aplicar decisões de revisão", key="btn_apply_review"):
        df_bnk, df_fin = apply_review_decisions(df_bnk, df_fin, cards)
        st.session_state["df_bnk"] = df_bnk
        st.session_state["df_fin"] = df_fin
        st.success("Decisões aplicadas!")
        st.rerun()

    st.divider()

    depara_dict = {}
    if cliente:
        cliente_id = get_cliente_id(cliente)
        if cliente_id:
            depara_rows = get_depara(cliente_id)
            depara_dict = get_depara_dict(depara_rows)

    if st.button("Gerar Relatório Excel", type="primary", key="btn_gerar"):
        with st.spinner("Gerando relatório..."):
            xlsx_bytes = build_report(df_bnk, df_fin, depara_dict)
            st.download_button(
                label="Baixar Relatório (.xlsx)",
                data=xlsx_bytes,
                file_name="relatorio_conciliacao.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    st.divider()
    if st.button("Nova conciliação", key="btn_nova"):
        for k in [
            "df_bnk", "df_fin", "review_cards", "wiz_step",
            "extrato_file", "fin_file", "fin2_file",
            "extrato_mapping", "fin_mapping", "fin2_mapping",
            "fin_modalidade_str", "params", "balance_warning",
        ]:
            st.session_state.pop(k, None)
        st.rerun()


# ── página De-Para ────────────────────────────────────────────────────────────

def depara_page(cliente):
    st.title("De-Para Contábil")

    if not cliente:
        st.warning("Selecione um cliente na barra lateral.")
        return

    cliente_id = get_cliente_id(cliente)
    if not cliente_id:
        st.error("Cliente não encontrado.")
        return

    st.subheader(f"Cliente: {cliente}")
    rows = get_depara(cliente_id)

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width='stretch')
        csv_bytes = export_depara_csv(rows)
        st.download_button("Exportar CSV", csv_bytes, "depara.csv", "text/csv")
    else:
        st.info("Nenhuma regra De-Para cadastrada.")

    st.divider()
    st.subheader("Adicionar / Editar regra")
    col1, col2, col3 = st.columns(3)
    with col1:
        classif = st.text_input("Classificação", key="dp_classif")
    with col2:
        debito = st.text_input("Conta Débito", key="dp_debito")
    with col3:
        credito = st.text_input("Conta Crédito", key="dp_credito")
    if st.button("Salvar", key="dp_save") and classif:
        upsert_depara(cliente_id, classif, debito, credito)
        st.success("Regra salva!")
        st.rerun()

    st.divider()
    st.subheader("Importar CSV")
    st.caption("CSV com colunas: classif, debito, credito")
    up = st.file_uploader("Upload CSV", type="csv", key="dp_csv_up")
    if up and st.button("Importar", key="dp_csv_btn"):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(up.read())
            tmp_path = tmp.name
        import_depara_csv(cliente_id, tmp_path)
        os.unlink(tmp_path)
        st.success("CSV importado!")
        st.rerun()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    pagina, cliente = sidebar()

    if pagina == "Nova Conciliação":
        wizard_page(cliente)
    elif pagina == "De-Para Contábil":
        depara_page(cliente)


if __name__ == "__main__":
    main()
