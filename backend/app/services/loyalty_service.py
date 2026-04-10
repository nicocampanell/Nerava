"""
Loyalty Service — punch card programs for merchants.

Handles card CRUD, visit tracking, auto-unlock, and merchant analytics.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.loyalty import LoyaltyCard, LoyaltyProgress

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Card CRUD
# ---------------------------------------------------------------------------

def create_loyalty_card(
    db: Session,
    merchant_id: str,
    program_name: str,
    visits_required: int,
    reward_cents: int = 0,
    reward_description: Optional[str] = None,
    place_id: Optional[str] = None,
) -> LoyaltyCard:
    card = LoyaltyCard(
        id=str(uuid.uuid4()),
        merchant_id=merchant_id,
        place_id=place_id,
        program_name=program_name,
        visits_required=visits_required,
        reward_cents=reward_cents,
        reward_description=reward_description,
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


def update_loyalty_card(
    db: Session,
    card_id: str,
    merchant_id: str,
    **updates,
) -> Optional[LoyaltyCard]:
    card = db.query(LoyaltyCard).filter(
        LoyaltyCard.id == card_id,
        LoyaltyCard.merchant_id == merchant_id,
    ).first()
    if not card:
        return None

    allowed = {"program_name", "visits_required", "reward_cents", "reward_description", "is_active"}
    for key, value in updates.items():
        if key in allowed and value is not None:
            setattr(card, key, value)
    card.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(card)
    return card


def get_loyalty_cards(db: Session, merchant_id: str) -> List[LoyaltyCard]:
    return db.query(LoyaltyCard).filter(
        LoyaltyCard.merchant_id == merchant_id,
    ).order_by(LoyaltyCard.created_at.desc()).all()


# ---------------------------------------------------------------------------
# Driver progress
# ---------------------------------------------------------------------------

def get_driver_progress(
    db: Session,
    driver_user_id: int,
    merchant_id: str,
) -> List[Dict[str, Any]]:
    """Return punch card state for a driver at a specific merchant."""
    cards = db.query(LoyaltyCard).filter(
        LoyaltyCard.merchant_id == merchant_id,
        LoyaltyCard.is_active == True,
    ).all()

    results = []
    for card in cards:
        progress = db.query(LoyaltyProgress).filter(
            LoyaltyProgress.driver_user_id == driver_user_id,
            LoyaltyProgress.loyalty_card_id == card.id,
        ).first()

        results.append({
            "card_id": card.id,
            "program_name": card.program_name,
            "visits_required": card.visits_required,
            "reward_cents": card.reward_cents,
            "reward_description": card.reward_description,
            "visit_count": progress.visit_count if progress else 0,
            "reward_unlocked": progress.reward_unlocked if progress else False,
            "reward_claimed": progress.reward_claimed if progress else False,
            "last_visit_at": progress.last_visit_at.isoformat() if progress and progress.last_visit_at else None,
        })
    return results


def increment_visit(
    db: Session,
    driver_user_id: int,
    merchant_id: str,
) -> List[LoyaltyProgress]:
    """Increment visit count on all active cards for this merchant. Auto-unlocks milestones."""
    cards = db.query(LoyaltyCard).filter(
        LoyaltyCard.merchant_id == merchant_id,
        LoyaltyCard.is_active == True,
    ).all()

    updated = []
    for card in cards:
        progress = db.query(LoyaltyProgress).filter(
            LoyaltyProgress.driver_user_id == driver_user_id,
            LoyaltyProgress.loyalty_card_id == card.id,
        ).first()

        if not progress:
            progress = LoyaltyProgress(
                id=str(uuid.uuid4()),
                driver_user_id=driver_user_id,
                loyalty_card_id=card.id,
                merchant_id=merchant_id,
                visit_count=0,
                reward_unlocked=False,
                reward_claimed=False,
            )
            db.add(progress)

        # Don't increment if reward already claimed (card complete)
        if progress.reward_claimed:
            continue

        progress.visit_count += 1
        progress.last_visit_at = datetime.utcnow()

        # Auto-unlock when milestone reached
        if not progress.reward_unlocked and progress.visit_count >= card.visits_required:
            progress.reward_unlocked = True
            progress.reward_unlocked_at = datetime.utcnow()
            logger.info(
                "Loyalty reward unlocked: driver=%s card=%s visits=%d/%d",
                driver_user_id, card.id, progress.visit_count, card.visits_required,
            )

        updated.append(progress)

    db.commit()
    return updated


def claim_reward(
    db: Session,
    driver_user_id: int,
    card_id: str,
) -> Optional[LoyaltyProgress]:
    """Claim an unlocked reward. Returns progress or None if not eligible."""
    progress = db.query(LoyaltyProgress).filter(
        LoyaltyProgress.driver_user_id == driver_user_id,
        LoyaltyProgress.loyalty_card_id == card_id,
    ).first()

    if not progress or not progress.reward_unlocked or progress.reward_claimed:
        return None

    progress.reward_claimed = True
    progress.reward_claimed_at = datetime.utcnow()
    db.commit()
    db.refresh(progress)
    return progress


# ---------------------------------------------------------------------------
# Merchant analytics
# ---------------------------------------------------------------------------

def get_merchant_loyalty_stats(db: Session, merchant_id: str) -> Dict[str, int]:
    """Aggregate loyalty stats for a merchant."""
    enrolled = db.query(func.count(func.distinct(LoyaltyProgress.driver_user_id))).filter(
        LoyaltyProgress.merchant_id == merchant_id,
    ).scalar() or 0

    total_visits = db.query(func.sum(LoyaltyProgress.visit_count)).filter(
        LoyaltyProgress.merchant_id == merchant_id,
    ).scalar() or 0

    rewards_unlocked = db.query(func.count(LoyaltyProgress.id)).filter(
        LoyaltyProgress.merchant_id == merchant_id,
        LoyaltyProgress.reward_unlocked == True,
    ).scalar() or 0

    rewards_claimed = db.query(func.count(LoyaltyProgress.id)).filter(
        LoyaltyProgress.merchant_id == merchant_id,
        LoyaltyProgress.reward_claimed == True,
    ).scalar() or 0

    return {
        "enrolled_drivers": enrolled,
        "total_visits": total_visits,
        "rewards_unlocked": rewards_unlocked,
        "rewards_claimed": rewards_claimed,
    }


def get_merchant_loyalty_customers(
    db: Session,
    merchant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Paginated customer table for merchant portal (anonymized)."""
    query = (
        db.query(LoyaltyProgress)
        .join(LoyaltyCard, LoyaltyProgress.loyalty_card_id == LoyaltyCard.id)
        .filter(LoyaltyProgress.merchant_id == merchant_id)
        .order_by(LoyaltyProgress.last_visit_at.desc().nullslast())
    )

    total = query.count()
    rows = query.offset(offset).limit(limit).all()

    customers = []
    for p in rows:
        card = p.loyalty_card
        customers.append({
            "driver_id_anonymized": f"Driver-{str(p.driver_user_id)[-4:].zfill(4)}",
            "card_name": card.program_name if card else "Unknown",
            "visit_count": p.visit_count,
            "visits_required": card.visits_required if card else 0,
            "last_visit_at": p.last_visit_at.isoformat() if p.last_visit_at else None,
            "reward_unlocked": p.reward_unlocked,
            "reward_claimed": p.reward_claimed,
        })

    return {"customers": customers, "total": total, "limit": limit, "offset": offset}
