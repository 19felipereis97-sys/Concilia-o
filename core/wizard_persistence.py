"""
Persiste a configuração do wizard (mapeamento de colunas, sheet, skip, parâmetros)
em config/wizard_config.json para sobreviver a redeployments no Streamlit Cloud.

Fluxo:
  - apply_wizard_config() é chamado no startup do app (uma vez por sessão).
  - save_wizard_config() é chamado ao final do passo de parâmetros (step_params).
"""
from __future__ import annotations
import dataclasses
import json
from pathlib import Path
from typing import Any

from core.mapping import (
    ExtratoMapping, FinanceiroMapping,
    ValorModalidade, FinanceiroModalidade,
)

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "wizard_config.json"

# Chaves simples (str / int / float / list) a salvar direto do session_state
_CONFIG_KEYS: list[str] = [
    "extrato_sheet", "extrato_skip", "extrato_suffix",
    "fin_sheet", "fin_skip", "fin_suffix",
    "fin2_sheet", "fin2_skip", "fin2_suffix",
    "fin_modalidade_str",
    "default_year",
]

# Chaves de widgets do wizard que queremos pré-popular (mesmo valor do widget)
_WIDGET_KEYS: list[str] = [
    # extrato
    "bnk_col_data", "bnk_col_hist", "bnk_valor_mod",
    "bnk_col_valor", "bnk_col_deb", "bnk_col_cre",
    # financeiro (modalidade única)
    "fin_col_data", "fin_col_hist", "fin_hist_prefix", "fin_hist_sep",
    "fin_valor_mod", "fin_col_valor", "fin_col_deb", "fin_col_cre", "fin_col_classif",
    # recebimentos (modalidade separados)
    "rec_col_data", "rec_col_hist", "rec_hist_prefix", "rec_hist_sep",
    "rec_valor_mod", "rec_col_valor", "rec_col_deb", "rec_col_cre", "rec_col_classif",
    # pagamentos (modalidade separados)
    "pag_col_data", "pag_col_hist", "pag_hist_prefix", "pag_hist_sep",
    "pag_valor_mod", "pag_col_valor", "pag_col_deb", "pag_col_cre", "pag_col_classif",
    # parâmetros de conciliação
    "param_tol", "param_group", "param_combo_timeout",
    "param_offsets", "param_discard",
]

# Chaves cujos valores são dataclasses a serializar / desserializar
_MAPPING_KEYS: dict[str, type] = {
    "extrato_mapping": ExtratoMapping,
    "fin_mapping": FinanceiroMapping,
    "fin2_mapping": FinanceiroMapping,
}


def save_wizard_config(session_state: Any) -> None:
    """Salva as configurações relevantes do session_state em disco."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}

    for key in _CONFIG_KEYS + _WIDGET_KEYS:
        val = session_state.get(key)
        if val is not None:
            data[key] = val

    for key, cls in _MAPPING_KEYS.items():
        obj = session_state.get(key)
        if obj is not None and dataclasses.is_dataclass(obj):
            data[key] = dataclasses.asdict(obj)

    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Filesystem somente-leitura em alguns ambientes — ignora silenciosamente


def _load_raw() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _reconstruct_mapping(cls: type, d: dict) -> Any:
    try:
        if "valor_modalidade" in d:
            d = dict(d, valor_modalidade=ValorModalidade(d["valor_modalidade"]))
        if "modalidade" in d:
            d = dict(d, modalidade=FinanceiroModalidade(d["modalidade"]))
        return cls(**d)
    except Exception:
        return None


def apply_wizard_config(session_state: Any) -> None:
    """
    Pré-preenche o session_state com a config salva.
    Só aplica chaves que ainda NÃO estão no session_state para não sobrescrever
    o que o usuário acabou de configurar na sessão atual.
    """
    data = _load_raw()
    if not data:
        return

    for key in _CONFIG_KEYS + _WIDGET_KEYS:
        if key in data and key not in session_state:
            session_state[key] = data[key]

    for key, cls in _MAPPING_KEYS.items():
        if key in data and key not in session_state:
            obj = _reconstruct_mapping(cls, data[key])
            if obj is not None:
                session_state[key] = obj
