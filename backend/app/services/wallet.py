# app/services/wallet.py
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict

from ..core.env import is_local_env

logger = logging.getLogger(__name__)

# You can override via env; defaults to a local file next to the app.
DB_PATH = os.getenv("NERAVA_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "nerava.db"))
DB_PATH = os.path.abspath(DB_PATH)

# ---------- SQLite helpers ----------

@contextmanager
def _conn():
    """
    Get database connection. In production, raises exception on failure (fail-closed).
    In local dev, allows fallback to in-memory store.
    """
    is_local = is_local_env()
    con = None
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("PRAGMA journal_mode=WAL;")
        con.row_factory = sqlite3.Row
        yield con
        con.commit()
    except Exception as e:
        # In production, fail-closed: raise exception instead of falling back
        if not is_local:
            logger.error(f"Database error in wallet service (production): {e}", exc_info=True)
            raise RuntimeError(f"Wallet database error: {str(e)}") from e
        # In local dev, allow fallback to in-memory store
        logger.warning(f"Database error in wallet service (local dev, using fallback): {e}")
        yield None
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass

def _ensure_schema(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS wallet (
            user_id TEXT PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD'
        )
    """)

# ---------- In-memory fallback ----------

_mem_store: Dict[str, Dict[str, Any]] = {}

def _mem_get(user_id: str) -> Dict[str, Any]:
    if user_id not in _mem_store:
        _mem_store[user_id] = {"user_id": user_id, "balance_cents": 0, "currency": "USD"}
    return _mem_store[user_id]

def _mem_set(user_id: str, balance_cents: int, currency: str = "USD") -> Dict[str, Any]:
    _mem_store[user_id] = {"user_id": user_id, "balance_cents": int(balance_cents), "currency": currency}
    return _mem_store[user_id]

# ---------- Public API ----------

def get_wallet(user_id: str, currency: str = "USD") -> Dict[str, Any]:
    """
    Return { user_id, balance_cents, currency }
    Creates a wallet with zero balance if it doesn't exist.
    In production, raises exception on DB error (fail-closed).
    """
    with _conn() as con:
        if con is None:
            # Fallback only in local dev
            if is_local_env():
                return _mem_get(user_id)
            else:
                raise RuntimeError("Database connection failed in production")

        _ensure_schema(con)
        row = con.execute("SELECT user_id, balance_cents, currency FROM wallet WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            con.execute("INSERT INTO wallet (user_id, balance_cents, currency) VALUES (?, ?, ?)", (user_id, 0, currency))
            return {"user_id": user_id, "balance_cents": 0, "currency": currency}
        return {"user_id": row["user_id"], "balance_cents": int(row["balance_cents"]), "currency": row["currency"]}

def credit_wallet(user_id: str, amount_cents: int, currency: str = "USD") -> Dict[str, Any]:
    """
    Increment balance by amount_cents (must be >= 0). Returns updated wallet.
    In production, raises exception on DB error (fail-closed).
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    with _conn() as con:
        if con is None:
            # Fallback only in local dev
            if is_local_env():
                w = _mem_get(user_id)
                w["balance_cents"] = int(w["balance_cents"]) + int(amount_cents)
                return _mem_set(user_id, w["balance_cents"], w.get("currency", currency))
            else:
                raise RuntimeError("Database connection failed in production")

        _ensure_schema(con)
        row = con.execute("SELECT balance_cents, currency FROM wallet WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            con.execute("INSERT INTO wallet (user_id, balance_cents, currency) VALUES (?, ?, ?)",
                        (user_id, int(amount_cents), currency))
            return {"user_id": user_id, "balance_cents": int(amount_cents), "currency": currency}
        new_balance = int(row["balance_cents"]) + int(amount_cents)
        con.execute("UPDATE wallet SET balance_cents = ? WHERE user_id = ?", (new_balance, user_id))
        return {"user_id": user_id, "balance_cents": new_balance, "currency": row["currency"]}

def debit_wallet(user_id: str, amount_cents: int) -> Dict[str, Any]:
    """
    Decrement balance by amount_cents (must be >= 0; not allowing negative balances).
    Returns updated wallet.
    In production, raises exception on DB error (fail-closed).
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0")

    with _conn() as con:
        if con is None:
            # Fallback only in local dev
            if is_local_env():
                w = _mem_get(user_id)
                new_balance = max(0, int(w["balance_cents"]) - int(amount_cents))
                return _mem_set(user_id, new_balance, w.get("currency", "USD"))
            else:
                raise RuntimeError("Database connection failed in production")

        _ensure_schema(con)
        row = con.execute("SELECT balance_cents, currency FROM wallet WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            # Nothing to debit; create empty wallet
            con.execute("INSERT INTO wallet (user_id, balance_cents, currency) VALUES (?, ?, ?)",
                        (user_id, 0, "USD"))
            return {"user_id": user_id, "balance_cents": 0, "currency": "USD"}
        new_balance = int(row["balance_cents"]) - int(amount_cents)
        if new_balance < 0:
            new_balance = 0  # or raise if you prefer strict behavior
        con.execute("UPDATE wallet SET balance_cents = ? WHERE user_id = ?", (new_balance, user_id))
        return {"user_id": user_id, "balance_cents": new_balance, "currency": row["currency"]}
