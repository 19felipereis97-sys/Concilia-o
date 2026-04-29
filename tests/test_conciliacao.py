"""
12 cenários de teste para o motor de conciliação.
Compatível com pytest e com o runner standalone run_tests.py.
"""
from __future__ import annotations
import io
import datetime
from decimal import Decimal
from typing import List

import pandas as pd
import openpyxl

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_xlsx(rows: List[dict], headers: List[str]) -> io.BytesIO:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _bnk(id_, data, valor, hist="BNK"):
    return {"_id": id_, "_data": datetime.date.fromisoformat(data),
            "_valor": Decimal(str(valor)), "_historico": hist,
            "_status": "SEM_PAREAMENTO", "_metodo": "", "_ids_fin": ""}


def _fin(id_, data, valor, hist="FIN", classif=""):
    return {"_id": id_, "_data": datetime.date.fromisoformat(data),
            "_valor": Decimal(str(valor)), "_historico": hist, "_classif": classif,
            "_status": "IGNORADO_SEM_CONTRAPARTIDA", "_metodo": "", "_id_bnk": ""}


def _run(bnk_rows, fin_rows):
    from core.engine import run_engine
    from core.params import ConciliacaoParams
    df_b = pd.DataFrame(bnk_rows)
    df_f = pd.DataFrame(fin_rows)
    params = ConciliacaoParams()
    return run_engine(df_b, df_f, params)


# ── testes ────────────────────────────────────────────────────────────────────

def test_t01_exato_mesmo_dia():
    """1:1 exato na mesma data."""
    b, f = _run(
        [_bnk("B1", "2024-01-10", "-100.00")],
        [_fin("F1", "2024-01-10", "-100.00")],
    )
    assert b.iloc[0]["_status"] == "CONCILIADO"
    assert f.iloc[0]["_status"] == "CONCILIADO"


def test_t02_offset_d_mais_1():
    """1:1 com financeiro D+1 (banco 10/01, fin 11/01)."""
    b, f = _run(
        [_bnk("B1", "2024-01-10", "-200.00")],
        [_fin("FX", "2024-01-11", "-200.00"),   # D+1 — deve ganhar
         _fin("FY", "2024-01-12", "-200.00")],  # D+2
    )
    assert b.iloc[0]["_status"] == "CONCILIADO"
    assert b.iloc[0]["_ids_fin"] == "FX"
    assert f[f["_id"] == "FX"].iloc[0]["_status"] == "CONCILIADO"


def test_t03_sem_par():
    """Linha bancária sem nenhum candidato."""
    b, f = _run(
        [_bnk("B1", "2024-01-10", "-300.00")],
        [_fin("F1", "2024-01-10", "-999.00")],
    )
    assert b.iloc[0]["_status"] == "SEM_PAREAMENTO"
    assert f.iloc[0]["_status"] == "IGNORADO_SEM_CONTRAPARTIDA"


def test_t04_n_para_1_sispag():
    """N:1 — dois bancários somam o financeiro."""
    b, f = _run(
        [_bnk("B1", "2024-01-15", "-50.00"),
         _bnk("B2", "2024-01-15", "-50.00")],
        [_fin("F1", "2024-01-15", "-100.00")],
    )
    assert b[b["_id"] == "B1"].iloc[0]["_status"] == "CONCILIADO"
    assert b[b["_id"] == "B2"].iloc[0]["_status"] == "CONCILIADO"
    assert f.iloc[0]["_status"] == "CONCILIADO"


def test_t05_1_para_n():
    """1:N — um bancário = soma de dois financeiros."""
    b, f = _run(
        [_bnk("B1", "2024-01-20", "-150.00")],
        [_fin("F1", "2024-01-20", "-100.00"),
         _fin("F2", "2024-01-20", "-50.00")],
    )
    assert b.iloc[0]["_status"] == "CONCILIADO"
    assert f[f["_id"] == "F1"].iloc[0]["_status"] == "CONCILIADO"
    assert f[f["_id"] == "F2"].iloc[0]["_status"] == "CONCILIADO"


def test_t06_descarte_saldo():
    """Linhas com prefixo SDO/SALDO devem ser descartadas na normalização."""
    from core.normalize import normalize_extrato
    from core.mapping import ExtratoMapping, ValorModalidade
    from core.params import ConciliacaoParams

    buf = _make_xlsx(
        [{"Data": "10/01/2024", "Hist": "SALDO ANTERIOR", "Valor": "1000"},
         {"Data": "10/01/2024", "Hist": "PIX RECEBIDO",   "Valor": "-200"}],
        ["Data", "Hist", "Valor"],
    )
    mapping = ExtratoMapping(
        col_data="Data", col_historico=["Hist"],
        valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor="Valor",
    )
    df = normalize_extrato(buf, mapping, ConciliacaoParams(), suffix=".xlsx")
    assert len(df) == 1
    assert "PIX" in df.iloc[0]["_historico"]


def test_t07_colisao_prioridade_d():
    """Colisão: dois financeiros com mesmo valor; banco deve pegar o de D (offset 0)."""
    b, f = _run(
        [_bnk("B1", "2024-01-10", "-77.00")],
        [_fin("F_hoje",   "2024-01-10", "-77.00"),
         _fin("F_amanha", "2024-01-11", "-77.00")],
    )
    assert b.iloc[0]["_ids_fin"] == "F_hoje"


def test_t08_multiplos_bancos_mesmo_fin():
    """Dois bancos disputam o mesmo financeiro: um conciliado, outro revisar."""
    b, f = _run(
        [_bnk("B1", "2024-01-10", "-88.00"),
         _bnk("B2", "2024-01-10", "-88.00")],
        [_fin("F1", "2024-01-10", "-88.00")],
    )
    statuses = set(b["_status"].tolist())
    assert "CONCILIADO" in statuses
    assert "REVISAR_COLISAO" in statuses or "SEM_PAREAMENTO" in statuses


def test_t09_parse_decimal_virgula():
    """_parse_decimal deve tratar vírgula como separador decimal."""
    from core.normalize import _parse_decimal
    assert _parse_decimal("1.234,56") == Decimal("1234.56")
    assert _parse_decimal("R$ 99,90") == Decimal("99.90")
    assert _parse_decimal("") is None


def test_t10_parse_date_formatos():
    """_parse_date deve aceitar múltiplos formatos."""
    from core.normalize import _parse_date
    assert _parse_date("10/01/2024") == datetime.date(2024, 1, 10)
    assert _parse_date("2024-01-10") == datetime.date(2024, 1, 10)
    assert _parse_date("10.01.2024") == datetime.date(2024, 1, 10)
    assert _parse_date("") is None


def test_t11_relatorio_vazio():
    """build_report não deve quebrar com DataFrames vazios."""
    from core.report_builder import build_report
    df_b = pd.DataFrame()
    df_f = pd.DataFrame()
    resultado = build_report(df_b, df_f)
    assert isinstance(resultado, bytes)
    assert len(resultado) > 0


def test_t13_pagamentos_forcado_negativo():
    """normalize_financeiro com modalidade PAGAMENTOS deve forçar valores negativos."""
    from core.normalize import normalize_financeiro
    from core.mapping import FinanceiroMapping, FinanceiroModalidade, ValorModalidade
    from core.params import ConciliacaoParams

    buf = _make_xlsx(
        [{"Data": "10/01/2024", "Hist": "PGTO FORNEC", "Valor": "500"},
         {"Data": "10/01/2024", "Hist": "PGTO SALARIO", "Valor": "-300"}],
        ["Data", "Hist", "Valor"],
    )
    mapping = FinanceiroMapping(
        col_data="Data", col_historico=["Hist"],
        valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor="Valor",
        modalidade=FinanceiroModalidade.PAGAMENTOS,
    )
    df = normalize_financeiro(buf, mapping, ConciliacaoParams(), suffix=".xlsx")
    assert len(df) == 2
    assert all(df["_valor"] < 0), "Todos os valores PAGAMENTOS devem ser negativos"


def test_t14_recebimentos_forcado_positivo():
    """normalize_financeiro com modalidade RECEBIMENTOS deve forçar valores positivos."""
    from core.normalize import normalize_financeiro
    from core.mapping import FinanceiroMapping, FinanceiroModalidade, ValorModalidade
    from core.params import ConciliacaoParams

    buf = _make_xlsx(
        [{"Data": "10/01/2024", "Hist": "REC CLIENTE", "Valor": "-400"},
         {"Data": "10/01/2024", "Hist": "REC JUROS",   "Valor": "200"}],
        ["Data", "Hist", "Valor"],
    )
    mapping = FinanceiroMapping(
        col_data="Data", col_historico=["Hist"],
        valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor="Valor",
        modalidade=FinanceiroModalidade.RECEBIMENTOS,
    )
    df = normalize_financeiro(buf, mapping, ConciliacaoParams(), suffix=".xlsx")
    assert len(df) == 2
    assert all(df["_valor"] > 0), "Todos os valores RECEBIMENTOS devem ser positivos"


def test_t15_review_queue_sem_candidatos_vira_sem_pareamento():
    """Linha REVISAR sem candidatos financeiros livres deve sair da fila e virar SEM_PAREAMENTO."""
    from core.manual_review import build_review_queue
    from core.params import ConciliacaoParams

    df_b = pd.DataFrame([{
        "_id": "B1", "_data": datetime.date(2024, 1, 10),
        "_valor": Decimal("-100.00"), "_historico": "PGTO",
        "_status": "REVISAR", "_metodo": "", "_ids_fin": "",
    }])
    # Sem nenhum financeiro livre
    df_f = pd.DataFrame(columns=["_id", "_data", "_valor", "_historico", "_classif", "_status", "_metodo", "_id_bnk"])

    cards = build_review_queue(df_b, df_f, ConciliacaoParams())

    assert cards == [], "Fila deve estar vazia"
    assert str(df_b.iloc[0]["_status"]) == "SEM_PAREAMENTO", "Status deve mudar para SEM_PAREAMENTO"


def test_t16_filter_valid_combos_sem_combo_retorna_vazio():
    """_filter_to_valid_combos deve retornar [] quando nenhuma combinação válida existe."""
    from core.manual_review import _filter_to_valid_combos

    candidatos = [
        {"id": "F1", "valor": Decimal("-30.00"), "data": None, "historico": "", "classif": "", "delta_dias": 0, "probabilidade": "media"},
        {"id": "F2", "valor": Decimal("-40.00"), "data": None, "historico": "", "classif": "", "delta_dias": 0, "probabilidade": "media"},
    ]
    # target = -150, max_group=2: -30 + -40 = -70 ≠ -150, nenhuma combinação possível
    result = _filter_to_valid_combos(candidatos, -150.0, 0.0, max_group_size=2)
    assert result == [], "Sem combinação válida deve retornar lista vazia"


def test_t17_pagamentos_casam_com_debitos_banco():
    """Financeiro PAGAMENTOS (forçado negativo) deve conciliar com débitos bancários."""
    from core.normalize import normalize_financeiro
    from core.mapping import FinanceiroMapping, FinanceiroModalidade, ValorModalidade
    from core.params import ConciliacaoParams
    from core.engine import run_engine

    buf_fin = _make_xlsx(
        [{"Data": "20/01/2024", "Hist": "PGTO", "Valor": "250"}],
        ["Data", "Hist", "Valor"],
    )
    mapping = FinanceiroMapping(
        col_data="Data", col_historico=["Hist"],
        valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor="Valor",
        modalidade=FinanceiroModalidade.PAGAMENTOS,
    )
    params = ConciliacaoParams()
    df_f = normalize_financeiro(buf_fin, mapping, params, suffix=".xlsx")

    df_b = pd.DataFrame([_bnk("B1", "2024-01-20", "-250.00")])
    df_b["_valor_f"] = df_b["_valor"].apply(float)

    df_b, df_f = run_engine(df_b, df_f, params)
    assert str(df_b.iloc[0]["_status"]) == "CONCILIADO", "PAGAMENTOS negativo deve conciliar com débito bancário"


def test_t12_dois_colunas_debito_credito():
    """Extrato com colunas separadas de débito e crédito."""
    from core.normalize import normalize_extrato
    from core.mapping import ExtratoMapping, ValorModalidade
    from core.params import ConciliacaoParams

    buf = _make_xlsx(
        [{"Data": "15/01/2024", "Hist": "PAGTO",     "Deb": "500", "Cre": ""},
         {"Data": "15/01/2024", "Hist": "RECEBTO",   "Deb": "",    "Cre": "300"},
         {"Data": "15/01/2024", "Hist": "SDO FINAL", "Deb": "",    "Cre": ""}],
        ["Data", "Hist", "Deb", "Cre"],
    )
    mapping = ExtratoMapping(
        col_data="Data", col_historico=["Hist"],
        valor_modalidade=ValorModalidade.DOIS_COLUNAS,
        col_debito="Deb", col_credito="Cre",
    )
    df = normalize_extrato(buf, mapping, ConciliacaoParams(), suffix=".xlsx")
    # SDO FINAL zerado é descartado, PAGTO e RECEBTO mantidos
    assert len(df) == 2
    valores = sorted([float(r["_valor"]) for _, r in df.iterrows()])
    assert valores == [-300.0, 500.0]
