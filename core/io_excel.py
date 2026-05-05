"""
Leitura robusta de arquivos Excel e CSV sem assumir nenhum layout.

Detecção de formato por magic bytes — ignora a extensão declarada quando
os bytes reais indicam outro formato (ex: .xlsx com conteúdo .xls antigo).
Fallback automático de engine: openpyxl → xlrd e xlrd → openpyxl.
"""
from __future__ import annotations
import datetime
import io
import math
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

# Magic bytes para identificar o formato real independente da extensão
_XLS_MAGIC = b"\xD0\xCF\x11\xE0"  # OLE2 Compound Document → .xls
_ZIP_MAGIC  = b"PK\x03\x04"        # ZIP → .xlsx / .xlsm
_CSV_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin1")


def _sniff_suffix(file: io.BytesIO) -> Optional[str]:
    """Detecta o formato real lendo os primeiros bytes. Retorna None se não reconhecido."""
    pos = file.tell()
    header = file.read(8)
    file.seek(pos)
    if header[:4] == _XLS_MAGIC:
        return ".xls"
    if header[:4] == _ZIP_MAGIC:
        return ".xlsx"
    return None


def _real_suffix(file: Union[str, Path, io.BytesIO], declared: str) -> str:
    """Retorna o sufixo efetivo: detectado por bytes (mais confiável) ou declarado."""
    if isinstance(file, io.BytesIO):
        detected = _sniff_suffix(file)
        if detected:
            return detected
    return declared


def _engines_for(suffix: str) -> List[str]:
    """Lista de engines a tentar, em ordem de preferência para o sufixo."""
    if suffix == ".xls":
        return ["xlrd", "openpyxl"]
    return ["openpyxl", "xlrd"]


def _open_excel_file(file: Union[str, Path, io.BytesIO], suffix: str) -> pd.ExcelFile:
    """
    Abre um ExcelFile tentando engines em sequência.
    Lança ValueError com mensagem amigável se nenhum engine funcionar.
    """
    last_exc: Exception = Exception("Formato desconhecido")
    for engine in _engines_for(suffix):
        if hasattr(file, "seek"):
            file.seek(0)
        try:
            return pd.ExcelFile(file, engine=engine)
        except Exception as e:
            last_exc = e
    raise ValueError(
        "Arquivo não reconhecido como planilha válida. "
        "Verifique se o arquivo não está corrompido, protegido por senha "
        "ou salvo em formato incompatível (ex: arquivo .xls enviado com extensão .xlsx)."
    ) from last_exc


def _read_excel_robust(
    file: Union[str, Path, io.BytesIO],
    sheet_name,
    suffix: str,
    **kwargs,
) -> pd.DataFrame:
    """
    Lê uma planilha Excel tentando engines em sequência.
    Lança ValueError com mensagem amigável se nenhum engine funcionar.
    """
    last_exc: Exception = Exception("Formato desconhecido")
    for engine in _engines_for(suffix):
        if hasattr(file, "seek"):
            file.seek(0)
        try:
            df = pd.read_excel(
                file,
                sheet_name=sheet_name,
                header=None,
                dtype=object,
                engine=engine,
                keep_default_na=False,
                **kwargs,
            )
            return df.apply(lambda col: col.map(_normalise_cell))
        except Exception as e:
            last_exc = e
    raise ValueError(
        "Não foi possível ler os dados da planilha. "
        "Verifique se o arquivo não está corrompido, protegido por senha "
        "ou em formato incompatível."
    ) from last_exc


def get_sheet_names(
    file: Union[str, Path, io.BytesIO],
    suffix: str = ".xlsx",
) -> List[str]:
    if isinstance(file, (str, Path)):
        suffix = Path(file).suffix.lower()
    if suffix == ".csv":
        return ["csv"]
    rs = _real_suffix(file, suffix)
    return _open_excel_file(file, rs).sheet_names


def _normalise_cell(v):
    """
    Preserva objetos datetime nativos do openpyxl (evita inversão DD/MM via str()).
    Converte None e NaN para "". Converte todo o resto para str.
    """
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v
    if v is None:
        return ""
    try:
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    return str(v)


def _read_csv_robust(
    file: Union[str, Path, io.BytesIO],
    **kwargs,
) -> pd.DataFrame:
    """
    Lê CSV tentando encodings comuns em relatórios bancários brasileiros.
    """
    last_exc: Exception = Exception("Formato CSV desconhecido")
    for encoding in _CSV_ENCODINGS:
        if hasattr(file, "seek"):
            file.seek(0)
        try:
            return pd.read_csv(file, encoding=encoding, **kwargs)
        except UnicodeDecodeError as e:
            last_exc = e
            continue
    raise ValueError(
        "Não foi possível ler o CSV. Verifique se o arquivo não está corrompido "
        "ou salvo em uma codificação incompatível."
    ) from last_exc


def _ensure_has_header_row(df: pd.DataFrame, skip_rows: int) -> None:
    if df.empty:
        raise ValueError(
            f"Nenhuma linha encontrada após ignorar {skip_rows} linha(s). "
            "Revise a aba selecionada ou a quantidade de linhas de cabeçalho."
        )


def read_raw(
    file: Union[str, Path, io.BytesIO],
    sheet_name: Optional[str] = None,
    skip_rows: int = 0,
    suffix: str = ".xlsx",
) -> pd.DataFrame:
    if isinstance(file, (str, Path)):
        suffix = Path(file).suffix.lower()
    if suffix == ".csv":
        df = _read_csv_robust(file, header=None, dtype=str, keep_default_na=False)
    else:
        # dtype=object preserva datetime nativos do openpyxl.
        # Com dtype=str o pandas chamaria str() nas datas, retornando o formato
        # de exibição da célula Excel (ex: "03/10/2026" para MM/DD/YYYY),
        # o que causaria inversão DD↔MM no parser brasileiro.
        rs = _real_suffix(file, suffix)
        df = _read_excel_robust(file, sheet_name=sheet_name or 0, suffix=rs)

    if skip_rows > 0:
        df = df.iloc[skip_rows:].reset_index(drop=True)
    _ensure_has_header_row(df, skip_rows)
    df.columns = [str(c).strip() for c in df.iloc[0]]
    df = df.iloc[1:].reset_index(drop=True)
    # Descarta colunas sem nome (células vazias no cabeçalho — artefato comum do Excel)
    df = df.loc[:, df.columns != ""]
    if len(set(df.columns)) != len(df.columns):
        duplicated = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
        raise ValueError(
            "A planilha possui cabeçalhos duplicados: "
            + ", ".join(str(c) for c in duplicated)
            + ". Renomeie as colunas duplicadas antes de importar."
        )
    return df


def preview_raw(
    file: Union[str, Path, io.BytesIO],
    sheet_name: Optional[str] = None,
    n_rows: int = 20,
    suffix: str = ".xlsx",
    skip_rows: int = 0,
) -> pd.DataFrame:
    if isinstance(file, (str, Path)):
        suffix = Path(file).suffix.lower()
    if suffix == ".csv":
        df = _read_csv_robust(file, header=None, dtype=str, keep_default_na=False,
                              skiprows=skip_rows, nrows=n_rows)
    else:
        rs = _real_suffix(file, suffix)
        df = _read_excel_robust(file, sheet_name=sheet_name or 0, suffix=rs,
                                skiprows=skip_rows if skip_rows > 0 else None, nrows=n_rows)
    if df.empty:
        raise ValueError(
            f"Nenhuma linha encontrada após ignorar {skip_rows} linha(s). "
            "Revise a aba selecionada ou a quantidade de linhas de cabeçalho."
        )
    df.columns = [f"Col {i}" for i in range(df.shape[1])]
    return df


def get_columns(
    file: Union[str, Path, io.BytesIO],
    sheet_name: Optional[str] = None,
    skip_rows: int = 0,
    suffix: str = ".xlsx",
) -> List[str]:
    df = read_raw(file, sheet_name=sheet_name, skip_rows=skip_rows, suffix=suffix)
    return list(df.columns)
