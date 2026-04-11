from __future__ import annotations

import base64
import json
from math import asin, cos, radians, sin, sqrt
from typing import List, Optional, Tuple

from sqlalchemy import text

from app.db import SessionLocal

ALLOWED_FIELDS = {
    "id",
    "kind",
    "title",
    "name",
    "category",
    "lat",
    "lng",
    "distance_m",
    "starts_at",
    "ends_at",
    "green_window",
    "offer",
    "cta",
}


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    R = 6371000.0
    dlat = radians(b_lat - a_lat)
    dlng = radians(b_lng - a_lng)
    lat1 = radians(a_lat)
    lat2 = radians(b_lat)
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * R * asin(sqrt(h))


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return int(data.get("o", 0))
    except Exception:
        return 0


def _apply_fields(item: dict, fields: Optional[List[str]]) -> dict:
    if not fields:
        fields = [
            "id",
            "kind",
            "title",
            "name",
            "category",
            "lat",
            "lng",
            "distance_m",
            "green_window",
            "offer",
            "cta",
        ]
    filtered = [f for f in fields if f in ALLOWED_FIELDS]
    return {k: item.get(k) for k in filtered}


def _has_table(db, name: str) -> bool:
    try:
        res = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}).first()
        if res:
            return True
    except Exception:
        pass
    return False


def _fetch_events(db, lat: float, lng: float, radius_m: int) -> List[dict]:
    rows = db.execute(
        text(
            """
            SELECT id, title, starts_at, ends_at, lat, lng
            FROM events2
            WHERE ABS(lat-:lat) < 0.1 AND ABS(lng-:lng) < 0.1
        """
        ),
        {"lat": lat, "lng": lng},
    ).mappings()
    items = []
    for r in rows:
        dist = _haversine_m(lat, lng, r["lat"], r["lng"])
        if dist <= radius_m:
            items.append(
                {
                    "id": f"event:{r['id']}",
                    "kind": "event",
                    "title": r.get("title"),
                    "name": r.get("title"),
                    "category": "event",
                    "lat": r.get("lat"),
                    "lng": r.get("lng"),
                    "distance_m": round(dist, 1),
                    "starts_at": r.get("starts_at"),
                    "ends_at": r.get("ends_at"),
                    "green_window": None,
                    "offer": None,
                    "cta": {"join_event_id": str(r["id"]), "verify_url": None},
                }
            )
    return items


def _fetch_merchants(db, lat: float, lng: float, radius_m: int) -> List[dict]:
    rows = db.execute(
        text(
            """
            SELECT id, name, category, lat, lng
            FROM merchants
            WHERE ABS(lat-:lat) < 0.1 AND ABS(lng-:lng) < 0.1
        """
        ),
        {"lat": lat, "lng": lng},
    ).mappings()
    items = []
    for r in rows:
        dist = _haversine_m(lat, lng, r["lat"], r["lng"])
        if dist <= radius_m:
            offer_row = db.execute(
                text(
                    """
                    SELECT title, reward_cents
                    FROM offers
                    WHERE merchant_id = :mid AND active = 1
                    ORDER BY created_at DESC LIMIT 1
                """
                ),
                {"mid": r["id"]},
            ).mappings().first()
            offer = None
            if offer_row:
                offer = {
                    "title": offer_row.get("title"),
                    "est_reward_cents": offer_row.get("reward_cents"),
                }
            items.append(
                {
                    "id": f"merchant:{r['id']}",
                    "kind": "merchant",
                    "title": r.get("name"),
                    "name": r.get("name"),
                    "category": r.get("category"),
                    "lat": r.get("lat"),
                    "lng": r.get("lng"),
                    "distance_m": round(dist, 1),
                    "starts_at": None,
                    "ends_at": None,
                    "green_window": None,
                    "offer": offer,
                    "cta": {"join_event_id": None, "verify_url": None},
                }
            )
    return items


def _fetch_chargers(db, lat: float, lng: float, radius_m: int) -> List[dict]:
    if not _has_table(db, "chargers_openmap"):
        return []
    rows = db.execute(
        text(
            """
            SELECT id, name, lat, lng
            FROM chargers_openmap
            WHERE ABS(lat-:lat) < 0.1 AND ABS(lng-:lng) < 0.1
        """
        ),
        {"lat": lat, "lng": lng},
    ).mappings()
    items = []
    for r in rows:
        dist = _haversine_m(lat, lng, r["lat"], r["lng"])
        if dist <= radius_m:
            items.append(
                {
                    "id": f"charger:{r['id']}",
                    "kind": "ev_charging",
                    "title": r.get("name"),
                    "name": r.get("name"),
                    "category": "ev_charging",
                    "lat": r.get("lat"),
                    "lng": r.get("lng"),
                    "distance_m": round(dist, 1),
                    "starts_at": None,
                    "ends_at": None,
                    "green_window": None,
                    "offer": None,
                    "cta": {"join_event_id": None, "verify_url": None},
                }
            )
    return items


def _fetch_ranked(db, lat: float, lng: float, radius_m: int, categories: List[str]) -> List[dict]:
    items: List[dict] = []
    if "event" in categories:
        items.extend(_fetch_events(db, lat, lng, radius_m))
    if "merchant" in categories:
        items.extend(_fetch_merchants(db, lat, lng, radius_m))
    if "ev_charging" in categories:
        items.extend(_fetch_chargers(db, lat, lng, radius_m))
    items.sort(key=lambda x: x.get("distance_m", 0))
    return items


def search(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    categories: Optional[List[str]],
    limit: int,
    cursor: Optional[str],
    fields: Optional[List[str]],
) -> Tuple[List[dict], Optional[str], Optional[int]]:
    selected_categories = categories or ["ev_charging", "event"]
    db = SessionLocal()
    try:
        results = _fetch_ranked(db, lat, lng, radius_m, selected_categories)
        total = len(results)
        start = _decode_cursor(cursor)
        start = max(0, min(start, total))
        end = min(start + limit, total)
        page = results[start:end]
        page = [_apply_fields(item, fields) for item in page]
        next_cursor = _encode_cursor(end) if end < total else None
        return page, next_cursor, total
    finally:
        db.close()


