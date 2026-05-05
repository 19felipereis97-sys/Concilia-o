from pathlib import Path
import sys
import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.engine import run_engine
from core.mapping import (
    ExtratoMapping,
    FinanceiroMapping,
    FinanceiroModalidade,
    ValorModalidade,
)
from core.normalize import normalize_extrato, normalize_financeiro
from core.params import ConciliacaoParams
from core.report_builder import build_report
from core.scope import apply_financeiro_scope


BASE_DIR = Path(r"C:\Users\felipe.r\Desktop\Teste Cabine")
EXTRATO_PATH = BASE_DIR / "Extrato Bradesco 07.xlsx"
FINANCEIRO_PATH = BASE_DIR / "Sig Bradesco 07.xlsx"
OUTPUT_PATH = BASE_DIR / "Conciliacao Bradesco 07 - pagamentos.xlsx"


def main() -> None:
    params = ConciliacaoParams(
        default_year=2025,
        max_group_size=30,
        max_candidates_per_group=40,
        n_to_one_max_candidates=30,
        combo_timeout_sec=1.5,
    )

    extrato_mapping = ExtratoMapping(
        sheet_name="Planilha1",
        skip_rows=8,
        col_data="Data",
        col_historico=["Lançamento"],
        valor_modalidade=ValorModalidade.COLUNA_UNICA,
        col_valor="Valor",
    )

    financeiro_mapping = FinanceiroMapping(
        sheet_name="Planilha1",
        skip_rows=0,
        col_data="BAIXA",
        col_historico=["FOR_RAZ"],
        hist_separator=" - ",
        valor_modalidade=ValorModalidade.COLUNA_UNICA,
        col_valor="PAGTO_PARCIAL",
        col_classificacao="NAT_DESCRIC",
        modalidade=FinanceiroModalidade.PAGAMENTOS,
    )

    print("Normalizando extrato...")
    df_bnk = normalize_extrato(EXTRATO_PATH, extrato_mapping, params)
    print("Normalizando financeiro...")
    df_fin = normalize_financeiro(FINANCEIRO_PATH, financeiro_mapping, params)
    df_bnk, scoped_count = apply_financeiro_scope(df_bnk, FinanceiroModalidade.PAGAMENTOS)
    print(f"Fora do confronto por escopo de pagamentos: {scoped_count} entradas do extrato")
    print(f"Rodando motor: {len(df_bnk)} linhas de extrato, {len(df_fin)} linhas financeiras...")
    df_bnk, df_fin = run_engine(df_bnk, df_fin, params)

    print("Gerando relatorio...")
    report_bytes = build_report(df_bnk, df_fin)
    try:
        OUTPUT_PATH.write_bytes(report_bytes)
        output_path = OUTPUT_PATH
    except PermissionError:
        output_path = OUTPUT_PATH.with_name(
            f"{OUTPUT_PATH.stem} {datetime.datetime.now():%Y%m%d-%H%M%S}{OUTPUT_PATH.suffix}"
        )
        output_path.write_bytes(report_bytes)

    print(f"Relatorio: {output_path}")
    print(f"Extrato normalizado: {len(df_bnk)} linhas")
    print(f"Financeiro normalizado: {len(df_fin)} linhas")
    print("Status extrato:")
    print(df_bnk["_status"].value_counts(dropna=False).to_string())
    print("Status financeiro:")
    print(df_fin["_status"].value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
