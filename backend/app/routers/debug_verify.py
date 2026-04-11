from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/verify_session")
def debug_verify_session(session_id: str = Query(...)):
    if not settings.debug_verbose:
        return {"ok": False, "message": "disabled"}
    db: Session = SessionLocal()
    try:
        row = db.execute(text("SELECT id as session_id, status, target_type, target_id, radius_m, started_lat, started_lng, last_lat, last_lng, last_accuracy_m, dwell_seconds, started_at, verified_at FROM sessions WHERE id=:sid"), {"sid": session_id}).mappings().first()
        return {"ok": True, **(dict(row) if row else {})}
    finally:
        db.close()


