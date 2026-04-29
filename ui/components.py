"""
Componentes reutilizáveis para a interface Streamlit.
"""
from __future__ import annotations
import streamlit as st
from decimal import Decimal


def status_badge(status: str) -> str:
    cores = {
        "CONCILIADO":              "🟢",
        "CONCILIADO_MANUAL":       "🟩",
        "REVISAR":                 "🔴",
        "REVISAR_COLISAO":         "🟠",
        "SEM_PAREAMENTO":          "🟡",
        "IGNORADO_SEM_CONTRAPARTIDA": "⚪",
        "IGNORADO_USUARIO":        "⚫",
    }
    return cores.get(status, "❔") + " " + status


def fmt_valor(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, Decimal):
        v = float(v)
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_data(d) -> str:
    if d is None:
        return "-"
    if hasattr(d, "strftime"):
        return d.strftime("%d/%m/%Y")
    return str(d)


def progress_bar(step: int, total: int):
    st.progress(step / total, text=f"Etapa {step} de {total}")


def card_metric(label: str, value: str, delta: str = ""):
    st.metric(label=label, value=value, delta=delta or None)
