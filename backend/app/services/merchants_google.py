import os
import sqlite3
import urllib.parse
from contextlib import contextmanager
from typing import Any, Dict, List

import requests

# Hardcoded API key (no longer reads from environment variables)
API_KEY = "AIzaSyAs0PVYXj3-ztRXCjdd0ztUGUSjQR73FFg"

# ---------- Local SQLite (sidecar used by merchants_local) ----------
DB_PATH = os.getenv(
    "NERAVA_DB_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "nerava.db"))
)

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

def _badge(types):
    for t in types or []:
        if t in TYPE_MAP:
            return TYPE_MAP[t].split("_")[0].capitalize()
    return "Other"

def _category_list(types):
    cats = []
    for t in types or []:
        if t in TYPE_MAP and TYPE_MAP[t] not in cats:
            cats.append(TYPE_MAP[t])
    return cats or ["other"]

# map Google primary/secondary types → our categories
TYPE_MAP = {
    "restaurant": "dining_sitdown",
    "meal_takeaway": "quick_bite",
    "cafe": "coffee_bakery",
    "bar": "dining_sitdown",
    "shopping_mall": "shopping_retail",
    "clothing_store": "shopping_retail",
    "department_store": "shopping_retail",
    "supermarket": "shopping_retail",
}

def _dd_link(name: str, lat: float, lng: float, hub_id: str):
    q = urllib.parse.quote(name)
    return f"https://www.doordash.com/search/store/{q}/?lat={lat}&lng={lng}&utm_source=nerava&utm_medium=app&utm_campaign={hub_id}"

def _ot_link(name: str, lat: float, lng: float, hub_id: str):
    q = urllib.parse.quote(name)
    return f"https://www.opentable.com/s?covers=2&currentlocationid=0&latitude={lat}&longitude={lng}&term={q}&utm_source=nerava&utm_medium=app&utm_campaign={hub_id}"

# ---------- Local offers near me ----------
def _local_offers_near(*, lat: float, lng: float, radius_m: int, prefs: List[str]) -> List[Dict[str, Any]]:
    """
    Returns local merchants with an active perk, shaped like Google items + 'perk' and 'source'.
    """
    with _conn() as con:
        _ensure(con)
        deg = radius_m / 111320.0  # rough bbox
        rows = con.execute(
            """SELECT ml.id as merchant_id, ml.name, ml.lat, ml.lng, ml.category, ml.logo_url,
                      mp.id as perk_id, mp.title as perk_title, mp.reward_cents
               FROM merchants_local ml
               LEFT JOIN merchant_perks mp ON mp.merchant_id = ml.id AND mp.active=1
               WHERE ml.lat BETWEEN ? AND ? AND ml.lng BETWEEN ? AND ?
               ORDER BY mp.id DESC, ml.id DESC
               LIMIT 100""",
            (lat - deg, lat + deg, lng - deg, lng + deg),
        ).fetchall()

    out = []
    for r in rows:
        # Only include if a perk exists; otherwise skip (you can relax if you want all locals)
        if r["perk_id"] is None:
            continue
        cat = r["category"] or "other"
        if prefs and (cat not in prefs):
            # keep simple: only filter out if prefs specified and cat doesn't match
            continue
        out.append({
            "source": "local",
            "name": r["name"],
            "badge": "Perk",
            "categories": [cat],
            "logo": r["logo_url"] or "https://maps.gstatic.com/mapfiles/place_api/icons/v2/generic_pinlet",
            "distance_hint": "walkable",
            "perk": {
                "id": r["perk_id"],
                "title": r["perk_title"],
                "reward_cents": int(r["reward_cents"] or 0),
                "merchant_id": int(r["merchant_id"]),
            },
            # optional links your UI can use
            "links": {
                "claim_api": "/v1/local/perk/claim",  # POST with {perk_id, user_id}
            },
        })
    return out

# ---------- Google Places nearby ----------
def _google_nearby(*, lat: float, lng: float, radius_m: int, prefs: List[str], limit: int, hub_id: str) -> List[Dict[str, Any]]:
    if not API_KEY:
        return []
    url = "https://places.googleapis.com/v1/places:searchNearby"
    payload = {
        "includedTypes": [
            "restaurant","cafe","meal_takeaway","shopping_mall","clothing_store",
            "department_store","supermarket","bar","tourist_attraction","movie_theater","book_store","gym"
        ],
        "maxResultCount": min(20, max(1, limit)),
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius_m}
        }
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.types,places.iconMaskBaseUri"
    }
    r = requests.post(url, json=payload, headers=headers, timeout=12)
    r.raise_for_status()
    places = (r.json() or {}).get("places", []) or []

    out = []
    set_prefs = set(prefs or [])
    for p in places:
        name = p.get("displayName", {}).get("text")
        types = p.get("types", [])
        cats = _category_list(types)
        if set_prefs and not any(pref in cats for pref in set_prefs):
            # let a few through if needed; for now keep strict for cleaner demo
            pass
        badge = _badge(types)
        logo = (p.get("iconMaskBaseUri") or "").replace("pinlet_v2", "pinlet")
        out.append({
            "source": "google",
            "name": name,
            "badge": badge,
            "categories": cats,
            "logo": logo if logo else "https://maps.gstatic.com/mapfiles/place_api/icons/v2/generic_pinlet",
            "distance_hint": "walkable",
            "links": {
                "pickup": _dd_link(name, lat, lng, hub_id),
                "reserve": _ot_link(name, lat, lng, hub_id),
            }
        })
        if len(out) >= limit:
            break
    return out

# ---------- Public function used by router ----------
def search_nearby(*, lat: float, lng: float, radius_m: int = 600, prefs=None, limit: int = 12, hub_id: str = "hub_unknown"):
    """
    Returns a unified list: local merchants with perks (first), then Google places (with DD/OT links).
    """
    prefs = [p.strip() for p in (prefs or []) if p and p.strip()]
    local_items = _local_offers_near(lat=lat, lng=lng, radius_m=radius_m, prefs=prefs)

    remaining = max(0, limit - len(local_items))
    google_items = _google_nearby(lat=lat, lng=lng, radius_m=radius_m, prefs=prefs, limit=remaining or limit, hub_id=hub_id)

    # Put local offers first so your demo immediately shows perks, then fill with Google
    unified = local_items + google_items

    # Enforce final limit
    return unified[:limit]
