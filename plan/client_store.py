"""
Persistência SQLite: clientes, usuários, De-Para contábil e logs de auditoria.
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import hashlib
import tempfile
from pathlib import Path
from typing import Optional, List
import datetime
import pandas as pd

# Garante que _seed_default_admin() seja executado apenas uma vez por processo
# (Streamlit reexecuta o script a cada interação, mas o processo Python persiste)
_db_initialized = False

try:
    import bcrypt
    _USE_BCRYPT = True
except ImportError:
    _USE_BCRYPT = False

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "conciliador.db"


def _configured_db_path() -> Path:
    env_path = os.environ.get("CONCILIADOR_DB_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    try:
        import streamlit as st
        secret_path = str(st.secrets.get("CONCILIADOR_DB_PATH", "")).strip()
        if secret_path:
            return Path(secret_path).expanduser()
    except Exception:
        pass
    return _DEFAULT_DB_PATH


DB_PATH = _configured_db_path()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def get_database_path() -> str:
    return str(DB_PATH)


def export_database_backup() -> bytes:
    """Gera um backup consistente do SQLite atual."""
    init_db()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp_path = Path(tmp.name)
    try:
        with _conn() as src, sqlite3.connect(str(tmp_path)) as dst:
            src.execute("PRAGMA wal_checkpoint(FULL)")
            src.backup(dst)
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def restore_database_backup(data: bytes) -> None:
    """Restaura um backup SQLite validado, substituindo o banco atual."""
    global _db_initialized
    if not data:
        raise ValueError("Arquivo de backup vazio.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        with sqlite3.connect(str(tmp_path)) as con:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        required = {"clientes", "usuarios", "depara"}
        missing = required - tables
        if missing:
            raise ValueError(
                "Backup inválido. Tabelas ausentes: " + ", ".join(sorted(missing))
            )

        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        DB_PATH.write_bytes(tmp_path.read_bytes())
        _db_initialized = False
        init_db()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def get_storage_health() -> dict:
    """Retorna sinais simples para validar se o ambiente esta apto a producao."""
    init_db()
    checks: list[dict] = []
    env_configured = bool(os.environ.get("CONCILIADOR_DB_PATH", "").strip())
    checks.append({
        "item": "Banco fora do padrao local",
        "status": "OK" if env_configured else "ATENCAO",
        "detalhe": "CONCILIADOR_DB_PATH configurado" if env_configured else "Usando data/conciliador.db do projeto",
    })

    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(DB_PATH.parent), delete=True) as tmp:
            tmp.write(b"ok")
        checks.append({"item": "Diretorio do banco gravavel", "status": "OK", "detalhe": str(DB_PATH.parent)})
    except Exception as e:
        checks.append({"item": "Diretorio do banco gravavel", "status": "ERRO", "detalhe": str(e)})

    try:
        with _conn() as con:
            tables = {
                row[0]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        required = {"clientes", "usuarios", "depara", "conciliacao_templates", "logs"}
        missing = required - tables
        checks.append({
            "item": "Schema SQLite",
            "status": "OK" if not missing else "ERRO",
            "detalhe": "Tabelas principais presentes" if not missing else "Ausentes: " + ", ".join(sorted(missing)),
        })
    except Exception as e:
        checks.append({"item": "Schema SQLite", "status": "ERRO", "detalhe": str(e)})

    try:
        backup_size = len(export_database_backup())
        checks.append({"item": "Backup exportavel", "status": "OK", "detalhe": f"{backup_size} bytes"})
    except Exception as e:
        checks.append({"item": "Backup exportavel", "status": "ERRO", "detalhe": str(e)})

    return {"db_path": str(DB_PATH), "checks": checks}


def _migrate(con: sqlite3.Connection):
    """Migrações incrementais de schema — executa apenas o que ainda não existe."""
    # depara
    cols_dep = {r[1] for r in con.execute("PRAGMA table_info(depara)")}
    if "conta_contabil" not in cols_dep:
        con.execute("ALTER TABLE depara ADD COLUMN conta_contabil TEXT NOT NULL DEFAULT ''")
    if "conta_debito" in cols_dep or "conta_credito" in cols_dep:
        con.execute(
            """UPDATE depara
               SET conta_contabil = CASE
                   WHEN TRIM(COALESCE(conta_contabil, '')) <> '' THEN conta_contabil
                   WHEN TRIM(COALESCE(conta_debito, '')) <> '' THEN conta_debito
                   ELSE COALESCE(conta_credito, '')
               END
               WHERE TRIM(COALESCE(conta_contabil, '')) = ''"""
        )

    # clientes
    cols_cli = {r[1] for r in con.execute("PRAGMA table_info(clientes)")}
    if "conta_banco" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN conta_banco TEXT NOT NULL DEFAULT ''")
    con.execute(
        """UPDATE clientes
           SET codigo_interno = '0' || TRIM(codigo_interno)
           WHERE TRIM(codigo_interno) GLOB '[0-9][0-9][0-9]'"""
    )
    if "cnpj" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN cnpj TEXT NOT NULL DEFAULT ''")
    if "codigo_interno" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN codigo_interno TEXT NOT NULL DEFAULT ''")
    if "ativo" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1")
    if "grupo" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN grupo TEXT NOT NULL DEFAULT ''")
    if "unidade_jca" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN unidade_jca TEXT NOT NULL DEFAULT ''")
    if "tributacao" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN tributacao TEXT NOT NULL DEFAULT ''")
    if "nivel_operacional" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN nivel_operacional TEXT NOT NULL DEFAULT ''")

    # usuarios — novas colunas de governança
    cols_usr = {r[1] for r in con.execute("PRAGMA table_info(usuarios)")}
    if "perfil" not in cols_usr:
        con.execute("ALTER TABLE usuarios ADD COLUMN perfil TEXT NOT NULL DEFAULT 'operacional'")
        # Garante que o admin padrão receba o perfil correto se já existia
        con.execute(
            "UPDATE usuarios SET perfil='admin' WHERE email='felipe.r@jcacontadores.com.br'"
        )
    if "ativo" not in cols_usr:
        con.execute("ALTER TABLE usuarios ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1")
    if "troca_senha_obrigatoria" not in cols_usr:
        con.execute("ALTER TABLE usuarios ADD COLUMN troca_senha_obrigatoria INTEGER NOT NULL DEFAULT 0")
    if "nome" not in cols_usr:
        con.execute("ALTER TABLE usuarios ADD COLUMN nome TEXT NOT NULL DEFAULT ''")
    if "departamento" not in cols_usr:
        con.execute("ALTER TABLE usuarios ADD COLUMN departamento TEXT NOT NULL DEFAULT ''")

    # templates de conciliacao
    cols_tpl = {r[1] for r in con.execute("PRAGMA table_info(conciliacao_templates)")}
    if "criado_em" not in cols_tpl:
        con.execute("ALTER TABLE conciliacao_templates ADD COLUMN criado_em TEXT NOT NULL DEFAULT ''")
        con.execute(
            """UPDATE conciliacao_templates
               SET criado_em = COALESCE(NULLIF(atualizado_em, ''), datetime('now'))
               WHERE TRIM(COALESCE(criado_em, '')) = ''"""
        )


def init_db():
    global _db_initialized
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            cnpj TEXT NOT NULL DEFAULT '',
            codigo_interno TEXT NOT NULL DEFAULT '',
            grupo TEXT NOT NULL DEFAULT '',
            unidade_jca TEXT NOT NULL DEFAULT '',
            tributacao TEXT NOT NULL DEFAULT '',
            nivel_operacional TEXT NOT NULL DEFAULT '',
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            classif TEXT NOT NULL,
            conta_debito TEXT NOT NULL DEFAULT '',
            conta_credito TEXT NOT NULL DEFAULT '',
            UNIQUE(cliente_id, classif)
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL,
            acao TEXT NOT NULL,
            detalhes TEXT,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS depara_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            cliente_nome TEXT NOT NULL DEFAULT '',
            usuario TEXT NOT NULL,
            acao TEXT NOT NULL,
            detalhes TEXT,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conciliacao_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            banco_nome TEXT NOT NULL DEFAULT '',
            nome TEXT NOT NULL DEFAULT 'Padrao',
            config_json TEXT NOT NULL,
            usuario TEXT NOT NULL DEFAULT '',
            criado_em TEXT NOT NULL DEFAULT '',
            atualizado_em TEXT NOT NULL,
            UNIQUE(cliente_id, banco_nome, nome)
        );
        """)
        _migrate(con)
    if not _db_initialized:
        _seed_default_admin()
        _db_initialized = True


def _seed_default_admin():
    """Cria o admin padrão se não existir; atualiza a senha se ADMIN_PASSWORD estiver definida."""
    email = "felipe.r@jcacontadores.com.br"
    env_pw = os.environ.get("ADMIN_PASSWORD", "").strip()
    with _conn() as con:
        row = con.execute("SELECT id FROM usuarios WHERE email=?", (email,)).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO usuarios (email, senha_hash, perfil, criado_em) VALUES (?, ?, ?, ?)",
                (email, _hash_pw(env_pw or "123456"), "admin", datetime.datetime.now().isoformat()),
            )
        elif env_pw:
            # Se ADMIN_PASSWORD estiver configurada no ambiente, prevalece sobre o DB commitado
            con.execute(
                "UPDATE usuarios SET senha_hash=?, troca_senha_obrigatoria=0 WHERE email=?",
                (_hash_pw(env_pw), email),
            )


# -- Hashing ----------------------------------------------------------------------

def _hash_pw(password: str) -> str:
    if _USE_BCRYPT:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return hashlib.sha256(password.encode()).hexdigest()


def _check_pw(password: str, hashed: str) -> bool:
    if _USE_BCRYPT:
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            pass
    return hashlib.sha256(password.encode()).hexdigest() == hashed


# -- Autenticação e usuários ------------------------------------------------------

def authenticate_user(email: str, password: str) -> dict | None:
    """Retorna dict do usuário se autenticado com sucesso, None caso contrário."""
    with _conn() as con:
        row = con.execute(
            "SELECT id, email, senha_hash, nome, departamento, perfil, ativo, troca_senha_obrigatoria FROM usuarios WHERE email=?",
            (email,),
        ).fetchone()
    if row is None:
        return None
    if not _check_pw(password, row["senha_hash"]):
        return None
    return dict(row)


def get_user(email: str, password: str) -> bool:
    """Compatibilidade retroativa."""
    return authenticate_user(email, password) is not None


def create_user(
    email: str,
    password: str,
    perfil: str = "operacional",
    nome: str = "",
    departamento: str = "",
) -> bool:
    try:
        with _conn() as con:
            con.execute(
                """INSERT INTO usuarios
                   (email, senha_hash, nome, departamento, perfil, criado_em)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    email,
                    _hash_pw(password),
                    nome,
                    departamento,
                    perfil,
                    datetime.datetime.now().isoformat(),
                ),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def list_users() -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, email, nome, departamento, perfil, ativo,
                      troca_senha_obrigatoria, criado_em
               FROM usuarios
               ORDER BY email"""
        ).fetchall()
    return [dict(r) for r in rows]


def update_user(
    user_id: int,
    perfil: str = None,
    ativo: int = None,
    nome: str = None,
    departamento: str = None,
):
    parts, vals = [], []
    if perfil is not None:
        parts.append("perfil=?"); vals.append(perfil)
    if ativo is not None:
        parts.append("ativo=?"); vals.append(ativo)
    if nome is not None:
        parts.append("nome=?"); vals.append(nome)
    if departamento is not None:
        parts.append("departamento=?"); vals.append(departamento)
    if not parts:
        return
    vals.append(user_id)
    with _conn() as con:
        con.execute(f"UPDATE usuarios SET {', '.join(parts)} WHERE id=?", vals)


def admin_reset_password(user_id: int, new_password: str):
    """Admin redefine senha e ativa troca obrigatória no próximo login."""
    with _conn() as con:
        con.execute(
            "UPDATE usuarios SET senha_hash=?, troca_senha_obrigatoria=1 WHERE id=?",
            (_hash_pw(new_password), user_id),
        )


def force_change_password(email: str, new_password: str):
    """Troca a senha e limpa o flag de troca obrigatória."""
    with _conn() as con:
        con.execute(
            "UPDATE usuarios SET senha_hash=?, troca_senha_obrigatoria=0 WHERE email=?",
            (_hash_pw(new_password), email),
        )


def change_password(email: str, old_password: str, new_password: str) -> bool:
    """Troca de senha pelo próprio usuário — valida a senha atual antes."""
    if authenticate_user(email, old_password) is None:
        return False
    with _conn() as con:
        con.execute(
            "UPDATE usuarios SET senha_hash=? WHERE email=?",
            (_hash_pw(new_password), email),
        )
    return True


# -- Clientes ---------------------------------------------------------------------

def list_clientes(apenas_ativos: bool = True) -> List[str]:
    with _conn() as con:
        if apenas_ativos:
            rows = con.execute("SELECT nome FROM clientes WHERE ativo=1 ORDER BY nome").fetchall()
        else:
            rows = con.execute("SELECT nome FROM clientes ORDER BY nome").fetchall()
    return [r["nome"] for r in rows]


def _cliente_label(row: dict | sqlite3.Row) -> str:
    codigo = _normalizar_codigo_empresa(row["codigo_interno"] or "") or "SEM CODIGO"
    nome = str(row["nome"] or "").strip()
    return f"{codigo} - {nome}"


def _normalizar_codigo_empresa(codigo: str) -> str:
    codigo = str(codigo or "").strip()
    return codigo.zfill(4) if codigo.isdigit() and len(codigo) == 3 else codigo


def list_clientes_display(apenas_ativos: bool = True) -> List[dict]:
    """Lista empresas com label de exibição no formato código - nome."""
    with _conn() as con:
        where = "WHERE ativo=1" if apenas_ativos else ""
        rows = con.execute(
            f"""SELECT id, nome, codigo_interno, grupo, ativo
                FROM clientes {where}
                ORDER BY codigo_interno, nome"""
        ).fetchall()
    result = [dict(r) for r in rows]
    for row in result:
        row["codigo_interno"] = _normalizar_codigo_empresa(row.get("codigo_interno", ""))
        row["label"] = _cliente_label(row)
    return result


def get_cliente_by_id(cliente_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            """SELECT id, nome, cnpj, codigo_interno, grupo, unidade_jca,
                      tributacao, nivel_operacional, ativo, conta_banco
               FROM clientes WHERE id=?""",
            (cliente_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["codigo_interno"] = _normalizar_codigo_empresa(result.get("codigo_interno", ""))
    result["label"] = _cliente_label(result)
    return result


def create_cliente(nome: str) -> bool:
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO clientes (nome, criado_em) VALUES (?, ?)",
                (nome, datetime.datetime.now().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_cliente_id(nome: str) -> Optional[int]:
    with _conn() as con:
        row = con.execute("SELECT id FROM clientes WHERE nome=?", (nome,)).fetchone()
    return row["id"] if row else None


def get_cliente_label(cliente_id: int) -> str:
    row = get_cliente_by_id(cliente_id)
    return row["label"] if row else ""


def get_clientes_do_grupo(cliente_id: int, apenas_ativos: bool = True) -> List[dict]:
    cliente = get_cliente_by_id(cliente_id)
    grupo = str((cliente or {}).get("grupo") or "").strip()
    if not grupo:
        return []
    with _conn() as con:
        where_ativo = "AND ativo=1" if apenas_ativos else ""
        rows = con.execute(
            f"""SELECT id, nome, codigo_interno, grupo, ativo
                FROM clientes
                WHERE grupo=? {where_ativo}
                ORDER BY codigo_interno, nome""",
            (grupo,),
        ).fetchall()
    result = [dict(r) for r in rows]
    for row in result:
        row["codigo_interno"] = _normalizar_codigo_empresa(row.get("codigo_interno", ""))
        row["label"] = _cliente_label(row)
    return result


def get_clientes_with_stats() -> List[dict]:
    """Retorna clientes com todos os campos e contagem de regras De x Para."""
    with _conn() as con:
        rows = con.execute(
            """SELECT c.id, c.nome, c.cnpj, c.codigo_interno, c.grupo,
                      c.unidade_jca, c.tributacao, c.nivel_operacional,
                      c.ativo, c.criado_em,
                      COUNT(d.id) AS num_depara
               FROM clientes c
               LEFT JOIN depara d ON d.cliente_id = c.id
               GROUP BY c.id
               ORDER BY c.nome"""
        ).fetchall()
    result = [dict(r) for r in rows]
    for row in result:
        row["codigo_interno"] = _normalizar_codigo_empresa(row.get("codigo_interno", ""))
    return result


def update_cliente(
    cliente_id: int,
    nome: str = None,
    cnpj: str = None,
    codigo_interno: str = None,
    grupo: str = None,
    unidade_jca: str = None,
    tributacao: str = None,
    nivel_operacional: str = None,
    ativo: int = None,
):
    """Atualiza campos opcionais do cadastro de uma empresa."""
    if codigo_interno is not None:
        codigo_interno = _normalizar_codigo_empresa(codigo_interno)

    fields = dict(
        nome=nome, cnpj=cnpj, codigo_interno=codigo_interno,
        grupo=grupo, unidade_jca=unidade_jca, tributacao=tributacao,
        nivel_operacional=nivel_operacional, ativo=ativo,
    )
    parts = [f"{k}=?" for k, v in fields.items() if v is not None]
    vals = [v for v in fields.values() if v is not None]
    if not parts:
        return
    vals.append(cliente_id)
    with _conn() as con:
        con.execute(f"UPDATE clientes SET {', '.join(parts)} WHERE id=?", vals)


def import_clientes_bulk(data: bytes, suffix: str) -> dict:
    """
    Importa empresas de planilha Excel ou CSV.
    Detecta automaticamente as colunas e faz upsert por código ou nome.
    Retorna {'inseridos': N, 'atualizados': M, 'ignorados': K}.
    """
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(data), header=0, dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), header=0, dtype=str, encoding="utf-8-sig")

    df.columns = [str(c).strip() for c in df.columns]

    def _det(patterns: list[str]) -> str | None:
        for col in df.columns:
            if any(p.upper() in col.upper() for p in patterns):
                return col
        return None

    def _clean(val) -> str:
        s = str(val).strip()
        return "" if s.lower() in ("nan", "none", "nat", "") else s

    col_nome = _det(["EMPRESA", "NOME", "RAZÃO", "RAZAO"])
    col_cnpj = _det(["CNPJ"])
    col_cod  = _det(["CÓD", "COD", "CÓDIGO", "CODIGO"])
    col_grp  = _det(["GRUPO"])
    col_und  = _det(["UNIDADE"])
    col_tri  = _det(["TRIBUT"])
    col_niv  = _det(["NÍVEL", "NIVEL"])

    if not col_nome:
        raise ValueError("Coluna de nome da empresa não encontrada.")

    inseridos = atualizados = ignorados = 0
    now = datetime.datetime.now().isoformat()

    with _conn() as con:
        for row in df.to_dict("records"):
            nome = _clean(row.get(col_nome, ""))
            if not nome:
                ignorados += 1
                continue

            cnpj     = _clean(row.get(col_cnpj, "")) if col_cnpj else ""
            cod      = _normalizar_codigo_empresa(_clean(row.get(col_cod, ""))) if col_cod else ""
            grupo    = _clean(row.get(col_grp,  "")) if col_grp  else ""
            unidade  = _clean(row.get(col_und,  "")) if col_und  else ""
            tribut   = _clean(row.get(col_tri,  "")) if col_tri  else ""
            nivel    = _clean(row.get(col_niv,  "")) if col_niv  else ""

            existing = None
            if cod:
                existing = con.execute(
                    "SELECT id FROM clientes WHERE codigo_interno=?", (cod,)
                ).fetchone()
            if not existing:
                existing = con.execute(
                    "SELECT id FROM clientes WHERE nome=?", (nome,)
                ).fetchone()

            if existing:
                con.execute(
                    """UPDATE clientes SET nome=?, cnpj=?, codigo_interno=?, grupo=?,
                       unidade_jca=?, tributacao=?, nivel_operacional=? WHERE id=?""",
                    (nome, cnpj, cod, grupo, unidade, tribut, nivel, existing["id"]),
                )
                atualizados += 1
            else:
                try:
                    con.execute(
                        """INSERT INTO clientes (nome, cnpj, codigo_interno, grupo, unidade_jca,
                           tributacao, nivel_operacional, criado_em)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (nome, cnpj, cod, grupo, unidade, tribut, nivel, now),
                    )
                    inseridos += 1
                except sqlite3.IntegrityError:
                    ignorados += 1

    return {"inseridos": inseridos, "atualizados": atualizados, "ignorados": ignorados}


def get_kpis() -> dict:
    """KPIs de governança para o banner do painel admin."""
    now = datetime.datetime.now()
    d1 = (now - datetime.timedelta(days=1)).isoformat()
    d7 = (now - datetime.timedelta(days=7)).isoformat()
    d30 = (now - datetime.timedelta(days=30)).isoformat()
    with _conn() as con:
        usuarios_ativos = con.execute("SELECT COUNT(*) FROM usuarios WHERE ativo=1").fetchone()[0]
        empresas_ativas = con.execute("SELECT COUNT(*) FROM clientes WHERE ativo=1").fetchone()[0]
        logins_24h = con.execute(
            "SELECT COUNT(*) FROM logs WHERE acao='LOGIN_SUCESSO' AND criado_em >= ?", (d1,)
        ).fetchone()[0]
        depara_semana = con.execute(
            "SELECT COUNT(*) FROM logs WHERE acao='DEPARA_ALTERADO' AND criado_em >= ?", (d7,)
        ).fetchone()[0]
        relatorios_mes = con.execute(
            "SELECT COUNT(*) FROM logs WHERE acao='RELATORIO_GERADO' AND criado_em >= ?", (d30,)
        ).fetchone()[0]
        total_depara = con.execute("SELECT COUNT(*) FROM depara").fetchone()[0]
    return {
        "usuarios_ativos": usuarios_ativos,
        "empresas_ativas": empresas_ativas,
        "logins_24h": logins_24h,
        "depara_semana": depara_semana,
        "relatorios_mes": relatorios_mes,
        "total_depara": total_depara,
    }


def get_user_activity(data_inicio: str = "", data_fim: str = "") -> List[dict]:
    """Resumo de atividade por usuário, opcionalmente filtrado por período."""
    where = ""
    params: list[str] = []
    if data_inicio:
        where += " AND criado_em >= ?"
        params.append(data_inicio)
    if data_fim:
        where += " AND criado_em <= ?"
        params.append(data_fim + "T23:59:59")

    def _count_query(acao: str) -> tuple[str, list[str]]:
        return (
            f"SELECT usuario, COUNT(*) FROM logs WHERE acao=?{where} GROUP BY usuario",
            [acao] + params,
        )

    with _conn() as con:
        users = con.execute(
            "SELECT email, nome, departamento, perfil, ativo FROM usuarios ORDER BY email"
        ).fetchall()
        q, p = _count_query("LOGIN_SUCESSO")
        logins = dict(con.execute(q, p).fetchall())
        q, p = _count_query("RELATORIO_GERADO")
        relatorios = dict(con.execute(q, p).fetchall())
        q, p = _count_query("DEPARA_ALTERADO")
        depara_ch = dict(con.execute(q, p).fetchall())
        q, p = _count_query("CONCILIACAO_REALIZADA")
        conciliacoes = dict(con.execute(q, p).fetchall())
        last_acc = dict(con.execute(
            f"SELECT usuario, MAX(criado_em) FROM logs WHERE 1=1{where} GROUP BY usuario",
            params,
        ).fetchall())
    return [
        {
            "email": u["email"],
            "nome": u["nome"],
            "departamento": u["departamento"],
            "perfil": u["perfil"],
            "ativo": u["ativo"],
            "total_logins": logins.get(u["email"], 0),
            "relatorios_gerados": relatorios.get(u["email"], 0),
            "depara_alterados": depara_ch.get(u["email"], 0),
            "conciliacoes": conciliacoes.get(u["email"], 0),
            "ultimo_acesso": last_acc.get(u["email"]),
        }
        for u in users
    ]


def rename_cliente(cliente_id: int, novo_nome: str) -> bool:
    try:
        with _conn() as con:
            con.execute("UPDATE clientes SET nome=? WHERE id=?", (novo_nome, cliente_id))
        return True
    except sqlite3.IntegrityError:
        return False


def delete_cliente(cliente_id: int):
    """Remove o cliente e todas as suas regras De-Para."""
    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=?", (cliente_id,))
        con.execute("DELETE FROM conciliacao_templates WHERE cliente_id=?", (cliente_id,))
        con.execute("DELETE FROM clientes WHERE id=?", (cliente_id,))


# -- De-Para ----------------------------------------------------------------------

def get_depara(cliente_id: int) -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT classif, conta_contabil FROM depara WHERE cliente_id=? ORDER BY classif",
            (cliente_id,),
        ).fetchall()
    return [{"classif": r["classif"], "conta_contabil": r["conta_contabil"]} for r in rows]


def upsert_depara(cliente_id: int, classif: str, conta_contabil: str):
    with _conn() as con:
        con.execute(
            """INSERT INTO depara (cliente_id, classif, conta_contabil)
               VALUES (?, ?, ?)
               ON CONFLICT(cliente_id, classif) DO UPDATE SET
                 conta_contabil=excluded.conta_contabil""",
            (cliente_id, classif, conta_contabil),
        )


def delete_depara(cliente_id: int, classif: str):
    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=? AND classif=?", (cliente_id, classif))


def update_depara_batch(cliente_id: int, rows: List[dict]):
    """Substitui todos os registros De-Para do cliente pelos fornecidos."""
    registros = []
    for row in rows:
        classif = str(row.get("classif", "")).strip()
        conta = str(row.get("conta_contabil", "")).strip()
        if classif and classif.lower() not in ("nan", "none", ""):
            registros.append((cliente_id, classif, conta))

    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=?", (cliente_id,))
        con.executemany(
            """INSERT INTO depara (cliente_id, classif, conta_contabil)
               VALUES (?, ?, ?)
               ON CONFLICT(cliente_id, classif) DO UPDATE SET
                 conta_contabil=excluded.conta_contabil""",
            registros,
        )


def replicate_depara(
    origem_cliente_id: int,
    destino_cliente_ids: List[int],
    substituir: bool = False,
) -> dict:
    """
    Replica regras De x Para de uma empresa origem para empresas destino.
    Quando substituir=True, apaga a base destino antes de copiar.
    Quando False, mescla/atualiza classificações iguais e preserva as demais.
    """
    destino_ids = sorted({int(cid) for cid in destino_cliente_ids if int(cid) != int(origem_cliente_id)})
    if not destino_ids:
        return {"empresas": 0, "regras_origem": 0, "gravadas": 0}

    with _conn() as con:
        origem_rows = con.execute(
            "SELECT classif, conta_contabil FROM depara WHERE cliente_id=? ORDER BY classif",
            (origem_cliente_id,),
        ).fetchall()
        regras = [
            (str(r["classif"]).strip(), str(r["conta_contabil"]).strip())
            for r in origem_rows
            if str(r["classif"]).strip()
        ]
        if not regras:
            return {"empresas": len(destino_ids), "regras_origem": 0, "gravadas": 0}

        gravadas = 0
        for destino_id in destino_ids:
            if substituir:
                con.execute("DELETE FROM depara WHERE cliente_id=?", (destino_id,))
            for classif, conta in regras:
                con.execute(
                    """INSERT INTO depara (cliente_id, classif, conta_contabil)
                       VALUES (?, ?, ?)
                       ON CONFLICT(cliente_id, classif) DO UPDATE SET
                         conta_contabil=excluded.conta_contabil""",
                    (destino_id, classif, conta),
                )
                gravadas += 1

    return {"empresas": len(destino_ids), "regras_origem": len(regras), "gravadas": gravadas}


def log_depara_change(cliente_id: int, cliente_nome: str, usuario: str, acao: str, detalhes: str = ""):
    """Registra alteração no De x Para no histórico dedicado."""
    with _conn() as con:
        con.execute(
            """INSERT INTO depara_historico (cliente_id, cliente_nome, usuario, acao, detalhes, criado_em)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cliente_id, cliente_nome, usuario, acao, detalhes, datetime.datetime.now().isoformat()),
        )


def get_depara_historico(cliente_id: int, limit: int = 200) -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT usuario, acao, detalhes, criado_em FROM depara_historico
               WHERE cliente_id=? ORDER BY id DESC LIMIT ?""",
            (cliente_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def import_depara_stream(cliente_id: int, data: bytes, suffix: str) -> int:
    import pandas as pd
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(data), header=0, dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), header=0, dtype=str, encoding="utf-8-sig")
    if len(df.columns) < 2:
        raise ValueError("O arquivo deve ter ao menos 2 colunas.")
    col_classif = df.columns[0]
    col_conta = df.columns[1]
    registros = []
    for row in df.to_dict("records"):
        classif = str(row[col_classif]).strip()
        conta = str(row[col_conta]).strip()
        if classif and classif.lower() not in ("nan", "none", ""):
            conta_clean = conta if conta.lower() not in ("nan", "none", "") else ""
            registros.append((cliente_id, classif, conta_clean))

    with _conn() as con:
        con.executemany(
            """INSERT INTO depara (cliente_id, classif, conta_contabil)
               VALUES (?, ?, ?)
               ON CONFLICT(cliente_id, classif) DO UPDATE SET
                 conta_contabil=excluded.conta_contabil""",
            registros,
        )
    return len(registros)


def import_depara_csv(cliente_id: int, path: str) -> int:
    with open(path, "rb") as f:
        return import_depara_stream(cliente_id, f.read(), ".csv")


# -- Conta do banco ---------------------------------------------------------------

def get_conta_banco(cliente_id: int) -> str:
    with _conn() as con:
        row = con.execute("SELECT conta_banco FROM clientes WHERE id=?", (cliente_id,)).fetchone()
    return row["conta_banco"] if row else ""


def set_conta_banco(cliente_id: int, conta: str):
    with _conn() as con:
        con.execute("UPDATE clientes SET conta_banco=? WHERE id=?", (conta, cliente_id))


# -- Templates de configuração do wizard -----------------------------------------

def list_conciliacao_templates(cliente_id: int) -> List[dict]:
    with _conn() as con:
        rows = con.execute(
            """SELECT id, cliente_id, banco_nome, nome, usuario, criado_em, atualizado_em
               FROM conciliacao_templates
               WHERE cliente_id=?
               ORDER BY banco_nome COLLATE NOCASE, nome COLLATE NOCASE""",
            (cliente_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conciliacao_template(template_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            """SELECT id, cliente_id, banco_nome, nome, config_json, usuario, criado_em, atualizado_em
               FROM conciliacao_templates
               WHERE id=?""",
            (template_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["config"] = json.loads(result.pop("config_json") or "{}")
    except json.JSONDecodeError:
        result["config"] = {}
    return result


def upsert_conciliacao_template(
    cliente_id: int,
    banco_nome: str,
    nome: str,
    config: dict,
    usuario: str = "",
) -> int:
    banco_nome = str(banco_nome or "").strip()
    nome = str(nome or "Padrao").strip() or "Padrao"
    payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True)
    now = datetime.datetime.now().isoformat()
    with _conn() as con:
        con.execute(
            """INSERT INTO conciliacao_templates
                 (cliente_id, banco_nome, nome, config_json, usuario, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(cliente_id, banco_nome, nome) DO UPDATE SET
                 config_json=excluded.config_json,
                 usuario=excluded.usuario,
                 atualizado_em=excluded.atualizado_em""",
            (cliente_id, banco_nome, nome, payload, usuario, now, now),
        )
        row = con.execute(
            """SELECT id FROM conciliacao_templates
               WHERE cliente_id=? AND banco_nome=? AND nome=?""",
            (cliente_id, banco_nome, nome),
        ).fetchone()
    return int(row["id"]) if row else 0


def delete_conciliacao_template(template_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM conciliacao_templates WHERE id=?", (template_id,))


def list_banco_templates(cliente_id: int) -> List[dict]:
    """Lista templates de banco (exclui o template financeiro reservado)."""
    return [
        t for t in list_conciliacao_templates(cliente_id)
        if t.get("banco_nome") != "__financeiro__"
    ]


def list_fin_templates(cliente_id: int) -> List[dict]:
    """Lista templates financeiros da empresa."""
    return [
        t for t in list_conciliacao_templates(cliente_id)
        if t.get("banco_nome") == "__financeiro__"
    ]


def get_fin_template(cliente_id: int) -> Optional[dict]:
    """Retorna o template financeiro único da empresa."""
    with _conn() as con:
        row = con.execute(
            """SELECT id, config_json FROM conciliacao_templates
               WHERE cliente_id=? AND banco_nome='__financeiro__'
               ORDER BY atualizado_em DESC, id DESC
               LIMIT 1""",
            (cliente_id,),
        ).fetchone()
    if not row:
        return None
    try:
        config = json.loads(row["config_json"] or "{}")
    except Exception:
        config = {}
    return {"id": int(row["id"]), "config": config}


# -- Logs -------------------------------------------------------------------------

def log_acao(usuario: str, acao: str, detalhes: str = ""):
    with _conn() as con:
        con.execute(
            "INSERT INTO logs (usuario, acao, detalhes, criado_em) VALUES (?, ?, ?, ?)",
            (usuario, acao, detalhes, datetime.datetime.now().isoformat()),
        )


def list_logs(
    limit: int = 500,
    usuario_filter: str = "",
    acao_filter: str = "",
    detalhes_filter: str = "",
    data_inicio: str = "",
    data_fim: str = "",
) -> List[dict]:
    query = "SELECT usuario, acao, detalhes, criado_em FROM logs WHERE 1=1"
    params: list = []
    if usuario_filter:
        query += " AND usuario LIKE ?"
        params.append(f"%{usuario_filter}%")
    if acao_filter:
        query += " AND acao=?"
        params.append(acao_filter)
    if detalhes_filter:
        query += " AND detalhes LIKE ?"
        params.append(f"%{detalhes_filter}%")
    if data_inicio:
        query += " AND criado_em >= ?"
        params.append(data_inicio)
    if data_fim:
        query += " AND criado_em <= ?"
        params.append(data_fim + "T23:59:59")
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [dict(r) for r in rows]
