"""
Demo seed service - ensures demo data exists for sandbox testing.
Sandbox-only functionality, gated behind DEMO_MODE or DEMO_QR_ENABLED.
"""
import os
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from ..core.config import settings
from ..models.domain import DomainMerchant, MerchantReward


def ensure_eggman_demo_reward(db: Session) -> Optional[MerchantReward]:
    """
    Creates predefined 300 Nova 'Free Coffee' reward
    for Eggman sandbox merchant if missing.
    
    Only runs when DEMO_MODE=true OR DEMO_QR_ENABLED=true.
    
    Args:
        db: Database session
        
    Returns:
        MerchantReward if created/found, None if demo mode disabled or merchant not found
    """
    # Check if demo mode is enabled
    demo_mode = settings.DEMO_MODE
    demo_qr_enabled = os.getenv("DEMO_QR_ENABLED", "false").lower() == "true"
    
    if not (demo_mode or demo_qr_enabled):
        return None
    
    # Look up Eggman merchant by Square sandbox merchant ID
    # In sandbox, we'll look for a merchant with name containing "Eggman" or specific Square ID
    # For now, we'll search by name pattern (can be made more specific later)
    eggman_merchant = db.query(DomainMerchant).filter(
        DomainMerchant.name.ilike("%Eggman%"),
        DomainMerchant.status == "active"
    ).first()
    
    # If not found by name, try to find by Square merchant ID from env
    if not eggman_merchant:
        square_merchant_id = os.getenv("DEMO_EGGMAN_SQUARE_MERCHANT_ID")
        if square_merchant_id:
            eggman_merchant = db.query(DomainMerchant).filter(
                DomainMerchant.square_merchant_id == square_merchant_id,
                DomainMerchant.status == "active"
            ).first()
    
    if not eggman_merchant:
        # Merchant not found - skip silently (not an error in demo mode)
        return None
    
    # Check if reward already exists
    existing_reward = db.query(MerchantReward).filter(
        MerchantReward.merchant_id == eggman_merchant.id,
        MerchantReward.nova_amount == 300,
        MerchantReward.title == "Free Coffee",
        MerchantReward.is_active == True
    ).first()
    
    if existing_reward:
        return existing_reward
    
    # Create the reward
    reward = MerchantReward(
        id=str(uuid.uuid4()),
        merchant_id=eggman_merchant.id,
        nova_amount=300,
        title="Free Coffee",
        description="Redeem for a free coffee",
        is_active=True
    )
    
    db.add(reward)
    db.commit()
    db.refresh(reward)
    
    return reward









