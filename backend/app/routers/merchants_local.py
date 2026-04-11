import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models_extra import CreditLedger

router = APIRouter()  # prefix added in main.py

class MerchantIn(BaseModel):
    name: str
    lat: float
    lng: float
    category: str = "other"
    logo_url: str = ""

class PerkIn(BaseModel):
    merchant_id: int
    title: str = Field(..., min_length=2)
    description: str = ""
    reward_cents: int = Field(ge=0, default=0)

class ClaimIn(BaseModel):
    perk_id: int
    user_id: str

DB_PATH = os.getenv("NERAVA_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "..", "nerava.db"))
DB_PATH = os.path.abspath(DB_PATH)

@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    try:
        yield con
        con.commit()
    finally:
        con.close()

def _ensure(con: sqlite3.Connection):
    con.execute("""CREATE TABLE IF NOT EXISTS merchants_local(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
      category TEXT DEFAULT 'other', logo_url TEXT DEFAULT '',
      created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS merchant_perks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      merchant_id INTEGER NOT NULL,
      title TEXT NOT NULL, description TEXT DEFAULT '',
      reward_cents INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      FOREIGN KEY(merchant_id) REFERENCES merchants_local(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS perk_claims(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      perk_id INTEGER NOT NULL, user_id TEXT NOT NULL,
      status TEXT DEFAULT 'claimed',
      claimed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      FOREIGN KEY(perk_id) REFERENCES merchant_perks(id)
    )""")
    # enforce idempotency going forward
    con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_perk_claim ON perk_claims(perk_id, user_id)""")

def create_merchant(name: str, lat: float, lng: float, category: str = "other", logo_url: str = "") -> Dict[str, Any]:
    with _conn() as con:
        _ensure(con)
        con.execute("INSERT INTO merchants_local(name,lat,lng,category,logo_url) VALUES(?,?,?,?,?)",
                    (name, lat, lng, category, logo_url))
        row = con.execute("SELECT * FROM merchants_local ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row)

def list_merchants_near(lat: float, lng: float, radius_m: float = 800) -> List[Dict[str, Any]]:
    with _conn() as con:
        _ensure(con)
        deg = radius_m / 111320.0
        rows = con.execute("""SELECT * FROM merchants_local
                              WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?
                              ORDER BY id DESC LIMIT 100""",
                           (lat - deg, lat + deg, lng - deg, lng + deg)).fetchall()
        return [dict(r) for r in rows]

def add_perk(merchant_id: int, title: str, description: str = "", reward_cents: int = 0) -> Dict[str, Any]:
    with _conn() as con:
        _ensure(con)
        con.execute("INSERT INTO merchant_perks(merchant_id,title,description,reward_cents) VALUES(?,?,?,?)",
                    (merchant_id, title, description, reward_cents))
        row = con.execute("SELECT * FROM merchant_perks ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row)

def list_perks(merchant_id: int) -> List[Dict[str, Any]]:
    with _conn() as con:
        _ensure(con)
        rows = con.execute("SELECT * FROM merchant_perks WHERE merchant_id=? AND active=1 ORDER BY id DESC",
                           (merchant_id,)).fetchall()
        return [dict(r) for r in rows]

def get_perk_reward(perk_id: int) -> int:
    with _conn() as con:
        _ensure(con)
        row = con.execute("SELECT reward_cents FROM merchant_perks WHERE id=?", (perk_id,)).fetchone()
        return int(row["reward_cents"]) if row else 0

def insert_perk_claim(perk_id: int, user_id: str) -> Dict[str, Any]:
    """
    Fully idempotent:
    - pre-check for existing
    - attempt insert
    - on UNIQUE violation, treat as already-claimed and return existing
    """
    with _conn() as con:
        _ensure(con)

        existing = con.execute(
            "SELECT * FROM perk_claims WHERE perk_id=? AND user_id=? ORDER BY id ASC LIMIT 1",
            (perk_id, user_id),
        ).fetchone()
        if existing:
            out = dict(existing); out["newly_claimed"] = False
            return out

        try:
            con.execute("INSERT INTO perk_claims(perk_id,user_id) VALUES(?,?)", (perk_id, user_id))
        except sqlite3.IntegrityError:
            row = con.execute(
                "SELECT * FROM perk_claims WHERE perk_id=? AND user_id=? ORDER BY id ASC LIMIT 1",
                (perk_id, user_id),
            ).fetchone()
            out = dict(row) if row else {"perk_id": perk_id, "user_id": user_id, "status": "claimed"}
            out["newly_claimed"] = False
            return out

        row = con.execute(
            "SELECT * FROM perk_claims WHERE perk_id=? AND user_id=? ORDER BY id ASC LIMIT 1",
            (perk_id, user_id),
        ).fetchone()
        out = dict(row); out["newly_claimed"] = True
        return out

def _credit_wallet(db: Session, user_ref: str, cents: int, reason: str, meta: Dict[str, Any]) -> int:
    entry = CreditLedger(user_ref=user_ref, cents=cents, reason=reason, meta=meta or {}, created_at=datetime.utcnow())
    db.add(entry); db.commit()
    total = db.query(CreditLedger).with_entities(CreditLedger.cents).filter(CreditLedger.user_ref == user_ref).all()
    return sum(v[0] for v in total)

@router.post("/merchant")
def register_merchant(m: MerchantIn):
    try:
        return create_merchant(m.name, m.lat, m.lng, m.category, m.logo_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"merchant_create_failed: {e}")

@router.get("/merchants_near")
def merchants_near(lat: float = Query(...), lng: float = Query(...), radius_m: float = Query(800)):
    try:
        return list_merchants_near(lat, lng, radius_m)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"merchants_near_failed: {e}")

@router.post("/perk")
def create_perk(p: PerkIn):
    try:
        return add_perk(p.merchant_id, p.title, p.description, p.reward_cents)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"perk_create_failed: {e}")

@router.get("/perks")
def perks(merchant_id: int):
    try:
        return list_perks(merchant_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"perks_list_failed: {e}")

@router.post("/perk/claim")
def claim(p: ClaimIn, db: Session = Depends(get_db)):
    try:
        record = insert_perk_claim(p.perk_id, p.user_id)
        reward = get_perk_reward(p.perk_id)
        if reward > 0 and record.get("newly_claimed", False):
            new_balance = _credit_wallet(db, user_ref=p.user_id, cents=reward, reason="PERK_REWARD", meta={"perk_id": p.perk_id})
            record["wallet_balance_cents"] = new_balance
        else:
            record["wallet_balance_cents"] = None
        record["idempotent"] = not record["newly_claimed"]
        return record
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"claim_failed: {e}")

def list_claims_by_merchant(merchant_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as con:
        _ensure(con)
        rows = con.execute(
            """
            SELECT
              pc.id,
              pc.perk_id,
              mp.title AS perk_title,
              pc.user_id,
              pc.status,
              pc.claimed_at
            FROM perk_claims pc
            JOIN merchant_perks mp ON mp.id = pc.perk_id
            WHERE mp.merchant_id = ?
            ORDER BY pc.claimed_at DESC, pc.id DESC
            LIMIT ?
            """,
            (merchant_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

def summary_by_merchant(merchant_id: int) -> Dict[str, Any]:
    with _conn() as con:
        _ensure(con)
        # per-perk rollup
        perk_rows = con.execute(
            """
            SELECT
              mp.id         AS perk_id,
              mp.title      AS perk_title,
              COUNT(pc.id)  AS claim_count,
              COUNT(DISTINCT pc.user_id) AS unique_users,
              MAX(pc.claimed_at) AS last_claim_at
            FROM merchant_perks mp
            LEFT JOIN perk_claims pc ON pc.perk_id = mp.id
            WHERE mp.merchant_id = ?
            GROUP BY mp.id, mp.title
            ORDER BY claim_count DESC, perk_id ASC
            """,
            (merchant_id,),
        ).fetchall()
        perks = [dict(r) for r in perk_rows]

        # overall totals
        tot = con.execute(
            """
            SELECT
              COUNT(*) AS total_claims,
              COUNT(DISTINCT user_id) AS total_unique_users
            FROM perk_claims
            WHERE perk_id IN (SELECT id FROM merchant_perks WHERE merchant_id = ?)
            """,
            (merchant_id,),
        ).fetchone()
        return {
            "merchant_id": merchant_id,
            "totals": {
                "claims": int(tot["total_claims"]) if tot and tot["total_claims"] is not None else 0,
                "unique_users": int(tot["total_unique_users"]) if tot and tot["total_unique_users"] is not None else 0,
            },
            "perks": perks,
        }

@router.get("/merchant/{merchant_id}/claims")
def merchant_claims(merchant_id: int, limit: int = 50):
    try:
        return list_claims_by_merchant(merchant_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"merchant_claims_failed: {e}")

@router.get("/merchant/{merchant_id}/summary")
def merchant_summary(merchant_id: int):
    try:
        return summary_by_merchant(merchant_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"merchant_summary_failed: {e}")
