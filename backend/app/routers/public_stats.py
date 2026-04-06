"""Public stats endpoint for social proof (no auth required)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app.models import User
from app.models.driver_wallet import DriverWallet

router = APIRouter(prefix="/v1/stats", tags=["stats"])


class PublicStatsResponse(BaseModel):
    total_drivers: int
    total_earned_cents: int
    total_sessions: int


@router.get("/public", response_model=PublicStatsResponse)
def get_public_stats(db: Session = Depends(get_db)):
    """Public network stats for social proof. No auth required."""
    total_drivers = db.query(func.count(User.id)).scalar() or 0

    total_earned = db.query(func.coalesce(func.sum(DriverWallet.total_earned_cents), 0)).scalar() or 0

    # Count completed sessions
    try:
        from app.models.session_event import SessionEvent
        total_sessions = db.query(func.count(SessionEvent.id)).filter(
            SessionEvent.session_end.isnot(None)
        ).scalar() or 0
    except Exception:
        total_sessions = 0

    return PublicStatsResponse(
        total_drivers=total_drivers,
        total_earned_cents=total_earned,
        total_sessions=total_sessions,
    )
