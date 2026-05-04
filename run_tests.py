"""
Runner standalone — executa os 12 testes sem precisar de pytest instalado.
Uso: python run_tests.py
"""
from __future__ import annotations
import sys
import traceback

# Adiciona raiz ao path para imports relativos funcionarem
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from tests.test_conciliacao import (
    test_t01_exato_mesmo_dia,
    test_t02_offset_d_mais_1,
    test_t03_sem_par,
    test_t04_n_para_1_sispag,
    test_t05_1_para_n,
    test_t06_descarte_saldo,
    test_t07_colisao_prioridade_d,
    test_t08_multiplos_bancos_mesmo_fin,
    test_t09_parse_decimal_virgula,
    test_t10_parse_date_formatos,
    test_t11_relatorio_vazio,
    test_t12_dois_colunas_debito_credito,
    test_t18_depara_indexado_equivale_ao_fluxo_original,
    test_t19_csv_latin1_e_lido_com_fallback,
    test_t20_header_duplicado_bloqueia_importacao,
)

SUITE = [
    ("T01 - Exato mesmo dia",              test_t01_exato_mesmo_dia),
    ("T02 - Offset D+1",                   test_t02_offset_d_mais_1),
    ("T03 - Sem par",                      test_t03_sem_par),
    ("T04 - N:1 SISPAG",                   test_t04_n_para_1_sispag),
    ("T05 - 1:N desmembrado",              test_t05_1_para_n),
    ("T06 - Descarte saldo",               test_t06_descarte_saldo),
    ("T07 - Colisao prioridade D",         test_t07_colisao_prioridade_d),
    ("T08 - Multiplos bancos mesmo fin",   test_t08_multiplos_bancos_mesmo_fin),
    ("T09 - Parse decimal virgula",        test_t09_parse_decimal_virgula),
    ("T10 - Parse date formatos",          test_t10_parse_date_formatos),
    ("T11 - Relatorio vazio",              test_t11_relatorio_vazio),
    ("T12 - Duas colunas debito credito",  test_t12_dois_colunas_debito_credito),
    ("T18 - De x Para indexado equivalente", test_t18_depara_indexado_equivale_ao_fluxo_original),
    ("T19 - CSV latin1 com fallback",       test_t19_csv_latin1_e_lido_com_fallback),
    ("T20 - Header duplicado bloqueado",    test_t20_header_duplicado_bloqueia_importacao),
]


def run():
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Sistema de Conciliacao — Suite de Testes")
    print("=" * 60)
    for name, fn in SUITE:
        try:
            fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}")
            traceback.print_exc()
            failed += 1
    print("=" * 60)
    print(f"  {passed}/{len(SUITE)} passaram | {failed} falharam")
    print("=" * 60)
    return failed


if __name__ == "__main__":
    sys.exit(run())
