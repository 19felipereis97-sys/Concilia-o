# -*- coding: utf-8 -*-
"""
Experimento: amplia max_candidates_per_group e max_group_size para resolver
os grupos grandes de SISPAG e mede o maior grupo real formado.
"""
import sys, time
sys.path.insert(0, r'C:\Users\felipe.r\Desktop\Conciliação')

from core.normalize import normalize_extrato, normalize_financeiro
from core.engine import run_engine
from core.params import ConciliacaoParams
from core.mapping import ExtratoMapping, FinanceiroMapping, ValorModalidade, FinanceiroModalidade

EXTRATO_PATH = r'C:\Users\felipe.r\Desktop\Teste Cabine\Extrato Bancário.xlsx'
FIN_PATH     = r'C:\Users\felipe.r\Desktop\Teste Cabine\Movimento Financeiro.xlsx'

extrato_mapping = ExtratoMapping(
    sheet_name='Lançamentos', skip_rows=9,
    col_data='Data', col_historico=['Lançamento'],
    valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor='Valor (R$)',
)
fin_mapping = FinanceiroMapping(
    sheet_name='Plan1', skip_rows=0,
    col_data='DT_PAG', col_historico=['Razao'],
    valor_modalidade=ValorModalidade.COLUNA_UNICA, col_valor='VALOR_PAG',
    col_classificacao='COD_CENTROCUSTO',
    modalidade=FinanceiroModalidade.PAGAMENTOS,
)

df_bnk_base = normalize_extrato(EXTRATO_PATH, extrato_mapping,
    ConciliacaoParams(default_year=2026, discard_patterns=['SDO','SALDO','S/D','SALDO ANTERIOR']))
df_fin_base = normalize_financeiro(FIN_PATH, fin_mapping,
    ConciliacaoParams(default_year=2026, discard_patterns=['SDO','SALDO','S/D','SALDO ANTERIOR']))

# ── Configurações a testar ───────────────────────────────────────────────────
CONFIGS = [
    # (label, max_candidates_per_group, max_group_size, timeout)
    ("ATUAL         (cap=30 ,k<=30,t=3s)",   30,  30,  3.0),
    ("AMPLIADO-A    (cap=100,k<=50,t=5s)",  100,  50,  5.0),
    ("AMPLIADO-B    (cap=300,k<=60,t=10s)", 300,  60, 10.0),
    ("SEM CAP       (cap=0  ,k<=60,t=15s)",   0,  60, 15.0),
]

print("Extrato:", len(df_bnk_base), "lancamentos")
print("Financeiro:", len(df_fin_base), "lancamentos")
print()

results = []
for label, cap, max_k, timeout in CONFIGS:
    params = ConciliacaoParams(
        value_tolerance_cents=0,
        max_group_size=max_k,
        max_candidates_per_group=cap,
        date_offsets=[0, 1, -1, 2, -2],
        default_year=2026,
        discard_patterns=['SDO','SALDO','S/D','SALDO ANTERIOR'],
        combo_timeout_sec=timeout,
    )
    t0 = time.time()
    df_b, df_f = run_engine(df_bnk_base.copy(), df_fin_base.copy(), params)
    elapsed = time.time() - t0

    bnk_real = df_b[~df_b['_id'].astype(str).str.startswith('PND_')]
    conc_bnk = bnk_real['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL','PARCIALMENTE CONCILIADO']).sum()
    total_bnk = len(bnk_real)
    conc_fin = df_f['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL']).sum()

    # Maior grupo formado (extraído do campo _metodo: "1:N D soma=N")
    metodos = df_b[df_b['_metodo'].str.contains('soma=', na=False)]['_metodo']
    if len(metodos) > 0:
        ks = metodos.str.extract(r'soma=(\d+)')[0].dropna().astype(int)
        maior_k = int(ks.max()) if len(ks) > 0 else 0
        media_k  = float(ks.mean()) if len(ks) > 0 else 0
    else:
        maior_k = 0
        media_k = 0

    revisar = (bnk_real['_status'] == 'REVISAR').sum()

    results.append({
        'label': label, 'elapsed': elapsed,
        'conc_bnk': conc_bnk, 'total_bnk': total_bnk,
        'conc_fin': conc_fin, 'revisar': revisar,
        'maior_k': maior_k, 'media_k': media_k,
    })

    print(f"[{label}]")
    print(f"  Tempo         : {elapsed:.1f}s")
    print(f"  Extrato       : {conc_bnk}/{total_bnk} = {100*conc_bnk/total_bnk:.1f}%  |  REVISAR: {revisar}")
    print(f"  Financeiro    : {conc_fin}/{len(df_fin_base)} = {100*conc_fin/len(df_fin_base):.1f}%")
    print(f"  Maior grupo 1:N formado: {maior_k} lancamentos  (media: {media_k:.1f})")

    # Detalha os grupos conciliados (1:N com soma=)
    conc_1n = df_b[df_b['_metodo'].str.contains('soma=', na=False) & df_b['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL'])]
    if len(conc_1n) > 0:
        print(f"  Grupos 1:N conciliados ({len(conc_1n)}):")
        for _, r in conc_1n.sort_values('_metodo').iterrows():
            k_val = int(r['_metodo'].split('soma=')[1]) if 'soma=' in str(r['_metodo']) else 0
            print(f"    {r['_data']}  {r['_valor_f']:>14.2f}  k={k_val:3d}  {r['_historico'][:40]}")
    print()

# ── Comparativo final ────────────────────────────────────────────────────────
print("=" * 70)
print(f"{'Config':<35} {'Tempo':>6} {'BNK%':>6} {'FIN%':>6} {'REV':>5} {'max k':>6}")
print("-" * 70)
for r in results:
    pct_b = 100*r['conc_bnk']/r['total_bnk']
    pct_f = 100*r['conc_fin']/len(df_fin_base)
    print(f"{r['label']:<35} {r['elapsed']:>5.1f}s {pct_b:>5.1f}% {pct_f:>5.1f}% {r['revisar']:>5}  {r['maior_k']:>5}")
