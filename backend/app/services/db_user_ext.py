import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Optional

DB_PATH = os.getenv("NERAVA_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "nerava.db"))
DB_PATH = os.path.abspath(DB_PATH)

@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    yield con
    con.commit()
    con.close()

def _ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS users(
        email TEXT PRIMARY KEY,
        name TEXT,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")

def register_user(email: str, name: str = "") -> dict:
    with _conn() as con:
        _ensure_schema(con)
        row = con.execute("SELECT email,name,created_at FROM users WHERE email=?", (email,)).fetchone()
        if row:
            return dict(row)
        con.execute("INSERT INTO users(email,name) VALUES(?,?)", (email, name))
        row = con.execute("SELECT email,name,created_at FROM users WHERE email=?", (email,)).fetchone()
        return dict(row)

def get_user(email: str) -> Optional[Dict]:
    with _conn() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT email, name, created_at FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        return dict(row) if row else None
