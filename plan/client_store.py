"""
Persistência SQLite: clientes, usuários, De-Para contábil e logs de auditoria.
"""
from __future__ import annotations
import sqlite3
import hashlib
import os
from pathlib import Path
from typing import Optional, List, Tuple
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


def get_depara(cliente_id: int) -> List[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT classif, conta_debito, conta_credito FROM depara WHERE cliente_id=? ORDER BY classif",
            (cliente_id,),
        ).fetchall()
    return [{"classif": r["classif"], "debito": r["conta_debito"], "credito": r["conta_credito"]} for r in rows]


def upsert_depara(cliente_id: int, classif: str, debito: str, credito: str):
    init_db()
    with _conn() as con:
        con.execute(
            """INSERT INTO depara (cliente_id, classif, conta_debito, conta_credito)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cliente_id, classif) DO UPDATE SET
                 conta_debito=excluded.conta_debito,
                 conta_credito=excluded.conta_credito""",
            (cliente_id, classif, debito, credito),
        )


def delete_depara(cliente_id: int, classif: str):
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM depara WHERE cliente_id=? AND classif=?", (cliente_id, classif))


def import_depara_csv(cliente_id: int, csv_path: str):
    """
    CSV com colunas: classif,debito,credito
    """
    import csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upsert_depara(
                cliente_id,
                row.get("classif", "").strip(),
                row.get("debito", "").strip(),
                row.get("credito", "").strip(),
            )


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
