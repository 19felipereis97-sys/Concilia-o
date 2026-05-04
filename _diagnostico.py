# -*- coding: utf-8 -*-
import sys, time, os
sys.path.insert(0, r'C:\Users\felipe.r\Desktop\Conciliação')

from core.io_excel import read_raw, get_sheet_names
from core.normalize import normalize_extrato, normalize_financeiro
from core.engine import run_engine
from core.params import ConciliacaoParams
from core.mapping import ExtratoMapping, FinanceiroMapping, ValorModalidade, FinanceiroModalidade
from decimal import Decimal
import pandas as pd

EXTRATO_PATH = r'C:\Users\felipe.r\Desktop\Teste Cabine\Extrato Bancário.xlsx'
FIN_PATH     = r'C:\Users\felipe.r\Desktop\Teste Cabine\Movimento Financeiro.xlsx'

# Verifica abas disponíveis
print("Abas Extrato:", get_sheet_names(EXTRATO_PATH))
print("Abas Financeiro:", get_sheet_names(FIN_PATH))

# ── Mapeamentos ──────────────────────────────────────────────────────────────
extrato_mapping = ExtratoMapping(
    sheet_name    = 'Lançamentos',
    skip_rows     = 9,
    col_data      = 'Data',
    col_historico = ['Lançamento'],
    valor_modalidade = ValorModalidade.COLUNA_UNICA,
    col_valor     = 'Valor (R$)',
)

fin_mapping = FinanceiroMapping(
    sheet_name    = 'Plan1',
    skip_rows     = 0,
    col_data      = 'DT_PAG',
    col_historico = ['Razao'],
    valor_modalidade = ValorModalidade.COLUNA_UNICA,
    col_valor     = 'VALOR_PAG',
    col_classificacao = 'COD_CENTROCUSTO',
    modalidade    = FinanceiroModalidade.PAGAMENTOS,
)

params = ConciliacaoParams(
    value_tolerance_cents    = 0,
    max_group_size           = 30,
    max_candidates_per_group = 30,
    date_offsets             = [0, 1, -1, 2, -2],
    default_year             = 2026,
    discard_patterns         = ['SDO', 'SALDO', 'S/D', 'SALDO ANTERIOR'],
    combo_timeout_sec        = 3.0,
)

# ── Normalização ─────────────────────────────────────────────────────────────
print("\nNormalizando extrato...")
t0 = time.time()
df_bnk = normalize_extrato(EXTRATO_PATH, extrato_mapping, params)
print(f"  {len(df_bnk)} lancamentos  ({time.time()-t0:.2f}s)")
print(f"  Periodo: {df_bnk['_data'].min()} ate {df_bnk['_data'].max()}")
bnk_pos = (df_bnk['_valor_f'] > 0).sum()
bnk_neg = (df_bnk['_valor_f'] < 0).sum()
print(f"  Positivos (creditos): {bnk_pos}  |  Negativos (debitos): {bnk_neg}")

print("\nNormalizando financeiro...")
t0 = time.time()
df_fin = normalize_financeiro(FIN_PATH, fin_mapping, params)
print(f"  {len(df_fin)} lancamentos  ({time.time()-t0:.2f}s)")
print(f"  Periodo: {df_fin['_data'].min()} ate {df_fin['_data'].max()}")
fin_pos = (df_fin['_valor_f'] > 0).sum()
fin_neg = (df_fin['_valor_f'] < 0).sum()
print(f"  Positivos: {fin_pos}  |  Negativos: {fin_neg}")

# ── Engine ───────────────────────────────────────────────────────────────────
print("\nExecutando engine...")
t0 = time.time()
df_bnk_r, df_fin_r = run_engine(df_bnk.copy(), df_fin.copy(), params)
elapsed = time.time() - t0
print(f"  Concluido em {elapsed:.2f}s")

# ── Resumo de status ─────────────────────────────────────────────────────────
print("\n=== STATUS DO EXTRATO ===")
vc_bnk = df_bnk_r['_status'].value_counts()
for status, cnt in vc_bnk.items():
    print(f"  {status}: {cnt}")

print("\n=== STATUS DO FINANCEIRO ===")
vc_fin = df_fin_r['_status'].value_counts()
for status, cnt in vc_fin.items():
    print(f"  {status}: {cnt}")

# ── Taxas ────────────────────────────────────────────────────────────────────
bnk_real = df_bnk_r[~df_bnk_r['_id'].astype(str).str.startswith('PND_')]
conc_bnk = bnk_real['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL','PARCIALMENTE CONCILIADO']).sum()
total_bnk = len(bnk_real)
conc_fin  = df_fin_r['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL']).sum()
total_fin = len(df_fin_r)

print(f"\nTaxa extrato   : {conc_bnk}/{total_bnk} = {100*conc_bnk/max(total_bnk,1):.1f}%")
print(f"Taxa financeiro: {conc_fin}/{total_fin} = {100*conc_fin/max(total_fin,1):.1f}%")

# ── Análise de gargalos ──────────────────────────────────────────────────────
pendentes_bnk = bnk_real[bnk_real['_status'] == 'SEM_PAREAMENTO'].copy()
pendentes_fin = df_fin_r[df_fin_r['_status'] == 'IGNORADO_SEM_CONTRAPARTIDA'].copy()

print(f"\n=== GARGALOS: EXTRATO SEM PAR ({len(pendentes_bnk)} linhas) ===")
print("Distribuicao por data:")
date_dist = pendentes_bnk['_data'].value_counts().sort_index()
for d, c in date_dist.items():
    soma = pendentes_bnk[pendentes_bnk['_data']==d]['_valor_f'].apply(float).sum()
    print(f"  {d}: {c} lancamentos  soma={soma:,.2f}")

print(f"\n=== GARGALOS: FINANCEIRO SEM PAR ({len(pendentes_fin)} linhas) ===")
print("Distribuicao por data:")
date_dist_f = pendentes_fin['_data'].value_counts().sort_index()
for d, c in date_dist_f.items():
    soma = pendentes_fin[pendentes_fin['_data']==d]['_valor_f'].apply(float).sum()
    print(f"  {d}: {c} lancamentos  soma={soma:,.2f}")

# ── Maiores divergências ─────────────────────────────────────────────────────
print("\n=== TOP 20 EXTRATO SEM PAR (por valor absoluto) ===")
top_bnk = pendentes_bnk[['_id','_data','_historico','_valor_f']].copy()
top_bnk['_abs'] = top_bnk['_valor_f'].abs()
top_bnk = top_bnk.sort_values('_abs', ascending=False).head(20)
for _, r in top_bnk.iterrows():
    print(f"  {r['_data']}  {r['_valor_f']:>12.2f}  {str(r['_historico'])[:60]}")

print("\n=== TOP 20 FINANCEIRO SEM PAR (por valor absoluto) ===")
top_fin = pendentes_fin[['_id','_data','_historico','_valor_f','_classif']].copy()
top_fin['_abs'] = top_fin['_valor_f'].abs()
top_fin = top_fin.sort_values('_abs', ascending=False).head(20)
for _, r in top_fin.iterrows():
    print(f"  {r['_data']}  {r['_valor_f']:>12.2f}  CC={r['_classif']}  {str(r['_historico'])[:50]}")

# ── Análise de sinal ─────────────────────────────────────────────────────────
print("\n=== ANALISE DE SINAL ===")
print("Extrato (pendentes) - positivos:", (pendentes_bnk['_valor_f'] > 0).sum())
print("Extrato (pendentes) - negativos:", (pendentes_bnk['_valor_f'] < 0).sum())
print("Financeiro (pendentes) - positivos:", (pendentes_fin['_valor_f'] > 0).sum())
print("Financeiro (pendentes) - negativos:", (pendentes_fin['_valor_f'] < 0).sum())

# ── Sobreposição de valores e datas ──────────────────────────────────────────
print("\n=== VERIFICACAO CRUZADA DE VALORES ===")
bnk_vals = set(round(float(v),2) for v in pendentes_bnk['_valor_f'])
fin_vals  = set(round(float(v),2) for v in pendentes_fin['_valor_f'])
intersec  = bnk_vals & fin_vals
print(f"Valores em comum (extrato x financeiro pendentes): {len(intersec)}")
if intersec:
    sample = sorted(intersec, key=abs, reverse=True)[:10]
    print("  Amostra:", sample)
