"""
Configurações Gerais — gestão de empresas, usuários, auditoria e histórico De x Para.
"""
from __future__ import annotations
import io
import datetime
import pandas as pd
import streamlit as st
from plan.client_store import (
    list_users, create_user, update_user, admin_reset_password,
    log_acao, list_logs, get_depara_historico,
    get_cliente_id, get_clientes_with_stats, list_clientes_display,
    create_cliente, rename_cliente, delete_cliente, update_cliente,
    import_clientes_bulk, get_kpis, get_user_activity,
    export_database_backup, get_database_path, get_storage_health, restore_database_backup,
)

# ── Valores fixos para filtros categóricos ─────────────────────────────────────
_TRIBUTACOES = [
    "Todas", "PRESUMIDO", "SIMPLES", "REAL TRIMESTRAL",
    "REAL ESTIMATIVA", "IMUNE/ISENTA", "INATIVA", "A CONFIRMAR",
]
_NIVEIS = ["Todos", "SÊNIOR", "MASTER", "PLENO", "ESPECIALISTA", "JÚNIOR"]
_UNIDADES = ["Todas", "MATRIZ", "FILIAL CAXIAS", "AURELINO LEAL"]


def show_admin_panel():
    st.title("Configurações Gerais")
    _show_kpi_banner()
    st.divider()

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🏢 Empresas",
        "👤 Atividade por Usuário",
        "👥 Gestão de Usuários",
        "📋 Auditoria de Acessos",
        "📝 Histórico De x Para",
        "💾 Backup",
    ])
    with tab1:
        _show_client_management()
    with tab2:
        _show_user_activity()
    with tab3:
        _show_user_management()
    with tab4:
        _show_audit_logs()
    with tab5:
        _show_depara_historico()
    with tab6:
        _show_backup_restore()


# ── KPI banner ─────────────────────────────────────────────────────────────────

def _show_kpi_banner():
    kpis = get_kpis()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("👥 Usuários ativos", kpis["usuarios_ativos"])
    c2.metric("🏢 Empresas ativas", kpis["empresas_ativas"])
    c3.metric("🔑 Logins (24h)", kpis["logins_24h"])
    c4.metric("📝 De x Para (7d)", kpis["depara_semana"])
    c5.metric("📊 Relatórios (30d)", kpis["relatorios_mes"])
    c6.metric("🔗 Total regras", kpis["total_depara"])


def _show_backup_restore():
    st.subheader("Backup e restauração do banco")
    st.caption(f"Banco atual: `{get_database_path()}`")
    st.warning(
        "Em ambientes sem disco persistente, alterações feitas em usuários, senhas e De x Para "
        "podem ser perdidas em reboot ou atualização. Baixe backups regularmente ou configure "
        "`CONCILIADOR_DB_PATH` para um volume persistente."
    )

    col_export, col_import = st.columns(2)
    with col_export:
        st.markdown("**Exportar backup**")
        try:
            backup = export_database_backup()
            st.download_button(
                "Baixar backup SQLite",
                backup,
                file_name=f"conciliador_backup_{datetime.datetime.now():%Y%m%d_%H%M%S}.db",
                mime="application/octet-stream",
                key="db_backup_download",
            )
        except Exception as e:
            st.error(f"Não foi possível gerar backup: {e}")

    with col_import:
        st.markdown("**Restaurar backup**")
        up = st.file_uploader("Arquivo .db de backup", type=["db", "sqlite"], key="db_restore_file")
        confirmar = st.checkbox(
            "Confirmo que a restauração substituirá usuários, senhas, empresas, De x Para e logs atuais.",
            key="db_restore_confirm",
        )
        if st.button("Restaurar backup", type="primary", disabled=not (up and confirmar), key="db_restore_btn"):
            try:
                restore_database_backup(up.read())
                log_acao(st.session_state.get("usuario_email", "desconhecido"), "BACKUP_RESTAURADO", "")
                st.success("Backup restaurado. O app será recarregado.")
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao restaurar backup: {e}")

    st.divider()
    st.subheader("Health check de produção")
    try:
        health = get_storage_health()
        df_health = pd.DataFrame(health["checks"])
        st.dataframe(
            df_health,
            hide_index=True,
            use_container_width=True,
            column_config={
                "item": st.column_config.TextColumn("Verificação", width=220),
                "status": st.column_config.TextColumn("Status", width=90),
                "detalhe": st.column_config.TextColumn("Detalhe", width=420),
            },
        )
        if any(c.get("status") == "ERRO" for c in health["checks"]):
            st.error("Existe item crítico antes de publicar uma nova versão.")
        elif any(c.get("status") == "ATENCAO" for c in health["checks"]):
            st.warning("Ambiente funcional, mas ainda há risco de perda de dados se o disco for efêmero.")
        else:
            st.success("Sinais principais de produção estão OK.")
    except Exception as e:
        st.error(f"Não foi possível executar o health check: {e}")

    st.markdown("**Rotina recomendada antes de atualizar versão**")
    st.markdown(
        "1. Baixar backup SQLite.\n"
        "2. Confirmar health check sem erro.\n"
        "3. Publicar a nova versão.\n"
        "4. Abrir o app e validar login, De x Para e templates.\n"
        "5. Restaurar backup somente se os dados não aparecerem."
    )


# ── Empresas ───────────────────────────────────────────────────────────────────

def _show_client_management():
    clientes = get_clientes_with_stats()

    # ── Filtros ──
    with st.container():
        f1, f2, f3, f4, f5 = st.columns([4, 2, 2, 2, 1])
        with f1:
            busca = st.text_input(
                "busca", placeholder="🔍  Buscar por nome, CNPJ, código ou grupo...",
                key="adm_emp_busca", label_visibility="collapsed",
            )
        with f2:
            tri_opts = sorted({c.get("tributacao", "") or "" for c in clientes if c.get("tributacao")})
            filtro_tri = st.selectbox("Tributação", ["Todas"] + tri_opts, key="adm_emp_tri")
        with f3:
            niv_opts = sorted({c.get("nivel_operacional", "") or "" for c in clientes if c.get("nivel_operacional")})
            filtro_niv = st.selectbox("Nível", ["Todos"] + niv_opts, key="adm_emp_niv")
        with f4:
            und_opts = sorted({c.get("unidade_jca", "") or "" for c in clientes if c.get("unidade_jca")})
            filtro_und = st.selectbox("Unidade", ["Todas"] + und_opts, key="adm_emp_und")
        with f5:
            filtro_st = st.selectbox("Status", ["Ativos", "Todos", "Inativos"], key="adm_emp_st")

    # ── Aplicar filtros ──
    df = pd.DataFrame(clientes)
    if df.empty:
        st.info("Nenhuma empresa cadastrada. Use **Importar planilha** ou **Adicionar** abaixo.")
        _show_add_import(clientes)
        return

    if busca.strip():
        t = busca.strip().lower()
        mask = (
            df["nome"].str.lower().str.contains(t, na=False)
            | df["cnpj"].str.lower().str.contains(t, na=False)
            | df["codigo_interno"].str.lower().str.contains(t, na=False)
            | df["grupo"].str.lower().str.contains(t, na=False)
        )
        df = df[mask]

    if filtro_tri != "Todas":
        df = df[df["tributacao"].str.upper() == filtro_tri.upper()]
    if filtro_niv != "Todos":
        df = df[df["nivel_operacional"].str.upper() == filtro_niv.upper()]
    if filtro_und != "Todas":
        df = df[df["unidade_jca"].str.upper() == filtro_und.upper()]
    if filtro_st == "Ativos":
        df = df[df["ativo"] == 1]
    elif filtro_st == "Inativos":
        df = df[df["ativo"] == 0]

    total_orig = len(clientes)
    total_filt = len(df)

    # ── Tabela ──
    df_exib = df[[
        "codigo_interno", "nome", "cnpj", "grupo",
        "unidade_jca", "tributacao", "nivel_operacional",
        "num_depara", "ativo",
    ]].copy()
    df_exib["ativo"] = df_exib["ativo"].map({1: "✅ Ativo", 0: "❌ Inativo"})
    df_exib["num_depara"] = df_exib["num_depara"].fillna(0).astype(int)
    df_exib.columns = [
        "Código", "Empresa", "CNPJ", "Grupo",
        "Unidade", "Tributação", "Nível",
        "Regras", "Status",
    ]

    st.caption(
        f"**{total_filt}** empresa(s) exibida(s)"
        + (f" de {total_orig} no total" if total_filt < total_orig else "")
    )

    st.dataframe(
        df_exib,
        hide_index=True,
        use_container_width=True,
        height=min(35 * len(df_exib) + 38, 520),
        column_config={
            "Código":    st.column_config.TextColumn("Código",    width=80),
            "Empresa":   st.column_config.TextColumn("Empresa",   width=320),
            "CNPJ":      st.column_config.TextColumn("CNPJ",      width=160),
            "Grupo":     st.column_config.TextColumn("Grupo",     width=180),
            "Unidade":   st.column_config.TextColumn("Unidade",   width=120),
            "Tributação":st.column_config.TextColumn("Tributação",width=140),
            "Nível":     st.column_config.TextColumn("Nível",     width=100),
            "Regras":    st.column_config.NumberColumn("Regras",  width=70),
            "Status":    st.column_config.TextColumn("Status",    width=90),
        },
    )

    if not df_exib.empty:
        buf = io.StringIO()
        df_exib.to_csv(buf, index=False, encoding="utf-8-sig")
        st.download_button(
            "⬇️ Exportar CSV",
            buf.getvalue().encode("utf-8-sig"),
            "empresas.csv", "text/csv", key="adm_emp_export",
        )

    st.divider()
    _show_add_import(clientes)
    st.divider()
    _show_edit_delete(clientes)


def _show_add_import(clientes: list):
    col_add, col_imp = st.columns(2)

    with col_add:
        with st.expander("➕ Adicionar empresa manualmente", expanded=False):
            with st.form("form_add_emp"):
                r1c1, r1c2, r1c3 = st.columns(3)
                with r1c1:
                    n_nome = st.text_input("Nome *", key="ae_nome")
                with r1c2:
                    n_cnpj = st.text_input("CNPJ", key="ae_cnpj")
                with r1c3:
                    n_cod  = st.text_input("Código", key="ae_cod")
                r2c1, r2c2, r2c3, r2c4 = st.columns(4)
                with r2c1:
                    n_grp = st.text_input("Grupo", key="ae_grp")
                with r2c2:
                    n_und = st.text_input("Unidade JCA", key="ae_und")
                with r2c3:
                    n_tri = st.text_input("Tributação", key="ae_tri")
                with r2c4:
                    n_niv = st.text_input("Nível", key="ae_niv")
                ok = st.form_submit_button("Adicionar", type="primary")
            if ok:
                if not n_nome.strip():
                    st.error("Nome obrigatório.")
                elif create_cliente(n_nome.strip()):
                    cid = get_cliente_id(n_nome.strip())
                    if cid:
                        update_cliente(
                            cid,
                            cnpj=n_cnpj.strip(), codigo_interno=n_cod.strip(),
                            grupo=n_grp.strip(), unidade_jca=n_und.strip(),
                            tributacao=n_tri.strip(), nivel_operacional=n_niv.strip(),
                        )
                    log_acao(st.session_state["usuario_email"], "CLIENTE_CRIADO", f"nome={n_nome.strip()}")
                    st.success(f"**{n_nome.strip()}** adicionada.")
                    st.rerun()
                else:
                    st.warning("Já existe uma empresa com esse nome.")

    with col_imp:
        with st.expander("📥 Importar planilha (Excel / CSV)", expanded=False):
            st.caption(
                "Colunas detectadas automaticamente: **CÓD, GRUPO, CNPJ, EMPRESAS, "
                "UNIDADE JCA, TRIBUTAÇÃO 2026, NÍVEL OPERACIONAL**"
            )
            up = st.file_uploader(
                "Selecione o arquivo", type=["xlsx", "xls", "csv"], key="adm_imp_file"
            )
            if up:
                try:
                    suffix = up.name.rsplit(".", 1)[-1].lower()
                    preview_bytes = up.read()
                    if suffix in ("xlsx", "xls"):
                        df_prev = pd.read_excel(io.BytesIO(preview_bytes), dtype=str)
                    else:
                        df_prev = pd.read_csv(io.BytesIO(preview_bytes), dtype=str, encoding="utf-8-sig")
                    df_prev.columns = [c.strip() for c in df_prev.columns]
                    st.markdown(f"**Prévia — {len(df_prev)} linha(s), {len(df_prev.columns)} coluna(s):**")
                    st.dataframe(df_prev.head(5), hide_index=True, use_container_width=True)

                    if st.button("Confirmar importação", type="primary", key="adm_imp_confirm"):
                        res = import_clientes_bulk(preview_bytes, f".{suffix}")
                        log_acao(
                            st.session_state["usuario_email"],
                            "CLIENTES_IMPORTADOS",
                            f"inseridos={res['inseridos']};atualizados={res['atualizados']};ignorados={res['ignorados']}",
                        )
                        st.success(
                            f"Importação concluída: **{res['inseridos']}** inserida(s), "
                            f"**{res['atualizados']}** atualizada(s), "
                            f"**{res['ignorados']}** ignorada(s)."
                        )
                        st.rerun()
                except Exception as e:
                    st.error(f"Erro ao processar arquivo: {e}")


def _show_edit_delete(clientes: list):
    if not clientes:
        return

    with st.expander("✏️ Editar / 🗑️ Excluir empresa", expanded=False):
        opts = sorted(clientes, key=lambda x: (x.get("codigo_interno") or "", x["nome"]))
        labels = {
            c["id"]: f"{c.get('codigo_interno') or 'SEM CODIGO'} - {c['nome']}"
            for c in opts
        }
        sel_id = st.selectbox(
            "Selecionar empresa",
            [c["id"] for c in opts],
            key="adm_edit_sel_id",
            format_func=lambda cid: labels.get(cid, str(cid)),
        )
        cli = next((c for c in clientes if c["id"] == sel_id), None)
        if not cli:
            return
        sel_nome = cli["nome"]

        # Formulário de edição
        st.markdown(f"**Editando:** {cli['nome']}")
        with st.form(f"form_edit_empresa_{sel_id}"):
            e1, e2, e3 = st.columns(3)
            with e1:
                e_nome = st.text_input("Nome *", value=cli["nome"], key=f"ee_nome_{sel_id}")
            with e2:
                e_cnpj = st.text_input("CNPJ", value=cli.get("cnpj") or "", key=f"ee_cnpj_{sel_id}")
            with e3:
                e_cod = st.text_input("Código", value=cli.get("codigo_interno") or "", key=f"ee_cod_{sel_id}")
            e4, e5, e6, e7 = st.columns(4)
            with e4:
                e_grp = st.text_input("Grupo", value=cli.get("grupo") or "", key=f"ee_grp_{sel_id}")
            with e5:
                e_und = st.text_input("Unidade JCA", value=cli.get("unidade_jca") or "", key=f"ee_und_{sel_id}")
            with e6:
                e_tri = st.text_input("Tributação", value=cli.get("tributacao") or "", key=f"ee_tri_{sel_id}")
            with e7:
                e_niv = st.text_input("Nível", value=cli.get("nivel_operacional") or "", key=f"ee_niv_{sel_id}")
            e8, _ = st.columns([1, 3])
            with e8:
                e_ativo = st.selectbox(
                    "Status", ["Ativo", "Inativo"],
                    index=0 if cli["ativo"] else 1,
                    key=f"ee_ativo_{sel_id}",
                )
            salvar = st.form_submit_button("Salvar alterações", type="primary")

        if salvar:
            if not e_nome.strip():
                st.error("Nome obrigatório.")
            else:
                nome_mudou = e_nome.strip() != cli["nome"]
                ok = rename_cliente(cli["id"], e_nome.strip()) if nome_mudou else True
                if not ok:
                    st.error("Já existe uma empresa com esse nome.")
                else:
                    update_cliente(
                        cli["id"],
                        cnpj=e_cnpj.strip(), codigo_interno=e_cod.strip(),
                        grupo=e_grp.strip(), unidade_jca=e_und.strip(),
                        tributacao=e_tri.strip(), nivel_operacional=e_niv.strip(),
                        ativo=1 if e_ativo == "Ativo" else 0,
                    )
                    log_acao(
                        st.session_state["usuario_email"],
                        "CLIENTE_EDITADO",
                        f"id={cli['id']};nome={e_nome.strip()}",
                    )
                    st.success("Empresa atualizada.")
                    st.rerun()

        st.divider()
        st.markdown("**Zona de perigo**")
        if cli["num_depara"] > 0:
            st.warning(f"Esta empresa tem **{cli['num_depara']} regra(s)** de De x Para que serão removidas.")
        confirmar = st.checkbox(
            f"Confirmo a exclusão permanente de **{sel_nome}**", key=f"adm_del_chk_{sel_id}"
        )
        if st.button("🗑️ Excluir empresa", type="primary", disabled=not confirmar, key=f"adm_del_btn_{sel_id}"):
            delete_cliente(cli["id"])
            log_acao(
                st.session_state["usuario_email"],
                "CLIENTE_EXCLUIDO",
                f"nome={sel_nome};depara={cli['num_depara']}",
            )
            st.success(f"**{sel_nome}** excluída.")
            st.rerun()


# ── Atividade por usuário ──────────────────────────────────────────────────────

def _card_usuario(u: dict) -> str:
    cor = "#FF9500" if u["perfil"] == "admin" else "#21C354"
    cor_st = "#21C354" if u["ativo"] else "#888"
    nome = u.get("nome") or u["email"]
    departamento = u.get("departamento") or "Sem departamento"
    inicial = nome[0].upper() if nome else "?"
    txt_st = "Ativo" if u["ativo"] else "Inativo"
    ultimo = u["ultimo_acesso"]
    if ultimo:
        try:
            ultimo_fmt = datetime.datetime.fromisoformat(ultimo).strftime("%d/%m/%Y %H:%M")
        except Exception:
            ultimo_fmt = str(ultimo)[:16]
    else:
        ultimo_fmt = "Nunca acessou"

    return (
        f'<div style="border:1px solid {cor}33;border-radius:10px;padding:14px 16px;'
        f'background:{cor}06;margin-bottom:8px;">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
        f'<div style="width:40px;height:40px;border-radius:50%;background:{cor};'
        f'display:flex;align-items:center;justify-content:center;'
        f'font-size:1.1em;font-weight:700;color:white;flex-shrink:0">{inicial}</div>'
        f'<div style="overflow:hidden">'
        f'<p style="margin:0;font-size:0.9em;font-weight:600;white-space:nowrap;'
        f'overflow:hidden;text-overflow:ellipsis">{nome}</p>'
        f'<p style="margin:0;font-size:0.75em;color:#888;white-space:nowrap;'
        f'overflow:hidden;text-overflow:ellipsis">{u["email"]}</p>'
        f'<span style="font-size:0.75em;color:{cor_st}">{txt_st}</span>'
        f'<span style="font-size:0.75em;color:#888"> · {u["perfil"].capitalize()} · {departamento}</span>'
        f"</div></div>"
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
        f'<div style="border-radius:8px;padding:8px;text-align:center;border:1px solid #88888820">'
        f'<p style="margin:0;font-size:1.3em;font-weight:700">{u["total_logins"]}</p>'
        f'<p style="margin:0;font-size:0.7em;color:#888">Logins</p></div>'
        f'<div style="border-radius:8px;padding:8px;text-align:center;border:1px solid #88888820">'
        f'<p style="margin:0;font-size:1.3em;font-weight:700">{u["conciliacoes"]}</p>'
        f'<p style="margin:0;font-size:0.7em;color:#888">Conciliações</p></div>'
        f'<div style="border-radius:8px;padding:8px;text-align:center;border:1px solid #88888820">'
        f'<p style="margin:0;font-size:1.3em;font-weight:700">{u["relatorios_gerados"]}</p>'
        f'<p style="margin:0;font-size:0.7em;color:#888">Relatórios</p></div>'
        f'<div style="border-radius:8px;padding:8px;text-align:center;border:1px solid #88888820">'
        f'<p style="margin:0;font-size:1.3em;font-weight:700">{u["depara_alterados"]}</p>'
        f'<p style="margin:0;font-size:0.7em;color:#888">De x Para</p></div>'
        f'</div>'
        f'<p style="margin:8px 0 0;font-size:0.75em;color:#888;text-align:center">'
        f'Último acesso: {ultimo_fmt}</p>'
        f"</div>"
    )


def _activity_kpi_card(titulo: str, valor: int, cor: str) -> str:
    return (
        f'<div style="border:1px solid {cor}44;border-left:5px solid {cor};'
        f'border-radius:8px;padding:14px 16px;background:{cor}0D;min-height:92px;">'
        f'<p style="margin:0 0 8px;font-size:0.78em;color:#666;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:0">{titulo}</p>'
        f'<p style="margin:0;font-size:2em;line-height:1.05;font-weight:800;color:{cor}">{valor}</p>'
        f"</div>"
    )


def _parse_datetime_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="mixed", errors="coerce")


def _user_activity_total(u: dict) -> int:
    return (
        int(u.get("total_logins") or 0)
        + int(u.get("conciliacoes") or 0)
        + int(u.get("relatorios_gerados") or 0)
        + int(u.get("depara_alterados") or 0)
    )


def _activity_ranking_df(activities: list[dict], metric: str, label: str) -> pd.DataFrame:
    rows = []
    for u in activities:
        rows.append({
            "Nome": u.get("nome") or u.get("email"),
            "E-mail": u.get("email"),
            "Departamento": u.get("departamento") or "",
            label: int(u.get(metric) or 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(label, ascending=False).head(10)


def _show_user_activity():
    st.subheader("Atividade por usuário")

    hoje = datetime.date.today()
    periodo = st.date_input(
        "Período",
        value=(hoje - datetime.timedelta(days=30), hoje),
        key="ativ_user_periodo",
    )
    data_ini = str(periodo[0]) if isinstance(periodo, (list, tuple)) and len(periodo) > 0 else ""
    data_fim = str(periodo[1]) if isinstance(periodo, (list, tuple)) and len(periodo) > 1 else data_ini

    activities = get_user_activity(data_inicio=data_ini, data_fim=data_fim)
    if not activities:
        st.info("Nenhum usuário encontrado.")
        return

    f1, f2, f3, f4, f5 = st.columns([4, 2, 2, 2, 2])
    with f1:
        busca = st.text_input(
            "busca",
            placeholder="🔍  Buscar por nome, e-mail ou departamento...",
            key="ativ_user_busca",
            label_visibility="collapsed",
        )
    with f2:
        departamentos = sorted({u.get("departamento") or "" for u in activities if u.get("departamento")})
        filtro_departamento = st.selectbox(
            "Departamento", ["Todos"] + departamentos, key="ativ_user_departamento"
        )
    with f3:
        filtro_perfil = st.selectbox("Perfil", ["Todos", "admin", "operacional"], key="ativ_user_perfil")
    with f4:
        filtro_status = st.selectbox("Status", ["Todos", "Ativos", "Inativos"], key="ativ_user_status")
    with f5:
        somente_sem_atividade = st.checkbox("Sem atividade", key="ativ_user_sem_atividade")

    if busca.strip():
        termo = busca.strip().lower()
        activities = [
            u for u in activities
            if termo in str(u.get("nome") or "").lower()
            or termo in str(u.get("email") or "").lower()
            or termo in str(u.get("departamento") or "").lower()
        ]
    if filtro_departamento != "Todos":
        activities = [u for u in activities if (u.get("departamento") or "") == filtro_departamento]
    if filtro_perfil != "Todos":
        activities = [u for u in activities if u.get("perfil") == filtro_perfil]
    if filtro_status == "Ativos":
        activities = [u for u in activities if u.get("ativo")]
    elif filtro_status == "Inativos":
        activities = [u for u in activities if not u.get("ativo")]
    if somente_sem_atividade:
        activities = [u for u in activities if _user_activity_total(u) == 0]

    total_logins = sum(int(u.get("total_logins") or 0) for u in activities)
    total_conciliacoes = sum(int(u.get("conciliacoes") or 0) for u in activities)
    total_relatorios = sum(int(u.get("relatorios_gerados") or 0) for u in activities)
    total_depara = sum(int(u.get("depara_alterados") or 0) for u in activities)
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(
            _activity_kpi_card("Usuários", len(activities), "#4B5563"),
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            _activity_kpi_card("Logins", total_logins, "#2563EB"),
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            _activity_kpi_card("Conciliações", total_conciliacoes, "#059669"),
            unsafe_allow_html=True,
        )
    with k4:
        st.markdown(
            _activity_kpi_card("Relatórios", total_relatorios, "#7C3AED"),
            unsafe_allow_html=True,
        )
    with k5:
        st.markdown(
            _activity_kpi_card("De x Para", total_depara, "#D97706"),
            unsafe_allow_html=True,
        )

    st.caption(f"**{len(activities)}** usuário(s) exibido(s)")
    if not activities:
        st.info("Nenhum usuário encontrado com os filtros aplicados.")
        return

    df_activity = pd.DataFrame([
        {
            "Nome": u.get("nome") or u.get("email"),
            "E-mail": u.get("email"),
            "Departamento": u.get("departamento") or "Sem departamento",
            "Perfil": u.get("perfil"),
            "Status": "Ativo" if u.get("ativo") else "Inativo",
            "Logins": int(u.get("total_logins") or 0),
            "Conciliações": int(u.get("conciliacoes") or 0),
            "Relatórios": int(u.get("relatorios_gerados") or 0),
            "De x Para": int(u.get("depara_alterados") or 0),
            "Total": _user_activity_total(u),
            "Último acesso": u.get("ultimo_acesso"),
        }
        for u in activities
    ])
    df_activity["Último acesso"] = (
        _parse_datetime_series(df_activity["Último acesso"])
        .dt.strftime("%d/%m/%Y %H:%M")
        .fillna("Sem acesso no período")
    )

    st.divider()
    c_graf, c_rank = st.columns([1.15, 1])
    with c_graf:
        st.markdown("**Adoção por departamento**")
        dept = (
            df_activity.groupby("Departamento", as_index=False)[
                ["Logins", "Conciliações", "Relatórios", "De x Para"]
            ]
            .sum()
            .set_index("Departamento")
        )
        st.bar_chart(dept, use_container_width=True)

    with c_rank:
        st.markdown("**Rankings por uso**")
        r1, r2, r3, r4 = st.tabs(["Logins", "Conciliações", "Relatórios", "De x Para"])
        with r1:
            st.dataframe(_activity_ranking_df(activities, "total_logins", "Logins"), hide_index=True, use_container_width=True, height=260)
        with r2:
            st.dataframe(_activity_ranking_df(activities, "conciliacoes", "Conciliações"), hide_index=True, use_container_width=True, height=260)
        with r3:
            st.dataframe(_activity_ranking_df(activities, "relatorios_gerados", "Relatórios"), hide_index=True, use_container_width=True, height=260)
        with r4:
            st.dataframe(_activity_ranking_df(activities, "depara_alterados", "De x Para"), hide_index=True, use_container_width=True, height=260)

    sem_atividade = df_activity[df_activity["Total"] == 0][
        ["Nome", "E-mail", "Departamento", "Perfil", "Status", "Último acesso"]
    ]
    with st.expander(f"Usuários sem atividade no período ({len(sem_atividade)})", expanded=bool(somente_sem_atividade)):
        if sem_atividade.empty:
            st.success("Todos os usuários filtrados tiveram alguma atividade no período.")
        else:
            st.dataframe(sem_atividade, hide_index=True, use_container_width=True, height=280)

    st.markdown("**Indicadores por usuário**")
    for i in range(0, len(activities), 3):
        cols = st.columns(3)
        for j, u in enumerate(activities[i:i + 3]):
            with cols[j]:
                st.markdown(_card_usuario(u), unsafe_allow_html=True)


# ── Gestão de usuários ─────────────────────────────────────────────────────────

def _show_user_management():
    st.subheader("Usuários cadastrados")
    users = list_users()
    user_labels = {
        u["id"]: f"{u.get('nome') or 'Sem nome'} - {u['email']}"
        for u in users
    }
    if users:
        df = pd.DataFrame(users)[[
            "nome", "email", "departamento", "perfil",
            "ativo", "troca_senha_obrigatoria", "criado_em",
        ]].copy()
        df.columns = ["Nome", "E-mail", "Departamento", "Perfil", "Ativo", "Troca senha obrig.", "Criado em"]
        df["Ativo"] = df["Ativo"].map({1: "✅ Sim", 0: "❌ Não"})
        df["Troca senha obrig."] = df["Troca senha obrig."].map({1: "⚠️ Sim", 0: "Não"})
        df["Criado em"] = (
            pd.to_datetime(df["Criado em"], format="mixed", errors="coerce")
            .dt.strftime("%d/%m/%Y %H:%M")
            .fillna("")
        )
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("Nenhum usuário cadastrado.")

    st.divider()

    with st.expander("➕ Criar novo usuário", expanded=False):
        with st.form("form_criar_usuario"):
            col1, col2 = st.columns(2)
            with col1:
                novo_nome = st.text_input("Nome", key="adm_novo_nome")
                novo_email = st.text_input("E-mail", key="adm_novo_email")
                novo_perfil = st.selectbox("Perfil", ["operacional", "admin"], key="adm_novo_perfil")
            with col2:
                novo_departamento = st.text_input("Departamento", key="adm_novo_departamento")
                nova_senha = st.text_input("Senha inicial", type="password", key="adm_nova_senha")
                confirma = st.text_input("Confirmar senha", type="password", key="adm_confirma")
            submitted = st.form_submit_button("Criar usuário", type="primary")
        if submitted:
            if not novo_email.strip() or not nova_senha:
                st.error("Preencha e-mail e senha.")
            elif "@" not in novo_email:
                st.error("Informe um e-mail válido.")
            elif nova_senha != confirma:
                st.error("As senhas não coincidem.")
            elif len(nova_senha) < 6:
                st.error("Senha mínima: 6 caracteres.")
            elif create_user(
                novo_email.strip(),
                nova_senha,
                perfil=novo_perfil,
                nome=novo_nome.strip(),
                departamento=novo_departamento.strip(),
            ):
                log_acao(st.session_state["usuario_email"], "USUARIO_CRIADO",
                         f"email={novo_email.strip()};perfil={novo_perfil};nome={novo_nome.strip()}")
                st.success(f"Usuário **{novo_email.strip()}** criado.")
                st.rerun()
            else:
                st.error("Já existe um usuário com esse e-mail.")

    with st.expander("✏️ Editar usuário (perfil e status)", expanded=False):
        if not users:
            st.info("Nenhum usuário para editar.")
        else:
            user_ids = [u["id"] for u in sorted(users, key=lambda x: (x.get("nome") or "", x["email"]))]
            sel_user_id = st.selectbox(
                "Selecionar usuário",
                user_ids,
                key="adm_sel_edit_id",
                format_func=lambda uid: user_labels.get(uid, str(uid)),
            )
            sel_user = next((u for u in users if u["id"] == sel_user_id), None)
            if sel_user:
                form_key = f"form_editar_usuario_{sel_user['id']}"
                with st.form(form_key):
                    col1, col2 = st.columns(2)
                    with col1:
                        nome_edit = st.text_input(
                            "Nome",
                            value=sel_user.get("nome") or "",
                            key=f"adm_edit_nome_{sel_user['id']}",
                        )
                        novo_perfil_edit = st.selectbox(
                            "Perfil", ["operacional", "admin"],
                            index=0 if sel_user["perfil"] == "operacional" else 1,
                            key=f"adm_edit_perfil_{sel_user['id']}",
                        )
                    with col2:
                        departamento_edit = st.text_input(
                            "Departamento",
                            value=sel_user.get("departamento") or "",
                            key=f"adm_edit_departamento_{sel_user['id']}",
                        )
                        novo_ativo = st.selectbox(
                            "Status", ["Ativo", "Inativo"],
                            index=0 if sel_user["ativo"] else 1,
                            key=f"adm_edit_ativo_{sel_user['id']}",
                        )
                    submitted_edit = st.form_submit_button("Salvar alterações", type="primary")
                if submitted_edit:
                    ativo_val = 1 if novo_ativo == "Ativo" else 0
                    update_user(
                        sel_user["id"],
                        perfil=novo_perfil_edit,
                        ativo=ativo_val,
                        nome=nome_edit.strip(),
                        departamento=departamento_edit.strip(),
                    )
                    log_acao(st.session_state["usuario_email"], "USUARIO_EDITADO",
                             f"email={sel_user['email']};perfil={novo_perfil_edit};ativo={ativo_val}")
                    st.success("Usuário atualizado.")
                    st.rerun()

    with st.expander("🔑 Redefinir senha de usuário", expanded=False):
        if not users:
            st.info("Nenhum usuário.")
        else:
            user_ids = [u["id"] for u in sorted(users, key=lambda x: (x.get("nome") or "", x["email"]))]
            reset_user_id = st.selectbox(
                "Selecionar usuário",
                user_ids,
                key="adm_sel_reset_id",
                format_func=lambda uid: user_labels.get(uid, str(uid)),
            )
            user_reset = next((u for u in users if u["id"] == reset_user_id), None)
            st.caption("⚠️ O usuário será obrigado a trocar a senha no próximo login.")
            with st.form("form_reset_senha"):
                col1, col2 = st.columns(2)
                with col1:
                    nova_reset = st.text_input("Nova senha", type="password", key="adm_nova_reset")
                with col2:
                    confirma_reset = st.text_input("Confirmar", type="password", key="adm_confirma_reset")
                submitted_reset = st.form_submit_button("Redefinir senha", type="primary")
            if submitted_reset:
                if not nova_reset:
                    st.error("Informe a nova senha.")
                elif nova_reset != confirma_reset:
                    st.error("As senhas não coincidem.")
                elif len(nova_reset) < 6:
                    st.error("Senha mínima: 6 caracteres.")
                else:
                    admin_reset_password(user_reset["id"], nova_reset)
                    log_acao(st.session_state["usuario_email"], "SENHA_REDEFINIDA_ADMIN",
                             f"email={user_reset['email']}")
                    st.success(f"Senha de **{user_reset['email']}** redefinida.")
                    st.rerun()


# ── Auditoria de acessos ───────────────────────────────────────────────────────

def _audit_category(acao: str) -> str:
    acao = str(acao or "").upper()
    if acao.startswith("LOGIN") or acao == "LOGOUT":
        return "Login"
    if "CONCILIACAO" in acao:
        return "Conciliação"
    if "RELATORIO" in acao:
        return "Relatórios"
    if "DEPARA" in acao:
        return "De x Para"
    if acao.startswith("USUARIO") or "SENHA" in acao or acao.startswith("CLIENTE"):
        return "Administração"
    if "IMPORT" in acao:
        return "Importação"
    return "Outros"


def _audit_is_sensitive(acao: str, detalhes: str) -> bool:
    acao = str(acao or "").upper()
    detalhes = str(detalhes or "").lower()
    sensitive_actions = {
        "SENHA_REDEFINIDA_ADMIN",
        "CLIENTE_EXCLUIDO",
        "USUARIO_CRIADO",
        "CLIENTES_IMPORTADOS",
        "USUARIOS_IMPORTADOS",
        "DEPARA_REPLICADO",
        "DEPARA_REPLICADO_GRUPO",
    }
    return (
        acao in sensitive_actions
        or "perfil=admin" in detalhes
        or "substituir=true" in detalhes
        or "batch" in detalhes
        or "exclu" in detalhes
        or "import" in detalhes
    )


def _audit_extract_company(detalhes: str) -> str:
    detalhes = str(detalhes or "")
    for marker in ("cliente=", "nome=", "origem=", "destino="):
        if marker in detalhes:
            value = detalhes.split(marker, 1)[1].split(";", 1)[0].strip()
            if value:
                return value
    return ""


def _audit_export_excel(df_logs: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_logs.to_excel(writer, index=False, sheet_name="Auditoria")
    return out.getvalue()


def _show_audit_logs():
    st.subheader("Auditoria de processos")

    hoje = datetime.date.today()
    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
    with c1:
        data_range = st.date_input(
            "Período", value=(hoje - datetime.timedelta(days=30), hoje), key="audit_datas"
        )
    with c2:
        filtro_categoria = st.selectbox(
            "Categoria",
            ["Todas", "Login", "Conciliação", "Relatórios", "De x Para", "Administração", "Importação", "Outros"],
            key="audit_categoria",
        )
    with c3:
        filtro_sensivel = st.selectbox("Sensíveis", ["Todos", "Somente sensíveis"], key="audit_sensivel")
    with c4:
        filtro_texto = st.text_input(
            "Texto nos detalhes", key="audit_texto", placeholder="Conta, cliente, importação, senha..."
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        filtro_email = st.text_input("Filtrar por usuário", key="audit_email", placeholder="Parte do e-mail")
    with col2:
        acoes = [
            "Todas",
            "LOGIN_SUCESSO", "LOGIN_FALHA", "LOGIN_BLOQUEADO", "LOGOUT",
            "TROCA_SENHA", "TROCA_SENHA_OBRIGATORIA",
            "CONCILIACAO_REALIZADA", "DEPARA_ALTERADO", "RELATORIO_GERADO",
            "USUARIO_CRIADO", "USUARIO_EDITADO", "SENHA_REDEFINIDA_ADMIN",
            "CLIENTE_CRIADO", "CLIENTE_EDITADO", "CLIENTES_IMPORTADOS",
            "CLIENTE_STATUS", "CLIENTE_EXCLUIDO", "USUARIOS_IMPORTADOS",
            "DEPARA_REPLICADO", "DEPARA_REPLICADO_GRUPO",
        ]
        filtro_acao = st.selectbox("Filtrar por ação", acoes, key="audit_acao")
    with col3:
        filtro_empresa = st.text_input("Filtrar por empresa", key="audit_empresa", placeholder="Nome da empresa")

    data_ini = str(data_range[0]) if isinstance(data_range, (list, tuple)) and len(data_range) > 0 else ""
    data_fim = str(data_range[1]) if isinstance(data_range, (list, tuple)) and len(data_range) > 1 else data_ini

    logs = list_logs(
        limit=2000,
        usuario_filter=filtro_email.strip(),
        acao_filter=filtro_acao if filtro_acao != "Todas" else "",
        detalhes_filter=filtro_texto.strip(),
        data_inicio=data_ini,
        data_fim=data_fim,
    )

    if not logs:
        st.info("Nenhum log encontrado com os filtros aplicados.")
        return

    df_logs = pd.DataFrame(logs)
    df_logs["Data/Hora"] = _parse_datetime_series(df_logs["criado_em"])
    df_logs["Categoria"] = df_logs["acao"].map(_audit_category)
    df_logs["Empresa"] = df_logs["detalhes"].map(_audit_extract_company)
    df_logs["Sensível"] = [
        "Sim" if _audit_is_sensitive(acao, detalhes) else "Não"
        for acao, detalhes in zip(df_logs["acao"], df_logs["detalhes"])
    ]

    if filtro_categoria != "Todas":
        df_logs = df_logs[df_logs["Categoria"] == filtro_categoria]
    if filtro_sensivel == "Somente sensíveis":
        df_logs = df_logs[df_logs["Sensível"] == "Sim"]
    if filtro_empresa.strip():
        termo_empresa = filtro_empresa.strip().lower()
        df_logs = df_logs[
            df_logs["Empresa"].str.lower().str.contains(termo_empresa, na=False)
            | df_logs["detalhes"].str.lower().str.contains(termo_empresa, na=False)
        ]

    if df_logs.empty:
        st.info("Nenhum log encontrado com os filtros aplicados.")
        return

    df_logs = df_logs.sort_values("Data/Hora", ascending=False)
    df_logs["Data"] = df_logs["Data/Hora"].dt.strftime("%d/%m/%Y").fillna("")
    df_logs["Data/Hora"] = df_logs["Data/Hora"].dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    df_logs = df_logs.rename(columns={
        "usuario": "Usuário",
        "acao": "Ação",
        "detalhes": "Detalhes",
    })
    df_logs = df_logs[[
        "Data/Hora", "Data", "Categoria", "Sensível", "Usuário", "Ação", "Empresa", "Detalhes",
    ]]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Eventos", len(df_logs))
    k2.metric("Sensíveis", int((df_logs["Sensível"] == "Sim").sum()))
    k3.metric("Usuários", df_logs["Usuário"].nunique())
    k4.metric("Categorias", df_logs["Categoria"].nunique())

    st.divider()
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**Resumo por dia**")
        por_dia = df_logs.groupby("Data", as_index=False).size().rename(columns={"size": "Eventos"})
        st.bar_chart(por_dia.set_index("Data"), use_container_width=True)
    with s2:
        st.markdown("**Resumo por usuário**")
        por_usuario = (
            df_logs.groupby("Usuário", as_index=False).size()
            .rename(columns={"size": "Eventos"})
            .sort_values("Eventos", ascending=False)
            .head(12)
        )
        st.dataframe(por_usuario, hide_index=True, use_container_width=True, height=260)

    st.dataframe(df_logs, hide_index=True, use_container_width=True)

    buf = io.StringIO()
    df_logs.to_csv(buf, index=False, encoding="utf-8-sig")
    e1, e2 = st.columns([1, 1])
    with e1:
        st.download_button(
            "⬇️ Exportar CSV",
            buf.getvalue().encode("utf-8-sig"),
            "auditoria.csv",
            "text/csv",
            key="audit_export",
        )
    with e2:
        try:
            st.download_button(
                "⬇️ Exportar Excel",
                _audit_export_excel(df_logs),
                "auditoria.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="audit_export_excel",
            )
        except Exception:
            st.caption("Exportação Excel indisponível neste ambiente.")


# ── Histórico De x Para ────────────────────────────────────────────────────────

def _show_depara_historico():
    st.subheader("Histórico de alterações do De x Para")
    clientes = list_clientes_display(apenas_ativos=False)
    if not clientes:
        st.info("Nenhuma empresa cadastrada.")
        return

    labels = {c["id"]: c["label"] for c in clientes}
    cliente_id = st.selectbox(
        "Selecionar empresa",
        list(labels),
        key="hist_cli_id",
        format_func=lambda cid: labels.get(cid, str(cid)),
    )
    cliente_sel = labels.get(cliente_id, "")
    if not cliente_id:
        return

    hist = get_depara_historico(cliente_id, limit=200)
    if not hist:
        st.info("Nenhuma alteração registrada para esta empresa.")
        return

    df_hist = pd.DataFrame(hist)
    df_hist["criado_em"] = (
        pd.to_datetime(df_hist["criado_em"], format="mixed", errors="coerce")
        .dt.strftime("%d/%m/%Y %H:%M:%S")
        .fillna("")
    )
    df_hist.columns = ["Usuário", "Ação", "Detalhes", "Data/Hora"]
    df_hist = df_hist[["Data/Hora", "Usuário", "Ação", "Detalhes"]]

    st.dataframe(df_hist, hide_index=True, use_container_width=True)

    buf = io.StringIO()
    df_hist.to_csv(buf, index=False, encoding="utf-8-sig")
    st.download_button("⬇️ Exportar CSV", buf.getvalue().encode("utf-8-sig"),
                       f"historico_depara_{cliente_sel}.csv", "text/csv", key="hist_export")
