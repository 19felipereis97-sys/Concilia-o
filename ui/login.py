"""
Tela de login e troca de senha obrigatória.
"""
from __future__ import annotations
import streamlit as st
from plan.client_store import authenticate_user, force_change_password, log_acao


def show_login() -> bool:
    """Renderiza o formulário de login. Retorna True se autenticado com sucesso."""
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown(
            "<h2 style='text-align:center;margin-bottom:4px'>🔐 Conciliador Bancário</h2>"
            "<p style='text-align:center;color:#888;margin-bottom:24px'>Faça login para continuar</p>",
            unsafe_allow_html=True,
        )
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input("E-mail", placeholder="seu@email.com", key="f_email")
            senha = st.text_input("Senha", type="password", key="f_senha")
            submitted = st.form_submit_button(
                "Entrar", type="primary", use_container_width=True
            )

        if submitted:
            if not email.strip() or not senha:
                st.error("Preencha e-mail e senha.")
                return False

            user = authenticate_user(email.strip(), senha)

            if user is None:
                log_acao("sistema", "LOGIN_FALHA", f"email={email.strip()}")
                st.error("E-mail ou senha inválidos.")
                return False

            if not user["ativo"]:
                log_acao("sistema", "LOGIN_BLOQUEADO", f"email={email.strip()}")
                st.error("Usuário inativo. Contate o administrador.")
                return False

            log_acao(email.strip(), "LOGIN_SUCESSO", f"perfil={user['perfil']}")
            st.session_state["usuario_email"] = email.strip()
            st.session_state["usuario_perfil"] = user["perfil"]
            st.session_state["usuario_id"] = user["id"]
            st.session_state["usuario_nome"] = user.get("nome", "")
            st.session_state["usuario_departamento"] = user.get("departamento", "")
            st.session_state["troca_senha"] = bool(user["troca_senha_obrigatoria"])
            return True

    return False


def show_change_password_required():
    """Tela de troca de senha obrigatória (ativada após reset pelo admin)."""
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown(
            "<h2 style='text-align:center'>🔑 Troca de Senha Obrigatória</h2>",
            unsafe_allow_html=True,
        )
        st.warning(
            "O administrador redefiniu sua senha. "
            "Defina uma nova senha para acessar o sistema."
        )
        with st.form("change_pw_form"):
            nova = st.text_input("Nova senha", type="password", key="cp_nova")
            confirma = st.text_input("Confirmar nova senha", type="password", key="cp_confirma")
            submitted = st.form_submit_button(
                "Alterar senha", type="primary", use_container_width=True
            )

        if submitted:
            if len(nova) < 6:
                st.error("A senha deve ter pelo menos 6 caracteres.")
            elif nova != confirma:
                st.error("As senhas não coincidem.")
            else:
                email = st.session_state["usuario_email"]
                force_change_password(email, nova)
                log_acao(email, "TROCA_SENHA_OBRIGATORIA", "Senha trocada com sucesso")
                st.session_state["troca_senha"] = False
                st.success("Senha alterada! Redirecionando...")
                st.rerun()
