from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.db import SessionLocal


def contribute(city: str, amount_cents: int, source: str, related_event_id: Optional[int] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO pool_ledger2 (city, source, amount_cents, related_event_id) VALUES (:city, :src, :amt, :eid)"
            ),
            {"city": city, "src": source, "amt": amount_cents, "eid": related_event_id},
        )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


def payout_to_user(user_id: int, amount_cents: int, city: str, related_event_id: Optional[int] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO pool_ledger2 (city, source, amount_cents, related_event_id) VALUES (:city, 'verified_sessions', :amt, :eid)"
            ),
            {"city": city, "amt": -abs(amount_cents), "eid": related_event_id},
        )
        tomorrow_9 = datetime.utcnow().date() + timedelta(days=1)
        available_at = datetime.combine(tomorrow_9, time(9, 0))
        db.execute(
            text(
                """
            INSERT INTO rewards2 (user_id, type, amount_cents, earn_date, available_at, ref_txn_id, ref_event_id)
            VALUES (:uid, 'pool_award', :amt, :ed, :avail, NULL, :eid)
            """
            ),
            {"uid": user_id, "amt": amount_cents, "ed": datetime.utcnow().date(), "avail": available_at, "eid": related_event_id},
        )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


def summary(city: str, days: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = db.execute(
            text(
                "SELECT SUM(amount_cents) FROM pool_ledger2 WHERE city=:city AND created_at >= :since"
            ),
            {"city": city, "since": since},
        ).first()
        balance = int(rows[0] or 0)
        return {"city": city, "balance_cents": balance, "range_days": days}
    finally:
        db.close()


def run_daily_close(date_yyyymmdd: Optional[str] = None) -> Dict[str, Any]:
    # Minimal placeholder: no complex allocation logic, just report success
    return {"status": "ok", "date": date_yyyymmdd or datetime.utcnow().strftime("%Y-%m-%d")}


