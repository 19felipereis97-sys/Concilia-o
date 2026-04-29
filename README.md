# Sistema Universal de Conciliação Bancária v1.0

Sistema web em Python + Streamlit para conciliação bancária 100% agnóstica de layout — o usuário mapeia as colunas em tempo real via dropdowns, sem precisar configurar o sistema para cada banco ou ERP.

## Requisitos

- Python 3.10+
- Dependências listadas em `requirements.txt`

## Instalação

```bash
cd "C:\Users\felipe.r\Desktop\Conciliação"
pip install -r requirements.txt
```

## Executar localmente

```bash
streamlit run app.py
```

Acesse em: http://localhost:8501

## Executar os testes

```bash
python run_tests.py
```

Esperado: `12/12 passaram | 0 falharam`

## Gerar planilhas de exemplo

```bash
python exemplos/gerar_exemplos.py
```

Gera 3 arquivos de exemplo com layouts distintos para demonstração:
- `exemplo_alfa.xlsx` — layout simples, coluna única de valor
- `exemplo_beta.xlsx` — 3 linhas de cabeçalho extra, colunas Débito/Crédito separadas
- `exemplo_gama_extrato.csv` + `exemplo_gama_financeiro.xlsx` — CSV + histórico multi-colunas

## Estrutura do projeto

```
app.py                    # Entrypoint Streamlit
requirements.txt
run_tests.py              # Runner standalone (sem pytest)
core/
  params.py               # ConciliacaoParams
  mapping.py              # ExtratoMapping, FinanceiroMapping
  io_excel.py             # Leitura robusta xlsx/csv
  normalize.py            # Normalização para formato interno
  engine.py               # Orquestrador do motor
  match_one_to_one.py     # Conciliação 1:1 cascata de datas
  match_n_to_one.py       # Conciliação N:1 (SISPAG)
  match_one_to_n.py       # Conciliação 1:N (desmembrado)
  collision.py            # Resolução de colisões
  manual_review.py        # Fila de revisão manual
  report_builder.py       # Geração do Excel de resultado
ui/
  components.py           # Componentes reutilizáveis
  wizard_upload.py        # Etapas de upload
  wizard_mapping.py       # Etapas de mapeamento
  wizard_review.py        # Etapa de revisão manual
plan/
  client_store.py         # SQLite: clientes, usuários, De-Para, logs
  planilha_contabil.py    # Exportação/aplicação De-Para
data/
  conciliador.db          # Banco SQLite (criado automaticamente)
tests/
  test_conciliacao.py     # 12 cenários de teste
exemplos/
  gerar_exemplos.py       # Gerador de planilhas de exemplo
```

## Modos de conciliação suportados

| Modo | Descrição |
|------|-----------|
| 1:1  | Um lançamento bancário = um financeiro, com cascata de datas D, D±1, D±2 |
| N:1  | N bancários somam = 1 financeiro (ex: SISPAG) |
| 1:N  | 1 bancário = soma de N financeiros (desmembrado) |
| Manual | Revisão pelo usuário dos casos ambíguos |

## Status dos lançamentos

| Status | Significado |
|--------|-------------|
| CONCILIADO | Pareado automaticamente |
| CONCILIADO_MANUAL | Pareado pelo usuário |
| REVISAR | Ambíguo, aguarda decisão |
| REVISAR_COLISAO | Disputado por múltiplos candidatos |
| SEM_PAREAMENTO | Bancário sem par financeiro |
| IGNORADO_SEM_CONTRAPARTIDA | Financeiro sem par bancário |
| IGNORADO_USUARIO | Descartado pelo usuário na revisão |

## Deploy no Streamlit Cloud

1. Faça push do projeto para um repositório GitHub
2. Acesse https://share.streamlit.io
3. Conecte o repositório e aponte para `app.py`
4. Configure secrets se necessário (o SQLite persiste no sistema de arquivos do Cloud)

## Arquivo de relatório gerado

O relatório Excel possui 6 abas:
1. **Relatorio Consolidado** — visão completa com De-Para contábil aplicado
2. **Extrato Bancario** — todos os lançamentos do extrato com status
3. **Financeiro** — todos os lançamentos financeiros com status
4. **Banco Sem Par** — lançamentos bancários não conciliados
5. **Financeiro Sem Par** — lançamentos financeiros não conciliados
6. **Resumo** — métricas quantitativas e totais por categoria
