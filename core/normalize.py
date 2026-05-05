"""
Normalização de DataFrames brutos para o formato interno de conciliação.
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation
from typing import List, Optional
import datetime

import pandas as pd

from .mapping import ExtratoMapping, FinanceiroMapping, ValorModalidade, FinanceiroModalidade
from .params import ConciliacaoParams
from .io_excel import read_raw

STATUS_CONCILIADO          = "CONCILIADO"
STATUS_CONCILIADO_MANUAL   = "CONCILIADO_MANUAL"
STATUS_REVISAR             = "REVISAR"
STATUS_REVISAR_COLISAO     = "REVISAR_COLISAO"
STATUS_IGNORADO_SEM_PAR    = "IGNORADO_SEM_CONTRAPARTIDA"
STATUS_IGNORADO_USUARIO    = "IGNORADO_USUARIO"
STATUS_SEM_PAREAMENTO      = "SEM_PAREAMENTO"
STATUS_PARCIAL             = "PARCIALMENTE CONCILIADO"
STATUS_PENDENTE_PARCIAL    = "PENDENTE DE CONCILIAÇÃO PARCIAL"


_DD_MM_RE = re.compile(r"^\d{1,2}[\/\-.]\d{1,2}$")
_EXCEL_SERIAL_MIN = 1
_EXCEL_SERIAL_MAX = 60000


def _parse_date(value, default_year: int = 0) -> Optional[datetime.date]:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if str(value).strip() == "":
        return None
    v = str(value).strip()
    if v.lower() in {"nat", "nan", "none"}:
        return None
    v = re.sub(r"\s+", " ", v)
    if _DD_MM_RE.match(v):
        year = default_year if default_year else datetime.date.today().year
        sep = "/" if "/" in v else "-" if "-" in v else "."
        dia, mes = v.split(sep)
        v = f"{dia}/{mes}/{year}"
    if re.fullmatch(r"\d+(\.0+)?", v):
        serial = int(float(v))
        if _EXCEL_SERIAL_MIN <= serial <= _EXCEL_SERIAL_MAX:
            try:
                return (datetime.date(1899, 12, 30) + datetime.timedelta(days=serial))
            except Exception:
                pass
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S",
                "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(v, dayfirst=True, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def _parse_decimal(value: str) -> Optional[Decimal]:
    if not value or str(value).strip() in ("", "None"):
        return None
    v = str(value).strip()
    v = re.sub(r"[R$\s]", "", v)
    if "," in v and "." in v:
        # O separador que aparece por último é o decimal.
        # Brasileiro "1.234,56": vírgula por último → remove pontos, troca vírgula
        # Inglês "1,234.56": ponto por último → remove vírgulas
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    elif "," in v:
        v = v.replace(",", ".")
    # Somente ponto: já é formato válido para Decimal (ex: "123.25")
    try:
        return Decimal(v).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _concat_historico(row, cols: List[str], sep: str, prefix: str = "") -> str:
    """Aceita tanto pd.Series quanto dict (to_dict('records'))."""
    parts = []
    for c in cols:
        if c not in row:        # funciona para dict e Series
            continue
        v = row[c]
        if isinstance(v, datetime.date):
            s = v.strftime("%d/%m/%Y")
        else:
            s = str(v).strip() if v is not None else ""
        if s:
            parts.append(s)
    hist = sep.join(parts)
    if prefix:
        hist = prefix + hist
    return hist


def _should_discard(historico: str, patterns: List[str]) -> bool:
    h = historico.strip().upper()
    for p in patterns:
        if h.startswith(p.upper()):
            return True
    return False


def normalize_extrato(file, mapping: ExtratoMapping, params: ConciliacaoParams, suffix: str = ".xlsx") -> pd.DataFrame:
    df = read_raw(file, sheet_name=mapping.sheet_name, skip_rows=mapping.skip_rows, suffix=suffix)

    # Dica 9: remove linhas sem data antes de entrar no loop
    if mapping.col_data in df.columns:
        df = df[df[mapping.col_data].astype(str).str.strip().ne("")]

    records = []
    seq = 0
    # Dica 5: to_dict("records") evita overhead de pd.Series por linha do iterrows
    for row in df.to_dict("records"):
        hist = _concat_historico(row, mapping.col_historico, params.hist_separator)
        if _should_discard(hist, params.discard_patterns):
            continue
        data = _parse_date(row.get(mapping.col_data, ""), params.default_year)
        if data is None:
            continue
        valor = _extract_valor_extrato(row, mapping, params)
        if valor is None or valor == Decimal("0.00"):
            continue
        seq += 1
        row_id = str(row.get(mapping.col_id, "")).strip() if mapping.col_id else ""
        if not row_id:
            row_id = f"BNK_{seq:04d}"
        rec = {
            "_id": row_id, "_data": data, "_valor": valor,
            "_valor_f": float(valor),   # dica 6: float pré-computado para a combinatória
            "_historico": hist, "_classif": "",
            "_status": STATUS_SEM_PAREAMENTO, "_metodo": "", "_ids_fin": "",
        }
        for c in df.columns:
            rec[c] = row.get(c, "")
        rec[mapping.col_data] = data
        records.append(rec)
    return pd.DataFrame(records)


def normalize_financeiro(file, mapping: FinanceiroMapping, params: ConciliacaoParams, suffix: str = ".xlsx") -> pd.DataFrame:
    df = read_raw(file, sheet_name=mapping.sheet_name, skip_rows=mapping.skip_rows, suffix=suffix)

    # Dica 9: remove linhas sem data antes de entrar no loop
    if mapping.col_data in df.columns:
        df = df[df[mapping.col_data].astype(str).str.strip().ne("")]

    records = []
    seq = 0
    # Dica 5: to_dict("records") evita overhead de pd.Series por linha do iterrows
    for row in df.to_dict("records"):
        sep = mapping.hist_separator or params.hist_separator
        prefix = mapping.hist_prefix or params.hist_prefix
        hist = _concat_historico(row, mapping.col_historico, sep, prefix)
        if _should_discard(hist, params.discard_patterns):
            continue
        data = _parse_date(row.get(mapping.col_data, ""), params.default_year)
        if data is None:
            continue
        valor = _extract_valor_financeiro(row, mapping, params)
        if valor is None or valor == Decimal("0.00"):
            continue
        seq += 1
        row_id = str(row.get(mapping.col_id, "")).strip() if mapping.col_id else ""
        if not row_id:
            row_id = f"FIN_{seq:04d}"
        classif = ""
        if mapping.col_classificacao and mapping.col_classificacao in row:
            classif = str(row[mapping.col_classificacao]).strip()
        rec = {
            "_id": row_id, "_data": data, "_valor": valor,
            "_valor_f": float(valor),   # dica 6: float pré-computado para a combinatória
            "_historico": hist, "_classif": classif,
            "_status": STATUS_IGNORADO_SEM_PAR, "_metodo": "", "_id_bnk": "",
        }
        for c in df.columns:
            rec[c] = row.get(c, "")
        rec[mapping.col_data] = data
        records.append(rec)
    return pd.DataFrame(records)


def _enum_val(m) -> str:
    """Retorna o valor string de uma modalidade enum ou string.
    Robusto a module reloads do Streamlit (hot-reload recria a classe enum,
    quebrando comparações por identidade em Python 3.11+).
    """
    return m.value if hasattr(m, "value") else str(m)


def _extract_valor_extrato(row, mapping, params):
    if _enum_val(mapping.valor_modalidade) == ValorModalidade.COLUNA_UNICA.value:
        return _parse_decimal(row.get(mapping.col_valor, ""))
    pagamentos = _parse_decimal(row.get(mapping.col_debito, "")) or Decimal("0")
    recebimentos = _parse_decimal(row.get(mapping.col_credito, "")) or Decimal("0")
    result = abs(recebimentos) - abs(pagamentos)
    return result if result != Decimal("0") else None


def _extract_valor_financeiro(row, mapping, params):
    if _enum_val(mapping.valor_modalidade) == ValorModalidade.COLUNA_UNICA.value:
        v = _parse_decimal(row.get(mapping.col_valor, ""))
    else:
        pagamentos = _parse_decimal(row.get(mapping.col_debito, "")) or Decimal("0")
        recebimentos = _parse_decimal(row.get(mapping.col_credito, "")) or Decimal("0")
        v = abs(recebimentos) - abs(pagamentos)
        if v == Decimal("0"):
            return None
    if v is None:
        return None
    mod = _enum_val(mapping.modalidade)
    if mod == FinanceiroModalidade.RECEBIMENTOS.value:
        v = abs(v)
    elif mod == FinanceiroModalidade.PAGAMENTOS.value:
        v = -abs(v)
    return v
