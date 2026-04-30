"""
Persistência SQLite: clientes, usuários, De-Para contábil e logs de auditoria.
"""
from __future__ import annotations
import io
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional, List
import datetime

try:
    import bcrypt
    _USE_BCRYPT = True
except ImportError:
    _USE_BCRYPT = False

DB_PATH = Path(__file__).parent.parent / "data" / "conciliador.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _migrate(con: sqlite3.Connection):
    """Migrações incrementais de schema — executa apenas o que ainda não existe."""
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

    cols_cli = {r[1] for r in con.execute("PRAGMA table_info(clientes)")}
    if "conta_banco" not in cols_cli:
        con.execute("ALTER TABLE clientes ADD COLUMN conta_banco TEXT NOT NULL DEFAULT ''")


def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            criado_em TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
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
        """)
        _migrate(con)


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


def create_user(email: str, password: str) -> bool:
    init_db()
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO usuarios (email, senha_hash, criado_em) VALUES (?, ?, ?)",
                (email, _hash_pw(password), datetime.datetime.now().isoformat()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user(email: str, password: str) -> bool:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT senha_hash FROM usuarios WHERE email=?", (email,)).fetchone()
    if row is None:
        return False
    return _check_pw(password, row["senha_hash"])


def list_clientes() -> List[str]:
    init_db()
    with _conn() as con:
        rows = con.execute("SELECT nome FROM clientes ORDER BY nome").fetchall()
    return [r["nome"] for r in rows]


def create_cliente(nome: str) -> bool:
    init_db()
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


# ── De-Para ───────────────────────────────────────────────────────────────────

def get_depara(cliente_id: int) -> List[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT classif, conta_contabil FROM depara WHERE cliente_id=? ORDER BY classif",
            (cliente_id,),
        ).fetchall()
    return [{"classif": r["classif"], "conta_contabil": r["conta_contabil"]} for r in rows]


def upsert_depara(cliente_id: int, classif: str, conta_contabil: str):
    init_db()
    with _conn() as con:
        con.execute(
            """INSERT INTO depara (cliente_id, classif, conta_contabil)
               VALUES (?, ?, ?)
               ON CONFLICT(cliente_id, classif) DO UPDATE SET
                 conta_contabil=excluded.conta_contabil""",
            (cliente_id, classif, conta_contabil),
        )


def delete_depara(cliente_id: int, classif: str):
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=? AND classif=?", (cliente_id, classif))


def update_depara_batch(cliente_id: int, rows: List[dict]):
    """Substitui todos os registros De-Para do cliente pelos fornecidos."""
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=?", (cliente_id,))
        for row in rows:
            classif = str(row.get("classif", "")).strip()
            conta = str(row.get("conta_contabil", "")).strip()
            if classif and classif.lower() not in ("nan", "none", ""):
                con.execute(
                    """INSERT INTO depara (cliente_id, classif, conta_contabil)
                       VALUES (?, ?, ?)
                       ON CONFLICT(cliente_id, classif) DO UPDATE SET
                         conta_contabil=excluded.conta_contabil""",
                    (cliente_id, classif, conta),
                )


def import_depara_stream(cliente_id: int, data: bytes, suffix: str) -> int:
    """
    Importa De-Para de bytes (CSV ou Excel).
    Formato: 1ª coluna = classificação financeira, 2ª coluna = conta contábil.
    Retorna o número de registros importados.
    """
    import pandas as pd

    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(io.BytesIO(data), header=0, dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), header=0, dtype=str, encoding="utf-8-sig")

    if len(df.columns) < 2:
        raise ValueError("O arquivo deve ter ao menos 2 colunas.")

    col_classif = df.columns[0]
    col_conta = df.columns[1]
    count = 0
    for _, row in df.iterrows():
        classif = str(row[col_classif]).strip()
        conta = str(row[col_conta]).strip()
        if classif and classif.lower() not in ("nan", "none", ""):
            conta_clean = conta if conta.lower() not in ("nan", "none", "") else ""
            upsert_depara(cliente_id, classif, conta_clean)
            count += 1
    return count


# ── Conta do banco ────────────────────────────────────────────────────────────

def import_depara_csv(cliente_id: int, path: str) -> int:
    """Compatibilidade com a tela antiga: importa CSV a partir de um caminho."""
    with open(path, "rb") as f:
        return import_depara_stream(cliente_id, f.read(), ".csv")


def get_conta_banco(cliente_id: int) -> str:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT conta_banco FROM clientes WHERE id=?", (cliente_id,)).fetchone()
    return row["conta_banco"] if row else ""


def set_conta_banco(cliente_id: int, conta: str):
    init_db()
    with _conn() as con:
        con.execute("UPDATE clientes SET conta_banco=? WHERE id=?", (conta, cliente_id))


# ── Logs ──────────────────────────────────────────────────────────────────────

def log_acao(usuario: str, acao: str, detalhes: str = ""):
    init_db()
    with _conn() as con:
        con.execute(
            "INSERT INTO logs (usuario, acao, detalhes, criado_em) VALUES (?, ?, ?, ?)",
            (usuario, acao, detalhes, datetime.datetime.now().isoformat()),
        )


def list_logs(limit: int = 200) -> List[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT usuario, acao, detalhes, criado_em FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
