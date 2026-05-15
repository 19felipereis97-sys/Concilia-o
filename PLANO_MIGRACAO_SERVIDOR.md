# Plano de Migração — Banco de Dados para Servidor Local JCA Contadores

> **Autor:** Equipe de Desenvolvimento  
> **Data:** 12/05/2026  
> **Status:** Planejamento  
> **Versão:** 1.0

---

## 1. Diagnóstico do Estado Atual

### 1.1 Banco de dados atual

| Item | Valor |
|------|-------|
| **Tipo** | SQLite 3 (arquivo local) |
| **Arquivo** | `data/conciliador.db` |
| **Tamanho** | ~213 KB |
| **Localização** | Dentro do repositório Git, na máquina do desenvolvedor |

### 1.2 Dados existentes

| Tabela | Registros | Descrição |
|--------|-----------|-----------|
| `clientes` | 631 | Empresas/clientes cadastrados |
| `usuarios` | 56 | Usuários do sistema (login/senha com bcrypt) |
| `depara` | 215 | Regras de De x Para contábil |
| `logs` | 130 | Log de ações/auditoria |
| `depara_historico` | 7 | Histórico de alterações no De x Para |
| `conciliacao_templates` | 0 | Templates de configuração de conciliação |

### 1.3 Como o app se conecta ao banco hoje

O arquivo `plan/client_store.py` já possui um mecanismo de configuração flexível:

```python
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "conciliador.db"

def _configured_db_path() -> Path:
    # 1. Variável de ambiente CONCILIADOR_DB_PATH
    env_path = os.environ.get("CONCILIADOR_DB_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    # 2. Streamlit secrets
    secret_path = st.secrets.get("CONCILIADOR_DB_PATH", "")
    if secret_path:
        return Path(secret_path).expanduser()
    # 3. Fallback: data/conciliador.db local
    return _DEFAULT_DB_PATH
```

### 1.4 Cenário alvo

| Item | Valor |
|------|-------|
| **Servidor** | Servidor local JCA Contadores (hostname/IP a definir) |
| **Execução do app** | Streamlit centralizado no servidor |
| **Usuários simultâneos** | ~50 |
| **Acesso** | Rede interna da JCA |

---

## 2. Decisão Arquitetural

### ⚠️ Por que NÃO usar SQLite em rede para este cenário

Com **~50 usuários simultâneos**, o SQLite **não é recomendado**:

- SQLite usa locks de arquivo — apenas **uma escrita por vez** em todo o banco
- Em pasta de rede (SMB/CIFS), o risco de **corrupção de dados** aumenta significativamente
- Performance degrada rapidamente com concorrência acima de 3-5 usuários
- A própria documentação do SQLite [desaconselha uso em rede compartilhada](https://www.sqlite.org/whentouse.html)

### ✅ Recomendação: Migrar para PostgreSQL

| Aspecto | SQLite (atual) | PostgreSQL (proposto) |
|---------|---------------|----------------------|
| **Concorrência** | ❌ 1 escrita por vez | ✅ Centenas simultâneas |
| **Rede** | ❌ Risco de corrupção | ✅ Protocolo nativo TCP/IP |
| **Backup** | Manual (copiar arquivo) | ✅ `pg_dump` automático |
| **Custo** | Gratuito | ✅ Gratuito (open source) |
| **Segurança** | Arquivo acessível a qualquer um | ✅ Autenticação por usuário/senha |
| **Monitoramento** | Nenhum | ✅ `pg_stat_activity`, logs nativos |
| **Escalabilidade** | ❌ Limitada | ✅ Pronta para crescer |

---

## 3. Plano de Ação — Migração para PostgreSQL no Servidor

### Visão geral das fases

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FLUXO DE MIGRAÇÃO                             │
│                                                                      │
│  FASE 1          FASE 2          FASE 3          FASE 4              │
│  Infra do     →  Adaptar o    →  Migrar os    →  Deploy no          │
│  Servidor        Código          Dados            Servidor           │
│                                                                      │
│  FASE 5          FASE 6          FASE 7                              │
│  Backup       →  Validação    →  Go-Live                            │
│  Automático      Completa        + Rollback                          │
└──────────────────────────────────────────────────────────────────────┘
```

---

### FASE 1 — Preparar o Servidor (Infraestrutura)

**Responsável:** TI / Infraestrutura  
**Tempo estimado:** 1–2 horas

#### 1.1 Instalar PostgreSQL no servidor

```powershell
# Baixar PostgreSQL 16+ para Windows:
# https://www.postgresql.org/download/windows/
# Instalar com o instalador interativo (Stack Builder incluso)
```

**Configuração recomendada durante instalação:**
| Parâmetro | Valor |
|-----------|-------|
| Porta | `5432` (padrão) |
| Superusuário | `postgres` |
| Senha | (definir senha forte e anotar) |
| Locale | `Portuguese_Brazil.1252` |
| Data Directory | `D:\PostgreSQL\data` (ou disco com mais espaço) |

#### 1.2 Criar o banco de dados e o usuário da aplicação

Executar no **pgAdmin** ou via `psql`:

```sql
-- Criar usuário exclusivo para a aplicação
CREATE USER conciliador_app WITH PASSWORD 'SENHA_SEGURA_AQUI';

-- Criar o banco de dados
CREATE DATABASE conciliador
    OWNER conciliador_app
    ENCODING 'UTF8'
    LC_COLLATE 'pt_BR.UTF-8'
    LC_CTYPE 'pt_BR.UTF-8'
    TEMPLATE template0;

-- Permissões
GRANT ALL PRIVILEGES ON DATABASE conciliador TO conciliador_app;
```

#### 1.3 Liberar acesso na rede interna

Editar `pg_hba.conf` para permitir conexões da rede local:

```
# Permitir conexões da rede interna da JCA
host    conciliador    conciliador_app    192.168.0.0/16    scram-sha-256
host    conciliador    conciliador_app    10.0.0.0/8        scram-sha-256
```

Editar `postgresql.conf`:

```
listen_addresses = '*'     # Escutar em todas as interfaces
max_connections = 100      # Suportar até 100 conexões
```

> **Nota:** Ajustar o range de IPs (`192.168.x.x` ou `10.x.x.x`) conforme a rede real da JCA.

#### 1.4 Reiniciar o serviço PostgreSQL

```powershell
Restart-Service postgresql-x64-16
```

#### 1.5 Instalar Python 3.10+ no servidor

```powershell
# Baixar de https://www.python.org/downloads/
# Durante a instalação, marcar "Add Python to PATH"
# Verificar:
python --version
```

---

### FASE 2 — Adaptar o Código da Aplicação

**Responsável:** Desenvolvedor  
**Tempo estimado:** 4–6 horas

#### 2.1 Instalar dependência do PostgreSQL

Adicionar ao `requirements.txt`:

```
psycopg2-binary>=2.9.9
```

#### 2.2 Criar camada de abstração de banco de dados

Modificar `plan/client_store.py` para suportar tanto SQLite quanto PostgreSQL.

**Estratégia:** Criar uma função `_conn()` que detecta automaticamente qual banco usar baseado na configuração:

```python
import os

def _get_db_backend() -> str:
    """Retorna 'postgresql' se DATABASE_URL estiver configurada, senão 'sqlite'."""
    return "postgresql" if os.environ.get("DATABASE_URL", "").strip() else "sqlite"


def _conn():
    backend = _get_db_backend()

    if backend == "postgresql":
        import psycopg2
        import psycopg2.extras
        con = psycopg2.connect(os.environ["DATABASE_URL"])
        con.autocommit = False
        return con

    else:  # sqlite
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_PATH), timeout=10)
        con.row_factory = sqlite3.Row
        return con
```

#### 2.3 Adaptar queries SQLite → PostgreSQL

Principais diferenças a tratar:

| SQLite | PostgreSQL | Ação |
|--------|-----------|------|
| `AUTOINCREMENT` | `SERIAL` ou `GENERATED ALWAYS AS IDENTITY` | Alterar DDL |
| `?` (placeholder) | `%s` | Ajustar todas as queries |
| `ON CONFLICT(...) DO UPDATE` | `ON CONFLICT(...) DO UPDATE` | ✅ Compatível (PostgreSQL 9.5+) |
| `PRAGMA` | Sem equivalente | Remover/ignorar |
| `con.row_factory = sqlite3.Row` | `psycopg2.extras.RealDictCursor` | Substituir |
| `con.executescript()` | Executar cada statement separadamente | Ajustar `init_db()` |

#### 2.4 Criar o schema PostgreSQL

```sql
-- schema_postgresql.sql

CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    nome TEXT UNIQUE NOT NULL,
    cnpj TEXT NOT NULL DEFAULT '',
    codigo_interno TEXT NOT NULL DEFAULT '',
    grupo TEXT NOT NULL DEFAULT '',
    unidade_jca TEXT NOT NULL DEFAULT '',
    tributacao TEXT NOT NULL DEFAULT '',
    nivel_operacional TEXT NOT NULL DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    conta_banco TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL,
    nome TEXT NOT NULL DEFAULT '',
    departamento TEXT NOT NULL DEFAULT '',
    perfil TEXT NOT NULL DEFAULT 'operacional',
    ativo INTEGER NOT NULL DEFAULT 1,
    troca_senha_obrigatoria INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS depara (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
    classif TEXT NOT NULL,
    conta_debito TEXT NOT NULL DEFAULT '',
    conta_credito TEXT NOT NULL DEFAULT '',
    conta_contabil TEXT NOT NULL DEFAULT '',
    UNIQUE(cliente_id, classif)
);

CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    usuario TEXT NOT NULL,
    acao TEXT NOT NULL,
    detalhes TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS depara_historico (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL,
    cliente_nome TEXT NOT NULL DEFAULT '',
    usuario TEXT NOT NULL,
    acao TEXT NOT NULL,
    detalhes TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conciliacao_templates (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id),
    banco_nome TEXT NOT NULL DEFAULT '',
    nome TEXT NOT NULL DEFAULT 'Padrao',
    config_json TEXT NOT NULL,
    usuario TEXT NOT NULL DEFAULT '',
    atualizado_em TEXT NOT NULL,
    UNIQUE(cliente_id, banco_nome, nome)
);
```

#### 2.5 Lista de arquivos a modificar

| Arquivo | Tipo de mudança |
|---------|----------------|
| `plan/client_store.py` | Adaptar conexão, queries e DDL |
| `requirements.txt` | Adicionar `psycopg2-binary` |
| `core/background_jobs.py` | Verificar se usa paths locais que precisem mudar |
| `config/wizard_config.json` | Sem mudança (configuração do wizard, não do DB) |
| `.streamlit/secrets.toml` | Adicionar `DATABASE_URL` (novo arquivo) |

---

### FASE 3 — Migrar os Dados (SQLite → PostgreSQL)

**Responsável:** Desenvolvedor  
**Tempo estimado:** 30 min

#### 3.1 Script de migração

Criar um script Python que lê todos os dados do SQLite e insere no PostgreSQL:

```python
# scripts/migrar_sqlite_para_postgresql.py

import sqlite3
import psycopg2
from pathlib import Path

SQLITE_PATH = Path(__file__).parent.parent / "data" / "conciliador.db"
PG_URL = "postgresql://conciliador_app:SENHA@SERVIDOR-JCA:5432/conciliador"

def migrar():
    # Conectar às duas bases
    sqlite_con = sqlite3.connect(str(SQLITE_PATH))
    sqlite_con.row_factory = sqlite3.Row
    pg_con = psycopg2.connect(PG_URL)
    pg_cur = pg_con.cursor()

    # Ordem de migração (respeitar foreign keys)
    tabelas = [
        "clientes",
        "usuarios",
        "depara",
        "logs",
        "depara_historico",
        "conciliacao_templates",
    ]

    for tabela in tabelas:
        rows = sqlite_con.execute(f"SELECT * FROM {tabela}").fetchall()
        if not rows:
            print(f"  {tabela}: 0 registros (vazia)")
            continue

        colunas = rows[0].keys()
        placeholders = ", ".join(["%s"] * len(colunas))
        cols_str = ", ".join(colunas)

        for row in rows:
            pg_cur.execute(
                f"INSERT INTO {tabela} ({cols_str}) VALUES ({placeholders})",
                tuple(row)
            )

        print(f"  {tabela}: {len(rows)} registros migrados")

    # Atualizar sequences do PostgreSQL
    for tabela in tabelas:
        try:
            pg_cur.execute(f"""
                SELECT setval(
                    pg_get_serial_sequence('{tabela}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {tabela}), 0) + 1,
                    false
                )
            """)
        except Exception:
            pass

    pg_con.commit()
    pg_con.close()
    sqlite_con.close()
    print("\n✅ Migração concluída com sucesso!")

if __name__ == "__main__":
    migrar()
```

#### 3.2 Executar a migração

```powershell
cd C:\Users\Matheus\Documents\GitHub\Concilia-o
pip install psycopg2-binary
python scripts/migrar_sqlite_para_postgresql.py
```

#### 3.3 Verificar contagens

```sql
-- No pgAdmin ou psql, verificar:
SELECT 'clientes' AS tabela, COUNT(*) FROM clientes
UNION ALL SELECT 'usuarios', COUNT(*) FROM usuarios
UNION ALL SELECT 'depara', COUNT(*) FROM depara
UNION ALL SELECT 'logs', COUNT(*) FROM logs
UNION ALL SELECT 'depara_historico', COUNT(*) FROM depara_historico
UNION ALL SELECT 'conciliacao_templates', COUNT(*) FROM conciliacao_templates;
```

**Resultado esperado:**
| Tabela | Registros |
|--------|-----------|
| clientes | 631 |
| usuarios | 56 |
| depara | 215 |
| logs | 130 |
| depara_historico | 7 |
| conciliacao_templates | 0 |

---

### FASE 4 — Deploy no Servidor

**Responsável:** Desenvolvedor + TI  
**Tempo estimado:** 1–2 horas

#### 4.1 Clonar o repositório no servidor

```powershell
cd D:\Aplicacoes  # ou outro diretório adequado
git clone https://github.com/SEU_USUARIO/Concilia-o.git
cd Concilia-o
```

#### 4.2 Instalar dependências

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### 4.3 Configurar variáveis de ambiente

Criar o arquivo `.streamlit/secrets.toml` no servidor:

```toml
DATABASE_URL = "postgresql://conciliador_app:SENHA_SEGURA@localhost:5432/conciliador"
ADMIN_PASSWORD = "SENHA_ADMIN_FORTE"
```

Ou via variável de ambiente do sistema:

```powershell
[System.Environment]::SetEnvironmentVariable(
    "DATABASE_URL",
    "postgresql://conciliador_app:SENHA_SEGURA@localhost:5432/conciliador",
    "Machine"
)
```

#### 4.4 Configurar o Streamlit para acesso em rede

Criar/editar `.streamlit/config.toml`:

```toml
[server]
address = "0.0.0.0"
port = 8501
headless = true
maxUploadSize = 200

[browser]
gatherUsageStats = false
```

#### 4.5 Iniciar o Streamlit

```powershell
streamlit run app.py
```

#### 4.6 (Opcional) Criar serviço Windows para iniciar automaticamente

Usar o **NSSM** (Non-Sucking Service Manager) para registrar o Streamlit como serviço:

```powershell
# Baixar NSSM: https://nssm.cc/download
nssm install ConciliadorBancario "D:\Aplicacoes\Concilia-o\.venv\Scripts\python.exe"
nssm set ConciliadorBancario AppParameters "-m streamlit run app.py"
nssm set ConciliadorBancario AppDirectory "D:\Aplicacoes\Concilia-o"
nssm set ConciliadorBancario Description "Sistema de Conciliação Bancária - JCA Contadores"
nssm set ConciliadorBancario Start SERVICE_AUTO_START

# Iniciar o serviço
nssm start ConciliadorBancario
```

**Acesso pelos usuários:** `http://SERVIDOR-JCA:8501` (substituir pelo hostname/IP real)

---

### FASE 5 — Backup Automatizado

**Responsável:** TI / Infraestrutura  
**Tempo estimado:** 30 min

#### 5.1 Script de backup diário do PostgreSQL

```powershell
# backup_conciliador.ps1
$pgDumpPath = "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
$backupDir = "D:\Backups\conciliador"
$nomeBackup = "conciliador_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql"

# Criar diretório se não existir
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

# Definir senha via variável de ambiente temporária
$env:PGPASSWORD = "SENHA_SEGURA"

# Executar pg_dump
& $pgDumpPath `
    -h localhost `
    -U conciliador_app `
    -d conciliador `
    -F c `
    -f (Join-Path $backupDir $nomeBackup)

# Limpar variável de senha
Remove-Item Env:\PGPASSWORD

# Manter apenas os últimos 30 backups
Get-ChildItem $backupDir -Filter "conciliador_*.sql" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    Remove-Item -Force

Write-Host "✅ Backup concluído: $nomeBackup"
```

#### 5.2 Agendar no Task Scheduler do Windows

| Campo | Valor |
|-------|-------|
| **Nome** | Backup Conciliador PostgreSQL |
| **Frequência** | Diariamente |
| **Horário** | 23:00 (fora do expediente) |
| **Ação** | `powershell.exe -File "D:\Scripts\backup_conciliador.ps1"` |
| **Executar como** | Conta de serviço com acesso ao PostgreSQL |

#### 5.3 Procedimento de restauração (em caso de emergência)

```powershell
$env:PGPASSWORD = "SENHA_SEGURA"
& "C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" `
    -h localhost `
    -U conciliador_app `
    -d conciliador `
    -c `
    "D:\Backups\conciliador\conciliador_20260512_230000.sql"
```

---

### FASE 6 — Validação Pós-Migração

**Responsável:** Desenvolvedor + Usuários-chave  
**Tempo estimado:** 1 hora

#### Checklist de validação

| # | Teste | Status |
|---|-------|--------|
| 1 | **Login** — Fazer login com um usuário existente | ☐ |
| 2 | **Lista de clientes** — Verificar se carregam as 631 empresas | ☐ |
| 3 | **De x Para** — Selecionar um cliente e confirmar que as regras aparecem | ☐ |
| 4 | **Criar cliente** — Cadastrar empresa de teste e verificar persistência | ☐ |
| 5 | **Logs** — Verificar no painel admin se os logs históricos estão presentes | ☐ |
| 6 | **Upload de extrato** — Fazer upload de um extrato bancário | ☐ |
| 7 | **Conciliação completa** — Executar uma conciliação do início ao fim | ☐ |
| 8 | **Download relatório** — Gerar e baixar o relatório Excel | ☐ |
| 9 | **Multi-acesso** — 3+ usuários usando simultaneamente | ☐ |
| 10 | **Painel admin** — Acessar configurações gerais, KPIs e auditoria | ☐ |
| 11 | **Backup** — Executar script de backup manualmente | ☐ |
| 12 | **Performance** — Tempo de resposta aceitável (< 3s por operação) | ☐ |

---

### FASE 7 — Go-Live e Rollback

#### Procedimento de go-live

1. Comunicar a equipe sobre a data de migração (idealmente sexta-feira à noite ou fim de semana)
2. Congelar o uso do sistema antigo (ninguém usa durante a migração)
3. Executar a migração final (Fases 3-4)
4. Executar validação completa (Fase 6)
5. Distribuir o novo endereço de acesso: `http://SERVIDOR-JCA:8501`
6. Monitorar o primeiro dia útil de uso

#### Plano de rollback (se algo der errado)

```powershell
# 1. Parar o Streamlit no servidor
nssm stop ConciliadorBancario

# 2. Voltar ao modo local em qualquer máquina:
#    - Remover a variável DATABASE_URL
#    - O app volta a usar data/conciliador.db automaticamente

# 3. Os usuários continuam acessando localmente até resolver o problema
```

> **Importante:** Manter o banco SQLite original (`data/conciliador.db`) intacto durante
> pelo menos 30 dias após a migração, como rede de segurança.

---

## 4. Resumo de Estimativa

| Fase | Tempo | Responsável |
|------|-------|-------------|
| Fase 1 — Instalar PostgreSQL no servidor | 1–2h | TI / Infra |
| Fase 2 — Adaptar código Python | 4–6h | Desenvolvedor |
| Fase 3 — Migrar dados SQLite → PostgreSQL | 30min | Desenvolvedor |
| Fase 4 — Deploy no servidor | 1–2h | Desenvolvedor + TI |
| Fase 5 — Configurar backup automático | 30min | TI |
| Fase 6 — Validação completa | 1h | Todos |
| Fase 7 — Go-live | 30min | Todos |
| **Total estimado** | **~8–12 horas** | — |

---

## 5. Decisões Pendentes

| # | Decisão | Status |
|---|---------|--------|
| 1 | Hostname/IP do servidor da JCA | 🔴 Pendente |
| 2 | Senha do PostgreSQL para a aplicação | 🔴 Definir antes da Fase 1 |
| 3 | Diretório de instalação no servidor (`D:\Aplicacoes\` ou outro) | 🔴 Definir com TI |
| 4 | Horário da janela de migração (go-live) | 🔴 Agendar com equipe |
| 5 | Domínio/DNS interno para acesso (ex: `conciliador.jca.local`) | 🟡 Opcional |

---

## 6. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|:------------:|:-------:|-----------|
| Perda de dados durante migração | Baixa | Alto | Backup prévio do SQLite + validação de contagens |
| Incompatibilidade de queries SQL | Média | Médio | Testes unitários existentes (`run_tests.py`) cobrirão regressões |
| Firewall bloqueando porta 5432 | Média | Baixo | Testar conectividade antes do go-live |
| Performance inferior ao esperado | Baixa | Médio | PostgreSQL é mais rápido que SQLite para concorrência |
| Servidor indisponível (queda) | Baixa | Alto | Backup diário + SQLite local como fallback de emergência |

---

## 7. Diagrama de Arquitetura Final

```
    ┌─────────────────────────────────────────────────────────┐
    │                 SERVIDOR JCA CONTADORES                  │
    │                                                         │
    │  ┌──────────────┐      ┌──────────────────────────┐    │
    │  │  Streamlit    │────▶│    PostgreSQL 16          │    │
    │  │  (porta 8501) │      │    (porta 5432)           │    │
    │  │               │      │                          │    │
    │  │  app.py       │      │  DB: conciliador         │    │
    │  │  Python 3.10+ │      │  User: conciliador_app   │    │
    │  └──────────────┘      └──────────────────────────┘    │
    │         ▲                         ▲                     │
    └─────────┼─────────────────────────┼─────────────────────┘
              │                         │
              │  HTTP :8501             │  Backup diário
              │                         │  (Task Scheduler)
    ┌─────────┴─────────┐    ┌─────────┴─────────┐
    │  Navegadores dos   │    │  D:\Backups\       │
    │  50 usuários JCA   │    │  conciliador\      │
    │  (rede interna)    │    │  (retenção 30 dias)│
    └───────────────────┘    └───────────────────┘
```

---

> **Próximo passo:** Assim que o hostname/IP do servidor estiver definido, iniciar a **Fase 1**.
