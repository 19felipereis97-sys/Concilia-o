from __future__ import annotations

from core.mapping import (
    ExtratoMapping,
    FinanceiroMapping,
    FinanceiroModalidade,
    ValorModalidade,
)
from core.wizard_persistence import (
    apply_extrato_template_data,
    apply_fin_template_data,
    get_extrato_snapshot,
    get_fin_snapshot,
)


def test_extrato_template_preserva_configuracao_do_arquivo_atual():
    state = {
        "extrato_sheet": "Aba atual",
        "extrato_skip": 2,
        "extrato_suffix": ".xlsx",
        "default_year": 2026,
        "tmp_default_year": 2026,
    }
    legacy_template = {
        "extrato_sheet": "Sheet0",
        "extrato_skip": 8,
        "extrato_suffix": ".xls",
        "default_year": 2024,
        "tmp_default_year": 2024,
        "param_tol": 99,
        "bnk_col_data": "Data",
        "bnk_col_hist": ["Historico"],
        "bnk_col_valor": "Valor",
        "extrato_mapping": {
            "col_data": "Data antiga",
            "col_historico": ["Historico antigo"],
            "valor_modalidade": "coluna_unica",
            "col_valor": "Valor antigo",
            "skip_rows": 8,
            "sheet_name": "Sheet0",
        },
    }

    apply_extrato_template_data(state, legacy_template)

    assert state["extrato_sheet"] == "Aba atual"
    assert state["extrato_skip"] == 2
    assert state["extrato_suffix"] == ".xlsx"
    assert state["default_year"] == 2026
    assert state["tmp_default_year"] == 2026
    assert state["bnk_col_data"] == "Data"
    assert state["bnk_col_hist"] == ["Historico"]
    assert state["bnk_col_valor"] == "Valor"


def test_fin_template_preserva_etapas_anteriores():
    state = {
        "fin_sheet": "Lancamentos atuais",
        "fin_skip": 1,
        "fin_suffix": ".xlsx",
        "fin2_sheet": "Pagamentos atuais",
        "fin2_skip": 3,
        "fin2_suffix": ".xlsx",
        "fin_modalidade_str": "COMPLETO",
        "default_year": 2026,
        "tmp_fin_sheet": "Lancamentos atuais",
        "tmp_fin_skip": 1,
        "tmp_fin2_sheet": "Pagamentos atuais",
        "tmp_fin2_skip": 3,
        "tmp_default_year": 2026,
    }
    legacy_template = {
        "fin_sheet": "Plan1",
        "fin_skip": 6,
        "fin_suffix": ".xls",
        "fin2_sheet": "Plan2",
        "fin2_skip": 9,
        "fin2_suffix": ".xls",
        "fin_modalidade_str": "SEPARADOS",
        "default_year": 2024,
        "tmp_fin_sheet": "Plan1",
        "tmp_fin_skip": 6,
        "tmp_fin2_sheet": "Plan2",
        "tmp_fin2_skip": 9,
        "tmp_default_year": 2024,
        "rec_col_data": "Data recebimento",
        "fin_mapping": {
            "col_data": "Data antiga",
            "col_historico": ["Historico"],
            "valor_modalidade": "coluna_unica",
            "col_valor": "Valor",
            "skip_rows": 6,
            "sheet_name": "Plan1",
            "modalidade": "recebimentos",
        },
    }

    apply_fin_template_data(state, legacy_template)

    assert state["fin_sheet"] == "Lancamentos atuais"
    assert state["fin_skip"] == 1
    assert state["fin_suffix"] == ".xlsx"
    assert state["fin2_sheet"] == "Pagamentos atuais"
    assert state["fin2_skip"] == 3
    assert state["fin2_suffix"] == ".xlsx"
    assert state["fin_modalidade_str"] == "COMPLETO"
    assert state["default_year"] == 2026
    assert state["tmp_fin_sheet"] == "Lancamentos atuais"
    assert state["tmp_fin_skip"] == 1
    assert state["tmp_fin2_sheet"] == "Pagamentos atuais"
    assert state["tmp_fin2_skip"] == 3
    assert state["tmp_default_year"] == 2026
    assert state["rec_col_data"] == "Data recebimento"
    assert state["rec_col_hist"] == ["Historico"]
    assert state["rec_col_valor"] == "Valor"


def test_novos_snapshots_contem_somente_mapeamento_de_colunas():
    extrato_state = {
        "extrato_sheet": "Sheet0",
        "extrato_skip": 8,
        "default_year": 2026,
        "param_tol": 1,
        "bnk_col_data": "Data",
        "extrato_mapping": ExtratoMapping(
            col_data="Data",
            col_historico=["Historico"],
            valor_modalidade=ValorModalidade.COLUNA_UNICA,
            col_valor="Valor",
            skip_rows=8,
            sheet_name="Sheet0",
        ),
    }
    fin_state = {
        "fin_sheet": "Plan1",
        "fin_skip": 4,
        "fin2_sheet": "Plan2",
        "fin2_skip": 7,
        "fin_modalidade_str": "COMPLETO",
        "default_year": 2026,
        "fin_mapping": FinanceiroMapping(
            col_data="Vencimento",
            col_historico=["Fornecedor"],
            valor_modalidade=ValorModalidade.COLUNA_UNICA,
            col_valor="Total",
            skip_rows=4,
            sheet_name="Plan1",
            modalidade=FinanceiroModalidade.COMPLETO,
        ),
    }

    extrato_snapshot = get_extrato_snapshot(extrato_state)
    fin_snapshot = get_fin_snapshot(fin_state)

    assert extrato_snapshot["bnk_col_data"] == "Data"
    assert extrato_snapshot["bnk_col_hist"] == ["Historico"]
    assert extrato_snapshot["bnk_col_valor"] == "Valor"
    assert not {
        "extrato_sheet", "extrato_skip", "default_year", "param_tol",
        "extrato_mapping",
    } & extrato_snapshot.keys()

    assert fin_snapshot["fin_col_data"] == "Vencimento"
    assert fin_snapshot["fin_col_hist"] == ["Fornecedor"]
    assert fin_snapshot["fin_col_valor"] == "Total"
    assert not {
        "fin_sheet", "fin_skip", "fin2_sheet", "fin2_skip",
        "fin_modalidade_str", "default_year", "fin_mapping",
    } & fin_snapshot.keys()
