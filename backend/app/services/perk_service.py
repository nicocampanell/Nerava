"""
Perk Service
Handles perk unlock logic
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.copy import PERK_UNLOCK_COPY
from app.models import IntentSession, MerchantPerk, PerkUnlock
from app.services.wallet_pass_state import (
    transition_to_charging_moment,
    transition_to_perk_unlocked,
)

logger = logging.getLogger(__name__)


def unlock_perk(
    db: Session,
    user_id: int,
    perk_id: int,
    unlock_method: str,
    intent_session_id: Optional[str] = None,
    merchant_id: Optional[str] = None,
    dwell_time_seconds: Optional[int] = None,
) -> PerkUnlock:
    """
    Unlock a perk for a user.
    
    Enforces:
    - Max unlocks per intent session
    - Cooldown per merchant
    - Confidence tier A or B required (no Tier C)
    
    Args:
        db: Database session
        user_id: User ID
        perk_id: Perk ID
        unlock_method: "dwell_time" or "user_confirmation"
        intent_session_id: Optional intent session ID (required for caps)
        merchant_id: Optional merchant ID
        dwell_time_seconds: Optional dwell time in seconds
    
    Returns:
        Created PerkUnlock record
    
    Raises:
        ValueError: If caps are violated or confidence tier is C
    """
    # Verify perk exists and is active
    perk = db.query(MerchantPerk).filter(
        MerchantPerk.id == perk_id,
        MerchantPerk.is_active == True,
    ).first()
    
    if not perk:
        raise ValueError(f"Perk {perk_id} not found or inactive")
    
    # Use merchant_id from perk if not provided
    final_merchant_id = merchant_id or perk.merchant_id
    
    # Check confidence tier (require A or B, reject C)
    if intent_session_id:
        session = db.query(IntentSession).filter(
            IntentSession.id == intent_session_id,
            IntentSession.user_id == user_id,
        ).first()
        
        if not session:
            raise ValueError(f"Intent session {intent_session_id} not found")
        
        if session.confidence_tier == "C":
            raise ValueError(PERK_UNLOCK_COPY["tier_required"])
    else:
        # If no session provided, we can't check tier - allow but log warning
        logger.warning(f"Unlock perk {perk_id} called without intent_session_id - cannot verify confidence tier")
    
    # Check if already unlocked (idempotency)
    existing = (
        db.query(PerkUnlock)
        .filter(
            PerkUnlock.user_id == user_id,
            PerkUnlock.perk_id == perk_id,
        )
        .first()
    )
    
    if existing:
        logger.info(f"Perk {perk_id} already unlocked for user {user_id}")
        return existing
    
    # Enforce max unlocks per session
    if intent_session_id:
        session_unlock_count = (
            db.query(PerkUnlock)
            .filter(
                PerkUnlock.user_id == user_id,
                PerkUnlock.intent_session_id == intent_session_id,
            )
            .count()
        )
        
        if session_unlock_count >= settings.MAX_PERK_UNLOCKS_PER_SESSION:
            raise ValueError(PERK_UNLOCK_COPY["session_limit"])
    
    # Enforce cooldown per merchant
    if final_merchant_id:
        cooldown_minutes = settings.PERK_COOLDOWN_MINUTES_PER_MERCHANT
        cooldown_threshold = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
        
        recent_unlock = (
            db.query(PerkUnlock)
            .filter(
                PerkUnlock.user_id == user_id,
                PerkUnlock.merchant_id == final_merchant_id,
                PerkUnlock.unlocked_at >= cooldown_threshold,
            )
            .order_by(PerkUnlock.unlocked_at.desc())
            .first()
        )
        
        if recent_unlock:
            minutes_ago = (datetime.utcnow() - recent_unlock.unlocked_at).total_seconds() / 60
            minutes_remaining = cooldown_minutes - minutes_ago
            raise ValueError(f"{PERK_UNLOCK_COPY['cooldown']} ({int(minutes_remaining)} minutes remaining)")
    
    # Create perk unlock record
    unlock = PerkUnlock(
        id=str(uuid.uuid4()),
        user_id=user_id,
        perk_id=perk_id,
        unlock_method=unlock_method,
        intent_session_id=intent_session_id,
        merchant_id=final_merchant_id,
        dwell_time_seconds=dwell_time_seconds,
        unlocked_at=datetime.utcnow(),
    )
    
    db.add(unlock)
    
    # Update wallet pass state
    if intent_session_id:
        # Transition to charging moment if not already
        transition_to_charging_moment(db, user_id, intent_session_id)
    
    # Transition to perk unlocked
    transition_to_perk_unlocked(db, user_id, perk_id)
    
    db.commit()
    db.refresh(unlock)
    
    logger.info(
        f"Unlocked perk {perk_id} for user {user_id} via {unlock_method}"
    )
    
    return unlock

