"""
Exportação e utilitários do De-Para contábil.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any, Dict, List


CONTA_SEM_DEPARA = "4427"
STATUS_DEPARA_OK = "De x Para efetuado corretamente"
STATUS_SEM_CLASSIFICACAO = "Não possui classificação financeira"
STATUS_NAO_PARAMETRIZADA = "Classificação financeira não parametrizada"


def export_depara_csv(depara_rows: List[dict]) -> bytes:
    """
    Exporta lista de dicts {classif, conta_contabil} como CSV bytes.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["classif", "conta_contabil"])
    writer.writeheader()
    for row in depara_rows:
        writer.writerow({
            "classif": row.get("classif", ""),
            "conta_contabil": row.get("conta_contabil", ""),
        })
    return buf.getvalue().encode("utf-8")


def get_depara_dict(depara_rows: List[dict]) -> Dict[str, str]:
    """
    Converte lista de De-Para para dict {classif: conta_contabil}.
    """
    return {
        str(r["classif"]).strip(): str(r.get("conta_contabil", "")).strip()
        for r in depara_rows
        if str(r.get("classif", "")).strip()
    }


def _normaliza_classificacao(valor: Any) -> str:
    return str(valor or "").strip()


def build_depara_index(depara: Dict[str, Any]) -> Dict[str, str]:
    """
    Cria índice case-insensitive sem alterar a classificação exibida no relatório.
    Também aceita o formato antigo {classif: (débito, crédito)} para manter
    compatibilidade durante a migração.
    """
    index: Dict[str, str] = {}
    for classif, conta in (depara or {}).items():
        key = _normaliza_classificacao(classif).casefold()
        if not key:
            continue
        if isinstance(conta, (tuple, list)):
            conta_val = conta[0] if conta else ""
        else:
            conta_val = conta
        index[key] = str(conta_val or "").strip()
    return index


def _index_depara(depara: Dict[str, Any]) -> Dict[str, str]:
    return build_depara_index(depara)


def resolver_conta_depara(classificacao: Any, depara: Dict[str, Any]) -> tuple[str, str]:
    """
    Resolve a conta contábil pela classificação financeira.
    Retorna (conta, status). Quando não houver classificação ou parametrização,
    aplica a conta padrão 4427 conforme regra do De x Para.
    """
    classif = _normaliza_classificacao(classificacao)
    if not classif:
        return CONTA_SEM_DEPARA, STATUS_SEM_CLASSIFICACAO

    conta = _index_depara(depara).get(classif.casefold(), "")
    if conta:
        return conta, STATUS_DEPARA_OK
    return CONTA_SEM_DEPARA, STATUS_NAO_PARAMETRIZADA


def resolver_conta_depara_indexed(classificacao: Any, depara_index: Dict[str, str]) -> tuple[str, str]:
    """
    Resolve a conta usando um índice de De x Para já preparado.
    Mantém a mesma regra de status/conta 4427, evitando reconstruir o índice por linha.
    """
    classif = _normaliza_classificacao(classificacao)
    if not classif:
        return CONTA_SEM_DEPARA, STATUS_SEM_CLASSIFICACAO

    conta = (depara_index or {}).get(classif.casefold(), "")
    if conta:
        return conta, STATUS_DEPARA_OK
    return CONTA_SEM_DEPARA, STATUS_NAO_PARAMETRIZADA


def aplicar_depara_contabil(
    classificacao: Any,
    valor: Any,
    depara: Dict[str, Any],
    conta_banco: str,
) -> tuple[str, str, str]:
    """
    Aplica a regra contábil do De x Para e retorna (débito, crédito, status).
    Valor positivo: banco no débito e conta De x Para no crédito.
    Valor negativo: conta De x Para no débito e banco no crédito.
    """
    conta_depara, status = resolver_conta_depara(classificacao, depara)
    valor_num = float(valor) if isinstance(valor, Decimal) else float(valor or 0)
    conta_banco = str(conta_banco or "").strip()

    if valor_num >= 0:
        return conta_banco, conta_depara, status
    return conta_depara, conta_banco, status


def aplicar_depara_contabil_indexed(
    classificacao: Any,
    valor: Any,
    depara_index: Dict[str, str],
    conta_banco: str,
) -> tuple[str, str, str]:
    """
    Versão otimizada para processamento em lote: recebe o índice pronto.
    """
    conta_depara, status = resolver_conta_depara_indexed(classificacao, depara_index)
    valor_num = float(valor) if isinstance(valor, Decimal) else float(valor or 0)
    conta_banco = str(conta_banco or "").strip()

    if valor_num >= 0:
        return conta_banco, conta_depara, status
    return conta_depara, conta_banco, status

