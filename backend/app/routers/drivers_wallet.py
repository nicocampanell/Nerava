"""
Driver wallet endpoints - balance, history, and Nova redemption
Consolidates wallet-related routes for drivers
"""
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies_driver import get_current_driver
from ..models import User
from ..models.extra import CreditLedger, IncentiveRule
from ..services.incentives import calc_award_cents
from ..services.nova import cents_to_nova
from ..services.nova_service import NovaService

router = APIRouter(prefix="/v1/drivers", tags=["drivers-wallet"])


# ---- helpers ----
def _balance(db: Session, user_ref: str) -> int:
    """Get wallet balance, returning 0 if credit_ledger table doesn't exist."""
    try:
        rows = db.query(CreditLedger).filter(CreditLedger.user_ref == user_ref).all()
        return sum(r.cents for r in rows)
    except Exception:
        # Table might not exist yet - return 0 balance
        return 0


def _add_ledger(db: Session, user_ref: str, cents: int, reason: str, meta: Dict[str, Any] = None) -> int:
    row = CreditLedger(user_ref=user_ref, cents=cents, reason=reason, meta=meta or {})
    db.add(row)
    db.commit()
    return _balance(db, user_ref)


# ---- endpoints ----
@router.get("/wallet")
def get_wallet(user: User = Depends(get_current_driver), db: Session = Depends(get_db)):
    """Get wallet balance - returns 0 if credit_ledger table doesn't exist."""
    try:
        balance_cents = _balance(db, str(user.id))
    except Exception as e:
        # Handle gracefully if table doesn't exist
        balance_cents = 0
    
    # Also try to get Nova balance from DriverWallet if available
    nova_balance = 0
    try:
        driver_wallet = NovaService.get_driver_wallet(db, user.id)
        nova_balance = driver_wallet.nova_balance if driver_wallet else 0
    except Exception:
        pass
    
    return {
        "balance_cents": balance_cents,
        "nova_balance": cents_to_nova(balance_cents) + nova_balance
    }


@router.get("/wallet/history")
def wallet_history(
    limit: int = 50,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get wallet history - returns empty list if credit_ledger table doesn't exist."""
    try:
        q = (
            db.query(CreditLedger)
            .filter(CreditLedger.user_ref == str(user.id))
            .order_by(CreditLedger.id.desc())
            .limit(limit)
        )
        return [
            {
                "cents": r.cents,
                "nova_delta": cents_to_nova(r.cents),
                "reason": r.reason,
                "meta": r.meta,
                "ts": r.created_at.isoformat()
            }
            for r in q.all()
        ]
    except Exception:
        # Table might not exist yet - return empty history
        return []


@router.get("/wallet/summary")
def wallet_summary(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get wallet summary with balance and recent history"""
    try:
        balance = _balance(db, str(user.id))
        
        # Get recent history
        q = (
            db.query(CreditLedger)
            .filter(CreditLedger.user_ref == str(user.id))
            .order_by(CreditLedger.id.desc())
            .limit(10)
        )
        history = [
            {
                "cents": r.cents,
                "reason": r.reason,
                "meta": r.meta,
                "ts": r.created_at.isoformat()
            }
            for r in q.all()
        ]
    except Exception:
        # Table might not exist yet - return empty wallet
        balance = 0
        history = []
    
    # Also get Nova balance
    nova_balance = 0
    try:
        driver_wallet = NovaService.get_driver_wallet(db, user.id)
        nova_balance = driver_wallet.nova_balance if driver_wallet else 0
    except Exception:
        pass
    
    return {
        "balance_cents": balance,
        "nova_balance": cents_to_nova(balance) + nova_balance,
        "balance_dollars": round(balance / 100, 2),
        "history": [
            {
                **entry,
                "nova_delta": cents_to_nova(entry["cents"])
            }
            for entry in history
        ]
    }


class RedeemReq(BaseModel):
    cents: int
    perk: str


@router.post("/wallet/redeem")
def wallet_redeem(req: RedeemReq, user: User = Depends(get_current_driver), db: Session = Depends(get_db)):
    user_id = user.id
    cents = int(req.cents)
    perk = req.perk
    if cents <= 0:
        raise HTTPException(status_code=400, detail="invalid_request")
    bal = _balance(db, str(user_id))
    if bal < cents:
        raise HTTPException(status_code=400, detail="insufficient_funds")
    new_bal = _add_ledger(db, str(user_id), -cents, "REDEEM", {"perk": perk})
    return {
        "new_balance_cents": new_bal,
        "nova_balance": cents_to_nova(new_bal),
        "redeemed": cents,
        "redeemed_nova": cents_to_nova(cents),
        "perk": perk
    }


@router.post("/incentives/award_off_peak")
def award_off_peak(user: User = Depends(get_current_driver), db: Session = Depends(get_db)):
    # ensure default rule exists
    rule = db.query(IncentiveRule).filter(IncentiveRule.code == "OFF_PEAK_BASE").first()
    if not rule:
        rule = IncentiveRule(code="OFF_PEAK_BASE", active=True, params={"cents": 25, "window": ["22:00", "06:00"]})
        db.add(rule)
        db.commit()

    rules = db.query(IncentiveRule).all()
    rules_dicts = [{"code": r.code, "active": r.active, "params": r.params or {}} for r in rules]
    amt = calc_award_cents(datetime.utcnow(), rules_dicts)
    if amt > 0:
        new_bal = _add_ledger(db, str(user.id), amt, "OFF_PEAK_AWARD", {"rule": "OFF_PEAK_BASE"})
        return {
            "awarded_cents": amt,
            "nova_awarded": cents_to_nova(amt),
            "new_balance_cents": new_bal,
            "nova_balance": cents_to_nova(new_bal)
        }
    return {
        "awarded_cents": 0,
        "nova_awarded": 0,
        "message": "Not in off-peak window"
    }


