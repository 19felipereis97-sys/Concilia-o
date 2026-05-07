from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.params import ConciliacaoParams
from core.mapping import ExtratoMapping, FinanceiroMapping, ValorModalidade, FinanceiroModalidade

_CONFIG_PATH = PROJECT_ROOT / "config" / "wizard_config.json"

BASE_DIR = Path(r"C:\Users\felipe.r\Desktop\Teste Cabine")
EXTRATO_PATH = BASE_DIR / "Extrato Bradesco.xlsx"
FINANCEIRO_PATH = BASE_DIR / "Sig Bradesco.xlsx"


def _load_json() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def make_params() -> ConciliacaoParams:
    cfg = _load_json()
    offsets = [int(x.strip()) for x in cfg.get("param_offsets", "0,1,-1,2,-2").split(",") if x.strip()]
    discard = [x.strip() for x in cfg.get("param_discard", "SDO\nSALDO\nS/D\nSALDO ANTERIOR\nSALDO DO DIA").splitlines() if x.strip()]
    return ConciliacaoParams(
        default_year=int(cfg.get("default_year", 2025)),
        max_group_size=int(cfg.get("param_group", 30)),
        max_candidates_per_group=int(cfg.get("param_max_candidates", 40)),
        n_to_one_max_candidates=int(cfg.get("param_n_to_one_candidates", cfg.get("param_max_candidates", 40))),
        combo_timeout_sec=float(cfg.get("param_combo_timeout", 1.0)),
        value_tolerance_cents=int(cfg.get("param_tol", 0)),
        date_offsets=offsets,
        discard_patterns=discard,
        enable_n_to_one=bool(cfg.get("param_enable_n_to_one", False)),
    )


def make_extrato_mapping() -> ExtratoMapping:
    m = _load_json().get("extrato_mapping", {})
    return ExtratoMapping(
        col_data=m.get("col_data", "Data"),
        col_historico=m.get("col_historico", ["Lançamento"]),
        valor_modalidade=ValorModalidade(m.get("valor_modalidade", "coluna_unica")),
        col_valor=m.get("col_valor"),
        col_debito=m.get("col_debito"),
        col_credito=m.get("col_credito"),
        skip_rows=int(m.get("skip_rows", 8)),
        sheet_name=m.get("sheet_name", "Planilha1"),
    )


def make_financeiro_mapping() -> FinanceiroMapping:
    m = _load_json().get("fin_mapping", {})
    return FinanceiroMapping(
        col_data=m.get("col_data", "BAIXA"),
        col_historico=m.get("col_historico", ["FOR_RAZ"]),
        valor_modalidade=ValorModalidade(m.get("valor_modalidade", "coluna_unica")),
        col_valor=m.get("col_valor"),
        col_debito=m.get("col_debito"),
        col_credito=m.get("col_credito"),
        col_classificacao=m.get("col_classificacao"),
        skip_rows=int(m.get("skip_rows", 0)),
        sheet_name=m.get("sheet_name", "Planilha1"),
        modalidade=FinanceiroModalidade(m.get("modalidade", "pagamentos")),
        hist_prefix=m.get("hist_prefix", ""),
        hist_separator=m.get("hist_separator", " - "),
    )
