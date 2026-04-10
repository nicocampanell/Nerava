"""
Service for wallet pass activation
"""
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.intent import IntentSession
from app.models.wallet_pass import WalletPassActivation, WalletPassStateEnum
from app.models.while_you_charge import Merchant
from app.schemas.wallet import WalletActivateResponse, WalletState


def activate_wallet_pass(
    db: Session,
    session_id: str,
    merchant_id: str
) -> WalletActivateResponse:
    """
    Activate a wallet pass for a given session and merchant.
    
    Args:
        db: Database session
        session_id: Intent session ID
        merchant_id: Merchant ID
    
    Returns:
        WalletActivateResponse with wallet state
    """
    # Verify session exists
    session = db.query(IntentSession).filter(IntentSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Intent session not found")
    
    # Verify merchant exists
    merchant = db.query(Merchant).filter(
        (Merchant.id == merchant_id) | (Merchant.external_id == merchant_id)
    ).first()
    
    # MOCK_PLACES support: use mock merchant if not in DB
    import os
    if not merchant and os.getenv('MOCK_PLACES', 'false').lower() == 'true':
        from app.services.merchant_details import _get_mock_merchant_for_details
        merchant = _get_mock_merchant_for_details(merchant_id)
    
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    
    # Check if wallet pass already exists
    existing = db.query(WalletPassActivation).filter(
        WalletPassActivation.session_id == session_id,
        WalletPassActivation.merchant_id == merchant.id
    ).first()
    
    # Default expiry: now + 60 minutes
    expires_at = datetime.utcnow() + timedelta(minutes=60)
    
    if existing:
        # Update existing
        existing.state = WalletPassStateEnum.ACTIVE
        existing.expires_at = expires_at
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
    else:
        # Create new
        wallet_pass = WalletPassActivation(
            id=str(uuid.uuid4()),
            session_id=session_id,
            merchant_id=merchant.id,
            state=WalletPassStateEnum.ACTIVE,
            expires_at=expires_at
        )
        db.add(wallet_pass)
        db.commit()
        db.refresh(wallet_pass)
        existing = wallet_pass
    
    # Build response
    wallet_state = WalletState(
        state="ACTIVE",
        merchant_id=merchant.id,
        expires_at=existing.expires_at.isoformat(),
        active_copy="This pass is active while you're charging."
    )
    
    return WalletActivateResponse(
        status="ok",
        wallet_state=wallet_state
    )

