"""
Agentes determinísticos de supervisão da conciliação.

Eles não alteram o resultado do motor. O papel é diagnosticar qualidade,
performance e parâmetros para que bases grandes sejam tratadas com clareza.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd


@dataclass
class AgentFinding:
    agente: str
    nivel: str
    titulo: str
    detalhe: str


def _finding(agente: str, nivel: str, titulo: str, detalhe: str) -> dict:
    return asdict(AgentFinding(agente, nivel, titulo, detalhe))


def _date_span(df: pd.DataFrame) -> str:
    if df.empty or "_data" not in df.columns:
        return "sem datas"
    dates = pd.to_datetime(df["_data"], errors="coerce")
    if dates.dropna().empty:
        return "sem datas válidas"
    return f"{dates.min().date()} a {dates.max().date()}"


def quality_agent(df_bnk: pd.DataFrame, df_fin: pd.DataFrame) -> list[dict]:
    findings: list[dict] = []
    for nome, df in [("extrato bancário", df_bnk), ("financeiro", df_fin)]:
        if df.empty:
            findings.append(_finding(
                "Qualidade dos Dados", "crítico", f"{nome.title()} vazio",
                "A conciliação depende das duas bases normalizadas.",
            ))
            continue
        if "_historico" in df.columns:
            vazios = int(df["_historico"].astype(str).str.strip().eq("").sum())
            if vazios:
                findings.append(_finding(
                    "Qualidade dos Dados", "atenção", f"Histórico vazio no {nome}",
                    f"{vazios} linha(s) sem histórico podem reduzir a revisão contextual.",
                ))
        if "_valor" in df.columns:
            zeros = int(pd.to_numeric(df["_valor"], errors="coerce").fillna(0).eq(0).sum())
            if zeros:
                findings.append(_finding(
                    "Qualidade dos Dados", "atenção", f"Valores zerados no {nome}",
                    f"{zeros} linha(s) com valor zerado foram identificadas.",
                ))
    return findings


def parameter_agent(df_bnk: pd.DataFrame, df_fin: pd.DataFrame, params: Any) -> list[dict]:
    findings: list[dict] = []
    total_bnk = len(df_bnk)
    total_fin = len(df_fin)
    max_candidates = int(getattr(params, "max_candidates_per_group", 0) or 0)
    timeout = float(getattr(params, "combo_timeout_sec", 0) or 0)
    max_group = int(getattr(params, "max_group_size", 0) or 0)

    if getattr(params, "enable_n_to_one", False):
        n_to_one_matches = int(
            df_bnk["_metodo"].astype(str).str.startswith("N:1").sum()
        ) if "_metodo" in df_bnk.columns else 0
        findings.append(_finding(
            "Sugestão de Parâmetros", "info", "N:1 ativo",
            f"{n_to_one_matches} lançamento(s) bancário(s) conciliados via N:1 (N extratos → 1 financeiro).",
        ))
    if total_bnk >= 800 or total_fin >= 1000:
        findings.append(_finding(
            "Sugestão de Parâmetros", "info", "Base grande detectada",
            f"{total_bnk} linha(s) no banco e {total_fin} no financeiro. "
            f"A busca 1:N está limitada a {max_candidates or 'sem limite'} candidato(s) por grupo.",
        ))
    if max_group >= 20 and (max_candidates == 0 or max_candidates > 40):
        findings.append(_finding(
            "Sugestão de Parâmetros", "atenção", "Busca combinatória ampla",
            f"Grupo máximo {max_group} com {max_candidates} candidatos pode ficar pesado. "
            "Mantenha o grupo alto, mas aumente candidatos gradualmente.",
        ))
    if timeout <= 0:
        findings.append(_finding(
            "Sugestão de Parâmetros", "info", "Timeout desativado",
            "A busca não será interrompida por tempo, priorizando a análise completa.",
        ))
    return findings


def performance_agent(timings: list[dict], df_bnk: pd.DataFrame, df_fin: pd.DataFrame) -> list[dict]:
    findings: list[dict] = []
    if timings:
        slowest = max(timings, key=lambda x: float(x.get("Tempo (s)", 0) or 0))
        findings.append(_finding(
            "Diagnóstico de Performance", "info", "Etapa mais lenta",
            f"{slowest.get('Etapa')}: {slowest.get('Tempo (s)')}s ({slowest.get('Detalhes', '')}).",
        ))
    if "_metodo" in df_bnk.columns:
        grandes = df_bnk["_metodo"].astype(str).str.contains("grupo grande|grupo limitado", case=False, na=False)
        qtd = int(grandes.sum())
        if qtd:
            findings.append(_finding(
                "Diagnóstico de Performance", "atenção", "Grupos combinatórios grandes",
                f"{qtd} lançamento(s) foram enviados para revisão por limite/complexidade.",
            ))
    if "_status" in df_bnk.columns:
        counts = Counter(str(x) for x in df_bnk["_status"])
        revisar = counts.get("REVISAR", 0) + counts.get("REVISAR_COLISAO", 0)
        if revisar:
            findings.append(_finding(
                "Diagnóstico de Performance", "info", "Itens para revisão",
                f"{revisar} lançamento(s) bancário(s) exigem decisão manual.",
            ))
    return findings


def supervisor_agent(
    df_bnk: pd.DataFrame,
    df_fin: pd.DataFrame,
    params: Any,
    timings: list[dict] | None = None,
) -> dict:
    findings = []
    findings.extend(quality_agent(df_bnk, df_fin))
    findings.extend(parameter_agent(df_bnk, df_fin, params))
    findings.extend(performance_agent(timings or [], df_bnk, df_fin))
    return {
        "resumo": {
            "linhas_banco": len(df_bnk),
            "linhas_financeiro": len(df_fin),
            "periodo_banco": _date_span(df_bnk),
            "periodo_financeiro": _date_span(df_fin),
            "n_to_one": "ativo" if getattr(params, "enable_n_to_one", False) else "inativo",
        },
        "findings": findings,
    }
