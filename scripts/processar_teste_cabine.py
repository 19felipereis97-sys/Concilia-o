from pathlib import Path
import datetime
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from _config import EXTRATO_PATH, FINANCEIRO_PATH, make_params, make_extrato_mapping, make_financeiro_mapping
from core.engine import run_engine
from core.normalize import normalize_extrato, normalize_financeiro
from core.report_builder import build_report
from core.scope import apply_financeiro_scope

OUTPUT_PATH = Path(r"C:\Users\felipe.r\Desktop\Teste Cabine") / "Conciliacao Teste Cabine - pagamentos.xlsx"


def main() -> None:
    params = make_params()
    fin_mapping = make_financeiro_mapping()

    print("Normalizando extrato...")
    df_bnk = normalize_extrato(EXTRATO_PATH, make_extrato_mapping(), params)
    print("Normalizando financeiro...")
    df_fin = normalize_financeiro(FINANCEIRO_PATH, fin_mapping, params)

    df_bnk, scoped_count = apply_financeiro_scope(df_bnk, fin_mapping.modalidade)
    print(f"Fora do confronto por escopo de pagamentos: {scoped_count} entradas do extrato")
    print(f"Rodando motor: {len(df_bnk)} linhas de extrato, {len(df_fin)} linhas financeiras...")
    df_bnk, df_fin = run_engine(df_bnk, df_fin, params)

    print("Gerando relatorio...")
    report_bytes = build_report(df_bnk, df_fin)
    try:
        output_path = OUTPUT_PATH
        output_path.write_bytes(report_bytes)
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
