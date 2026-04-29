"""
Dataclasses que representam o mapeamento escolhido pelo usuário.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ValorModalidade(str, Enum):
    COLUNA_UNICA = "coluna_unica"
    DOIS_COLUNAS = "dois_colunas"


class FinanceiroModalidade(str, Enum):
    COMPLETO = "completo"
    RECEBIMENTOS = "recebimentos"
    PAGAMENTOS = "pagamentos"
    SEPARADOS = "separados"


@dataclass
class ExtratoMapping:
    col_data: str
    col_historico: List[str]
    valor_modalidade: ValorModalidade = ValorModalidade.COLUNA_UNICA
    col_valor: Optional[str] = None
    col_debito: Optional[str] = None
    col_credito: Optional[str] = None
    col_id: Optional[str] = None
    colunas_extras: List[str] = field(default_factory=list)
    skip_rows: int = 0
    sheet_name: Optional[str] = None


@dataclass
class FinanceiroMapping:
    col_data: str
    col_historico: List[str]
    valor_modalidade: ValorModalidade = ValorModalidade.COLUNA_UNICA
    col_valor: Optional[str] = None
    col_debito: Optional[str] = None
    col_credito: Optional[str] = None
    col_id: Optional[str] = None
    col_classificacao: Optional[str] = None
    colunas_extras: List[str] = field(default_factory=list)
    skip_rows: int = 0
    sheet_name: Optional[str] = None
    modalidade: FinanceiroModalidade = FinanceiroModalidade.COMPLETO
    hist_prefix: str = ""
    hist_separator: str = " - "
