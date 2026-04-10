# app/routers/incentives.py
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.nova import cents_to_nova
from app.services.rewards_engine import record_reward_event
from app.services.wallet import credit_wallet  # existing wallet service

router = APIRouter(prefix="/v1/incentives", tags=["incentives"])

# very simple demo policy: 5 minutes ON, 5 minutes OFF, cycling
def _current_window():
    now = datetime.now(timezone.utc)
    minute_mod = now.minute % 10
    active = minute_mod < 5
    if active:
        start = now.replace(second=0, microsecond=0) - timedelta(minutes=minute_mod)
        end   = start + timedelta(minutes=5)
        return {"active": True, "start_iso": start.isoformat(), "end_iso": end.isoformat(),
                "message": "Cheaper charging now"}
    else:
        # next ON starts when minute_mod hits 0 again
        start = now + timedelta(minutes=(10 - minute_mod))
        start = start.replace(second=0, microsecond=0)
        end   = start + timedelta(minutes=5)
        return {"active": False, "start_iso": start.isoformat(), "end_iso": end.isoformat(),
                "message": "Cheaper charging soon"}

@router.get("/window")
def window_status():
    """UI checks this to show 'Cheaper charging now' or '...in X minutes'."""
    return _current_window()

# Simple guard so we don't credit repeatedly within a short window.
_LAST_AWARD: Dict[str, datetime] = {}

@router.post("/award_off_peak")
def award(user_id: str = Query(...), cents: int = 100):
    """
    Credit a small bonus during ON window. Idempotent-ish: one award per user every 30 minutes.
    UI uses this as a fallback if /window isn't present.
    """
    w = _current_window()
    awarded = 0
    now = datetime.now(timezone.utc)
    last: Optional[datetime] = _LAST_AWARD.get(user_id)

    if w["active"] and (not last or (now - last) > timedelta(minutes=30)):
        out = credit_wallet(user_id, cents)
        _LAST_AWARD[user_id] = now
        awarded = cents
        balance = out.get("balance_cents", 0)
    else:
        balance = credit_wallet(user_id, 0).get("balance_cents", 0)  # no-op read

    return {
        "active": w["active"],
        "awarded_cents": awarded,
        "nova_awarded": cents_to_nova(awarded),
        "balance_cents": balance,
        "nova_balance": cents_to_nova(balance),
        "window": w,
    }

@router.post("/award")
def award_with_community(
    user_id: str = Query(...), 
    cents: int = Query(...),
    source: str = Query("CHARGE"),
    db: Session = Depends(get_db)
):
    """
    Award credits with community pool distribution to followers.
    Returns gross, net, and community amounts.
    """
    meta = {}
    
    # Record reward event with community distribution
    ev = record_reward_event(db, user_id=user_id, source=source, gross_cents=cents, meta=meta)
    
    # Credit the net amount to user's wallet
    credit_wallet(user_id, ev.net_cents, "USD")
    
    return {
        "gross_cents": ev.gross_cents,
        "nova_awarded": cents_to_nova(ev.net_cents),
        "net_cents": ev.net_cents, 
        "community_cents": ev.community_cents,
        "user_id": user_id,
        "source": source
    }
