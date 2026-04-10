import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List

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

def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS merchants_local(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      lat REAL NOT NULL,
      lng REAL NOT NULL,
      category TEXT DEFAULT 'other',
      logo_url TEXT DEFAULT '',
      created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS merchant_perks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      merchant_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT DEFAULT '',
      reward_cents INTEGER DEFAULT 0,
      active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      FOREIGN KEY(merchant_id) REFERENCES merchants_local(id)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS perk_claims(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      perk_id INTEGER NOT NULL,
      user_id TEXT NOT NULL,
      status TEXT DEFAULT 'claimed',
      claimed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      FOREIGN KEY(perk_id) REFERENCES merchant_perks(id)
    )""")

def create_merchant(name:str, lat:float, lng:float, category:str='other', logo_url:str='')->Dict[str,Any]:
    with _conn() as con:
        _ensure(con)
        con.execute("INSERT INTO merchants_local(name,lat,lng,category,logo_url) VALUES(?,?,?,?,?)",
                    (name,lat,lng,category,logo_url))
        row = con.execute("SELECT * FROM merchants_local ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row)

def list_merchants_near(lat:float, lng:float, radius_m:float=800)->List[Dict[str,Any]]:
    with _conn() as con:
        _ensure(con)
        # naive distance filter (square); good enough for demo
        deg = radius_m / 111320.0
        rows = con.execute("""SELECT * FROM merchants_local
                              WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?
                              ORDER BY id DESC LIMIT 100""",
                              (lat-deg, lat+deg, lng-deg, lng+deg)).fetchall()
        return [dict(r) for r in rows]

def add_perk(merchant_id:int, title:str, description:str='', reward_cents:int=0)->Dict[str,Any]:
    with _conn() as con:
        _ensure(con)
        con.execute("INSERT INTO merchant_perks(merchant_id,title,description,reward_cents) VALUES(?,?,?,?)",
                    (merchant_id,title,description,reward_cents))
        row = con.execute("SELECT * FROM merchant_perks ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row)

def list_perks(merchant_id:int)->List[Dict[str,Any]]:
    with _conn() as con:
        _ensure(con)
        rows = con.execute("SELECT * FROM merchant_perks WHERE merchant_id=? AND active=1 ORDER BY id DESC",
                           (merchant_id,)).fetchall()
        return [dict(r) for r in rows]

def claim_perk(perk_id:int, user_id:str)->Dict[str,Any]:
    with _conn() as con:
        _ensure(con)
        con.execute("INSERT INTO perk_claims(perk_id,user_id) VALUES(?,?)", (perk_id,user_id))
        row = con.execute("SELECT * FROM perk_claims ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row)
