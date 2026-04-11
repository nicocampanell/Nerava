"""
Wallet Pass State Service (Mocked)
Manages mocked wallet pass state machine transitions
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import WalletPassState

logger = logging.getLogger(__name__)


def get_or_create_wallet_pass_state(
    db: Session,
    user_id: int,
) -> WalletPassState:
    """
    Get or create wallet pass state for a user.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        WalletPassState record
    """
    state = (
        db.query(WalletPassState)
        .filter(WalletPassState.user_id == user_id)
        .order_by(WalletPassState.created_at.desc())
        .first()
    )
    
    if not state:
        state = WalletPassState(
            id=str(uuid.uuid4()),
            user_id=user_id,
            state="IDLE",
            state_changed_at=datetime.utcnow(),
        )
        db.add(state)
        db.commit()
        db.refresh(state)
    
    return state


def transition_to_charging_moment(
    db: Session,
    user_id: int,
    intent_session_id: str,
) -> WalletPassState:
    """
    Transition wallet pass state to CHARGING_MOMENT.
    
    Args:
        db: Database session
        user_id: User ID
        intent_session_id: Intent session ID
    
    Returns:
        Updated WalletPassState
    """
    state = get_or_create_wallet_pass_state(db, user_id)
    
    if state.state == "IDLE":
        state.state = "CHARGING_MOMENT"
        state.intent_session_id = intent_session_id
        state.state_changed_at = datetime.utcnow()
        state.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(state)
        
        logger.info(f"Wallet pass state transitioned to CHARGING_MOMENT for user {user_id}")
    
    return state


def transition_to_perk_unlocked(
    db: Session,
    user_id: int,
    perk_id: int,
) -> WalletPassState:
    """
    Transition wallet pass state to PERK_UNLOCKED.
    
    Args:
        db: Database session
        user_id: User ID
        perk_id: Perk ID
    
    Returns:
        Updated WalletPassState
    """
    state = get_or_create_wallet_pass_state(db, user_id)
    
    if state.state == "CHARGING_MOMENT":
        state.state = "PERK_UNLOCKED"
        state.perk_id = perk_id
        state.state_changed_at = datetime.utcnow()
        state.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(state)
        
        logger.info(f"Wallet pass state transitioned to PERK_UNLOCKED for user {user_id}, perk {perk_id}")
    
    return state


def reset_to_idle(
    db: Session,
    user_id: int,
) -> WalletPassState:
    """
    Reset wallet pass state to IDLE.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Updated WalletPassState
    """
    state = get_or_create_wallet_pass_state(db, user_id)
    
    state.state = "IDLE"
    state.intent_session_id = None
    state.perk_id = None
    state.state_changed_at = datetime.utcnow()
    state.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(state)
    
    logger.info(f"Wallet pass state reset to IDLE for user {user_id}")
    
    return state



