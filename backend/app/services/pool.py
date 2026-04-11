"""Community pool ledger service."""

from typing import Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.events import get_event_by_id
from app.utils.log import get_logger

logger = get_logger("pool")


def contribute(
    db: Session,
    amount_cents: int,
    source: str,
    user_id: Optional[int] = None,
    city: Optional[str] = None,
    related_event_id: Optional[int] = None
) -> int:
    """
    Record a contribution to the pool (positive amount).
    
    Returns:
        ledger_id
    """
    result = db.execute(text("""
        INSERT INTO pool_ledger (
            user_id, source, amount_cents, city, related_event_id, created_at
        ) VALUES (
            :user_id, :source, :amount_cents, :city, :related_event_id, CURRENT_TIMESTAMP
        )
    """), {
        "user_id": user_id,
        "source": source,
        "amount_cents": amount_cents,
        "city": city,
        "related_event_id": related_event_id
    })
    
    db.commit()
    ledger_id = result.lastrowid
    
    logger.info("pool_contributed", extra={
        "ledger_id": ledger_id,
        "amount_cents": amount_cents,
        "source": source,
        "city": city
    })
    
    return ledger_id


def payout_to_user(
    db: Session,
    user_id: int,
    amount_cents: int,
    city: Optional[str] = None,
    related_event_id: Optional[int] = None
) -> int:
    """
    Record a payout from pool (negative amount).
    
    Returns:
        ledger_id
    """
    result = db.execute(text("""
        INSERT INTO pool_ledger (
            user_id, source, amount_cents, city, related_event_id, created_at
        ) VALUES (
            :user_id, 'verified_sessions', :amount_cents, :city, :related_event_id, CURRENT_TIMESTAMP
        )
    """), {
        "user_id": user_id,
        "amount_cents": -amount_cents,  # Negative for outflow
        "city": city,
        "related_event_id": related_event_id
    })
    
    db.commit()
    ledger_id = result.lastrowid
    
    logger.info("pool_payout", extra={
        "ledger_id": ledger_id,
        "user_id": user_id,
        "amount_cents": amount_cents,
        "city": city
    })
    
    return ledger_id


def split_and_credit_verified_attendee(db: Session, event_id: int, user_id: int) -> Dict:
    """
    Award pool reward to verified attendee and credit wallet.
    
    Args:
        db: Database session
        event_id: Event ID
        user_id: User being rewarded
        
    Returns:
        {reward_cents, pool_contribution_cents}
    """
    event = get_event_by_id(db, event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")
    
    price_cents = event.get("price_cents", 0)
    
    # Compute attendee reward from pool
    # min(int(event.price_cents * 0.5), pool_reward_cap_cents)
    if price_cents > 0:
        reward_cents = min(int(price_cents * 0.5), settings.pool_reward_cap_cents)
    else:
        # Free event: still reward a small flat amount
        reward_cents = min(100, settings.pool_reward_cap_cents)
    
    # Record pool outflow
    pool_ledger_id = payout_to_user(
        db,
        user_id=user_id,
        amount_cents=reward_cents,
        city=event.get("city"),
        related_event_id=event_id
    )
    
    # Credit wallet
    # Get verification_id for ref_id
    verification_result = db.execute(text("""
        SELECT id FROM verifications
        WHERE user_id = :user_id AND event_id = :event_id
        ORDER BY started_at DESC
        LIMIT 1
    """), {"user_id": user_id, "event_id": event_id})
    
    verification_row = verification_result.first()
    ref_id = dict(verification_row._mapping)["id"] if verification_row else event_id
    
    # Get current wallet balance
    balance_result = db.execute(text("""
        SELECT COALESCE(SUM(amount_cents), 0) as balance FROM wallet_ledger WHERE user_id = :user_id
    """), {"user_id": user_id})
    row = balance_result.first()
    current_balance = row[0] if row else 0
    new_balance = current_balance + reward_cents
    
    db.execute(text("""
        INSERT INTO wallet_ledger (
            user_id, amount_cents, transaction_type, reference_id, reference_type, balance_cents, created_at
        ) VALUES (
            :user_id, :amount_cents, 'event_verified', :ref_id, 'event', :balance_cents, CURRENT_TIMESTAMP
        )
    """), {
        "user_id": user_id,
        "amount_cents": reward_cents,
        "ref_id": ref_id,
        "balance_cents": new_balance
    })
    
    db.commit()
    
    logger.info("verified_attendee_rewarded", extra={
        "user_id": user_id,
        "event_id": event_id,
        "reward_cents": reward_cents,
        "pool_ledger_id": pool_ledger_id
    })
    
    return {
        "reward_cents": reward_cents,
        "pool_contribution_cents": reward_cents  # Same as reward
    }

