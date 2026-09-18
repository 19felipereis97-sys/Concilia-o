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

# ── Chaves por escopo ──────────────────────────────────────────────────────────

_EXTRATO_CONFIG_KEYS: list[str] = [
    "extrato_sheet", "extrato_skip", "extrato_suffix", "banco_layout_conciliacao",
]
_EXTRATO_WIDGET_KEYS: list[str] = [
    "bnk_col_data", "bnk_col_hist", "bnk_valor_mod",
    "bnk_col_valor", "bnk_col_deb", "bnk_col_cre",
]
_EXTRATO_MAPPING_KEYS: dict[str, type] = {"extrato_mapping": ExtratoMapping}

_FIN_CONFIG_KEYS: list[str] = [
    "fin_sheet", "fin_skip", "fin_suffix",
    "fin2_sheet", "fin2_skip", "fin2_suffix",
    "fin_modalidade_str",
]
_FIN_WIDGET_KEYS: list[str] = [
    "fin_col_data", "fin_col_hist", "fin_hist_prefix", "fin_hist_sep",
    "fin_valor_mod", "fin_col_valor", "fin_col_deb", "fin_col_cre", "fin_col_classif",
    "rec_col_data", "rec_col_hist", "rec_hist_prefix", "rec_hist_sep",
    "rec_valor_mod", "rec_col_valor", "rec_col_deb", "rec_col_cre", "rec_col_classif",
    "pag_col_data", "pag_col_hist", "pag_hist_prefix", "pag_hist_sep",
    "pag_valor_mod", "pag_col_valor", "pag_col_deb", "pag_col_cre", "pag_col_classif",
]
_FIN_MAPPING_KEYS: dict[str, type] = {
    "fin_mapping": FinanceiroMapping,
    "fin2_mapping": FinanceiroMapping,
}

_PARAM_WIDGET_KEYS: list[str] = [
    "param_tol", "param_group", "param_max_candidates", "param_enable_n_to_one",
    "param_n_to_one_candidates", "param_combo_timeout",
    "param_offsets", "param_discard",
]

# Chaves legadas (usadas no snapshot completo para backward-compat)
_CONFIG_KEYS: list[str] = (
    _EXTRATO_CONFIG_KEYS + _FIN_CONFIG_KEYS + ["default_year"]
)
_WIDGET_KEYS: list[str] = (
    _EXTRATO_WIDGET_KEYS + _FIN_WIDGET_KEYS + _PARAM_WIDGET_KEYS
)
_MAPPING_KEYS: dict[str, type] = {
    **_EXTRATO_MAPPING_KEYS,
    **_FIN_MAPPING_KEYS,
}


# ── Derivação de widget keys a partir de mapping dicts ────────────────────────

def _extrato_mapping_to_widgets(m: dict) -> dict:
    """Deriva os widget keys de extrato a partir de um dict de ExtratoMapping."""
    widgets: dict[str, Any] = {}
    if m.get("col_data"):
        widgets["bnk_col_data"] = m["col_data"]
    if "col_historico" in m:
        widgets["bnk_col_hist"] = m["col_historico"] or []
    vm = m.get("valor_modalidade", "")
    if vm == ValorModalidade.COLUNA_UNICA or vm == "coluna_unica":
        widgets["bnk_valor_mod"] = "Coluna única (com sinal)"
        if m.get("col_valor"):
            widgets["bnk_col_valor"] = m["col_valor"]
    else:
        widgets["bnk_valor_mod"] = "Duas colunas (pagamentos e recebimentos)"
        if m.get("col_debito"):
            widgets["bnk_col_deb"] = m["col_debito"]
        if m.get("col_credito"):
            widgets["bnk_col_cre"] = m["col_credito"]
    return widgets


def _fin_mapping_to_widgets(m: dict, prefix: str) -> dict:
    """Deriva os widget keys de financeiro a partir de um dict de FinanceiroMapping."""
    widgets: dict[str, Any] = {}
    if m.get("col_data"):
        widgets[f"{prefix}col_data"] = m["col_data"]
    if "col_historico" in m:
        widgets[f"{prefix}col_hist"] = m["col_historico"] or []
    if "hist_prefix" in m:
        widgets[f"{prefix}hist_prefix"] = m.get("hist_prefix", "")
    if "hist_separator" in m:
        widgets[f"{prefix}hist_sep"] = m.get("hist_separator", " - ")
    vm = m.get("valor_modalidade", "")
    if vm == ValorModalidade.COLUNA_UNICA or vm == "coluna_unica":
        widgets[f"{prefix}valor_mod"] = "Coluna única"
        if m.get("col_valor"):
            widgets[f"{prefix}col_valor"] = m["col_valor"]
    else:
        widgets[f"{prefix}valor_mod"] = "Duas colunas (pagamentos e recebimentos)"
        if m.get("col_debito"):
            widgets[f"{prefix}col_deb"] = m["col_debito"]
        if m.get("col_credito"):
            widgets[f"{prefix}col_cre"] = m["col_credito"]
    if m.get("col_classificacao"):
        widgets[f"{prefix}col_classif"] = m["col_classificacao"]
    return widgets


# ── Snapshots por escopo ───────────────────────────────────────────────────────

def get_wizard_config_snapshot(session_state: Any) -> dict[str, Any]:
    """Snapshot completo (extrato + financeiro + params) — usado para wizard_config.json."""
    data: dict[str, Any] = {}
    for key in _CONFIG_KEYS + _WIDGET_KEYS:
        val = session_state.get(key)
        if val is not None:
            data[key] = val
    for key, cls in _MAPPING_KEYS.items():
        obj = session_state.get(key)
        if obj is not None and dataclasses.is_dataclass(obj):
            data[key] = dataclasses.asdict(obj)
    return data


def get_extrato_snapshot(session_state: Any) -> dict[str, Any]:
    """Snapshot apenas do mapeamento de colunas do extrato.

    Aba, linhas ignoradas e formato do arquivo pertencem à configuração do
    arquivo atual (Etapa 3), não ao layout reutilizável da Etapa 5.
    """
    data: dict[str, Any] = {}
    for key in _EXTRATO_WIDGET_KEYS:
        val = session_state.get(key)
        if val is not None:
            data[key] = val
    # Garante widget keys mesmo se só o objeto de mapping estiver disponível.
    mapping = session_state.get("extrato_mapping")
    if mapping is not None and dataclasses.is_dataclass(mapping):
        for k, v in _extrato_mapping_to_widgets(dataclasses.asdict(mapping)).items():
            if k not in data:
                data[k] = v
    return data


def get_fin_snapshot(session_state: Any) -> dict[str, Any]:
    """Snapshot apenas do mapeamento de colunas do financeiro.

    As abas, linhas ignoradas, sufixos e modalidade continuam sendo definidos
    pelo arquivo atual nas etapas anteriores.
    """
    data: dict[str, Any] = {}
    for key in _FIN_WIDGET_KEYS:
        val = session_state.get(key)
        if val is not None:
            data[key] = val
    # Garante widget keys derivando dos objetos de mapping.
    modalidade_str = session_state.get("fin_modalidade_str", "COMPLETO")
    fin_mapping = session_state.get("fin_mapping")
    if fin_mapping is not None and dataclasses.is_dataclass(fin_mapping):
        prefix = "rec_" if modalidade_str == "SEPARADOS" else "fin_"
        for k, v in _fin_mapping_to_widgets(dataclasses.asdict(fin_mapping), prefix).items():
            if k not in data:
                data[k] = v
    fin2_mapping = session_state.get("fin2_mapping")
    if fin2_mapping is not None and dataclasses.is_dataclass(fin2_mapping):
        for k, v in _fin_mapping_to_widgets(dataclasses.asdict(fin2_mapping), "pag_").items():
            if k not in data:
                data[k] = v
    return data


def apply_extrato_template_data(
    session_state: Any,
    data: dict,
    *,
    overwrite: bool = True,
) -> None:
    """Aplica somente o mapeamento da Etapa 5 de um template de extrato.

    Também aceita templates antigos que continham ``extrato_mapping``, mas
    ignora deliberadamente sheet, skip, sufixo e parâmetros da conciliação.
    """
    if not data:
        return
    widgets: dict[str, Any] = {}
    mapping = data.get("extrato_mapping")
    if isinstance(mapping, dict):
        widgets.update(_extrato_mapping_to_widgets(mapping))
    widgets.update({key: data[key] for key in _EXTRATO_WIDGET_KEYS if key in data})
    for key, value in widgets.items():
        if overwrite or key not in session_state:
            session_state[key] = value


def apply_fin_template_data(
    session_state: Any,
    data: dict,
    *,
    overwrite: bool = True,
) -> None:
    """Aplica somente os mapeamentos da Etapa 6 de um template financeiro.

    Templates antigos são convertidos a partir dos objetos de mapping, sem
    alterar modalidade, abas, linhas ignoradas ou sufixos do arquivo atual.
    """
    if not data:
        return
    widgets: dict[str, Any] = {}
    source_mode = data.get("fin_modalidade_str", "COMPLETO")
    fin_mapping = data.get("fin_mapping")
    if isinstance(fin_mapping, dict):
        prefix = "rec_" if source_mode == "SEPARADOS" else "fin_"
        widgets.update(_fin_mapping_to_widgets(fin_mapping, prefix))
    fin2_mapping = data.get("fin2_mapping")
    if isinstance(fin2_mapping, dict):
        widgets.update(_fin_mapping_to_widgets(fin2_mapping, "pag_"))
    widgets.update({key: data[key] for key in _FIN_WIDGET_KEYS if key in data})
    for key, value in widgets.items():
        if overwrite or key not in session_state:
            session_state[key] = value


# ── Aplicação de snapshots ─────────────────────────────────────────────────────

def save_wizard_config(session_state: Any) -> None:
    """Salva as configurações relevantes do session_state em disco."""
    data = get_wizard_config_snapshot(session_state)
    sig = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    if session_state.get("_wizard_cfg_sig") == sig:
        return  # nada mudou — evita I/O desnecessário durante polling
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_raw()
    if "_doc" in existing:
        data = {"_doc": existing["_doc"], **data}
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        session_state["_wizard_cfg_sig"] = sig
    except OSError:
        pass


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


def apply_wizard_config_data(session_state: Any, data: dict, *, overwrite: bool = False) -> None:
    """
    Aplica snapshot completo no session_state.
    overwrite=False preserva valores já definidos na sessão.
    """
    if not data:
        return
    for key in _CONFIG_KEYS + _WIDGET_KEYS:
        if key in data and (overwrite or key not in session_state):
            session_state[key] = data[key]
    for key, cls in _MAPPING_KEYS.items():
        if key in data and (overwrite or key not in session_state):
            obj = _reconstruct_mapping(cls, data[key])
            if obj is not None:
                session_state[key] = obj
    # Deriva widget keys dos mapping dicts (compatível com templates antigos)
    extrato_m = data.get("extrato_mapping")
    if isinstance(extrato_m, dict):
        for k, v in _extrato_mapping_to_widgets(extrato_m).items():
            if overwrite or k not in session_state:
                session_state[k] = v
    modalidade_str = data.get("fin_modalidade_str") or session_state.get("fin_modalidade_str", "COMPLETO")
    fin_m = data.get("fin_mapping")
    if isinstance(fin_m, dict):
        prefix = "rec_" if modalidade_str == "SEPARADOS" else "fin_"
        for k, v in _fin_mapping_to_widgets(fin_m, prefix).items():
            if overwrite or k not in session_state:
                session_state[k] = v
    fin2_m = data.get("fin2_mapping")
    if isinstance(fin2_m, dict):
        for k, v in _fin_mapping_to_widgets(fin2_m, "pag_").items():
            if overwrite or k not in session_state:
                session_state[k] = v


def apply_fin_config_data(session_state: Any, data: dict, *, overwrite: bool = True) -> None:
    """Aplica apenas as chaves financeiras de um snapshot (para auto-load por empresa)."""
    if not data:
        return
    for key in _FIN_CONFIG_KEYS + _FIN_WIDGET_KEYS:
        if key in data and (overwrite or key not in session_state):
            session_state[key] = data[key]
    for key, cls in _FIN_MAPPING_KEYS.items():
        if key in data and (overwrite or key not in session_state):
            obj = _reconstruct_mapping(cls, data[key])
            if obj is not None:
                session_state[key] = obj
    # Deriva widget keys dos mapping dicts
    modalidade_str = data.get("fin_modalidade_str") or session_state.get("fin_modalidade_str", "COMPLETO")
    fin_m = data.get("fin_mapping")
    if isinstance(fin_m, dict):
        prefix = "rec_" if modalidade_str == "SEPARADOS" else "fin_"
        for k, v in _fin_mapping_to_widgets(fin_m, prefix).items():
            if overwrite or k not in session_state:
                session_state[k] = v
    fin2_m = data.get("fin2_mapping")
    if isinstance(fin2_m, dict):
        for k, v in _fin_mapping_to_widgets(fin2_m, "pag_").items():
            if overwrite or k not in session_state:
                session_state[k] = v


def apply_wizard_config(session_state: Any) -> None:
    """
    Pré-preenche o session_state com a config salva.
    Só aplica chaves que ainda NÃO estão no session_state.
    """
    apply_wizard_config_data(session_state, _load_raw(), overwrite=False)
