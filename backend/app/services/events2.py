from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from app.db import SessionLocal


def event_insights(event_id: Optional[int], from_ts: Optional[datetime], to_ts: Optional[datetime]) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        # Minimal placeholder: counts of attendees and verifications
        if not event_id:
            return {"attendees": 0, "verified": 0}
        attendees = db.execute(
            text("SELECT COUNT(1) FROM event_attendance2 WHERE event_id=:eid"), {"eid": event_id}
        ).scalar() or 0
        verified = db.execute(
            text(
                "SELECT COUNT(1) FROM event_attendance2 WHERE event_id=:eid AND state='verified'"
            ),
            {"eid": event_id},
        ).scalar() or 0
        return {"attendees": attendees, "verified": verified}
    finally:
        db.close()


