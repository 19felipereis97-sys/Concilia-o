# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r'C:\Users\felipe.r\Desktop\Conciliação')

from core.normalize import normalize_extrato, normalize_financeiro
from core.engine import run_engine
from core.params import ConciliacaoParams
from core.mapping import ExtratoMapping, FinanceiroMapping, ValorModalidade, FinanceiroModalidade
import pandas as pd

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
params = ConciliacaoParams(
    value_tolerance_cents=0, max_group_size=30,
    max_candidates_per_group=30, date_offsets=[0, 1, -1, 2, -2],
    default_year=2026, discard_patterns=['SDO','SALDO','S/D','SALDO ANTERIOR'],
    combo_timeout_sec=3.0,
)

df_bnk = normalize_extrato(EXTRATO_PATH, extrato_mapping, params)
df_fin = normalize_financeiro(FIN_PATH, fin_mapping, params)
df_bnk_r, df_fin_r = run_engine(df_bnk.copy(), df_fin.copy(), params)

# ── Foco: débitos do extrato ─────────────────────────────────────────────────
bnk_real = df_bnk_r[~df_bnk_r['_id'].astype(str).str.startswith('PND_')].copy()
debitos   = bnk_real[bnk_real['_valor_f'] < 0].copy()
creditos  = bnk_real[bnk_real['_valor_f'] > 0].copy()

print("=== VISAO GERAL DOS DEBITOS DO EXTRATO ===")
print(f"Total debitos: {len(debitos)}")
for s, g in debitos.groupby('_status'):
    soma = g['_valor_f'].sum()
    print(f"  {s}: {len(g)} lancamentos  soma={soma:,.2f}")

print("\n=== DEBITOS NAO CONCILIADOS (SEM PAR) ===")
deb_pendentes = debitos[debitos['_status']=='SEM_PAREAMENTO']
for _, r in deb_pendentes.sort_values('_valor_f').iterrows():
    print(f"  {r['_data']}  {r['_valor_f']:>12.2f}  {str(r['_historico'])[:70]}")

print("\n=== DEBITOS MARCADOS REVISAR ===")
deb_revisar = debitos[debitos['_status'].isin(['REVISAR','REVISAR_COLISAO'])]
for _, r in deb_revisar.sort_values('_valor_f').iterrows():
    print(f"  {r['_data']}  {r['_valor_f']:>12.2f}  {str(r['_historico'])[:70]}  -> metodo: {r['_metodo']}")

print("\n=== CREDITOS DO EXTRATO - DISTRIBUICAO POR HISTORICO ===")
cred_hist = creditos['_historico'].str.split(' ').str[0].value_counts().head(20)
for k, v in cred_hist.items():
    print(f"  {k}: {v}")

print("\n=== FINANCEIRO NAO CONCILIADO - TOP FORNECEDORES ===")
fin_pendente = df_fin_r[df_fin_r['_status']=='IGNORADO_SEM_CONTRAPARTIDA'].copy()
fin_pendente['_razao'] = fin_pendente['_historico'].str.strip()
top_forn = fin_pendente.groupby('_razao').agg(
    count=('_valor_f','count'),
    soma=('_valor_f','sum')
).sort_values('soma').head(20)
for razao, row in top_forn.iterrows():
    print(f"  {row['count']:3d} lans  soma={row['soma']:>14,.2f}  {razao[:50]}")

print("\n=== FINANCEIRO NAO CONCILIADO - DISTRIBUICAO POR BANCO ===")
if 'BANCO' in df_fin_r.columns:
    banco_dist = fin_pendente['BANCO'].value_counts().head(10)
    for banco, cnt in banco_dist.items():
        print(f"  Banco {banco}: {cnt} lancamentos")

print("\n=== SOMA TOTAL POR SEGMENTO ===")
soma_cred_extrato = creditos['_valor_f'].sum()
soma_deb_extrato  = debitos['_valor_f'].sum()
soma_fin_pendente = fin_pendente['_valor_f'].sum()
print(f"Extrato creditos total    : R$ {soma_cred_extrato:>15,.2f}  ({len(creditos)} lancamentos)")
print(f"Extrato debitos total     : R$ {soma_deb_extrato:>15,.2f}  ({len(debitos)} lancamentos)")
print(f"Financeiro pendente total : R$ {soma_fin_pendente:>15,.2f}  ({len(fin_pendente)} lancamentos)")

print("\n=== COMPARACAO SOMA DEBITOS EXTRATO vs FINANCEIRO ===")
deb_conc = debitos[debitos['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL'])]['_valor_f'].sum()
deb_rev  = debitos[debitos['_status'].isin(['REVISAR','REVISAR_COLISAO'])]['_valor_f'].sum()
deb_parc = debitos[debitos['_status']=='PARCIALMENTE CONCILIADO']['_valor_f'].sum()
deb_pend = debitos[debitos['_status']=='SEM_PAREAMENTO']['_valor_f'].sum()
fin_conc = df_fin_r[df_fin_r['_status'].isin(['CONCILIADO','CONCILIADO_MANUAL'])]['_valor_f'].sum()
print(f"Debitos extrato conciliados: R$ {deb_conc:>14,.2f}")
print(f"Debitos extrato revisar    : R$ {deb_rev:>14,.2f}")
print(f"Debitos extrato parcial    : R$ {deb_parc:>14,.2f}")
print(f"Debitos extrato pendentes  : R$ {deb_pend:>14,.2f}")
print(f"Financeiro conciliado      : R$ {fin_conc:>14,.2f}")
