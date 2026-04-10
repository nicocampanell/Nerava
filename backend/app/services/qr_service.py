"""
QR Service - QR/code logic
Extracts QR/code logic from pilot_redeem.py and services/codes.py
(token → merchant/campaign resolution, status checks).
Also handles merchant QR tokens for national checkout.
"""
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models.domain import DomainMerchant
from ..models.while_you_charge import Merchant, MerchantOfferCode

try:
    from ..utils.log import get_logger
except ImportError:
    logger = logging.getLogger(__name__)
else:
    logger = get_logger(__name__)


def resolve_qr_token(db: Session, token: str) -> Optional[Dict]:
    """
    Resolve QR token to merchant/campaign (legacy pilot codes).
    
    NOTE: For merchant QR tokens (DomainMerchant.qr_token), use resolve_merchant_qr_token().
    
    Args:
        db: Database session
        token: QR redemption token/code
        
    Returns:
        Dict with merchant and campaign info, or None if not found
    """
    # Try to find MerchantOfferCode by code (legacy pilot system)
    offer_code = db.query(MerchantOfferCode).filter(
        MerchantOfferCode.code == token
    ).first()
    
    if offer_code:
        merchant = db.query(Merchant).filter(Merchant.id == offer_code.merchant_id).first()
        if merchant:
            return {
                "merchant_id": merchant.id,
                "merchant_name": merchant.name,
                "campaign_id": offer_code.id,
                "code": offer_code.code,
                "amount_cents": offer_code.amount_cents,
                "is_redeemed": offer_code.is_redeemed,
                "expires_at": offer_code.expires_at.isoformat() if offer_code.expires_at else None,
            }
    
    # Could extend to check other token types (e.g., pilot codes, campaign codes)
    return None


def check_code_status(db: Session, code: str) -> Dict[str, Any]:
    """
    Check code status (valid, expired, redeemed, etc.).
    
    Args:
        db: Database session
        code: Redemption code
        
    Returns:
        Dict with status information
    """
    offer_code = db.query(MerchantOfferCode).filter(
        MerchantOfferCode.code == code
    ).first()
    
    if not offer_code:
        return {
            "status": "not_found",
            "valid": False,
            "error": "Code not found"
        }
    
    if offer_code.is_redeemed:
        return {
            "status": "redeemed",
            "valid": False,
            "error": "Code already redeemed"
        }
    
    if offer_code.expires_at and offer_code.expires_at < datetime.utcnow():
        return {
            "status": "expired",
            "valid": False,
            "error": "Code expired",
            "expires_at": offer_code.expires_at.isoformat()
        }
    
    return {
        "status": "valid",
        "valid": True,
        "merchant_id": offer_code.merchant_id,
        "amount_cents": offer_code.amount_cents,
        "expires_at": offer_code.expires_at.isoformat() if offer_code.expires_at else None,
    }


def create_or_get_merchant_qr(db: Session, merchant: DomainMerchant) -> dict:
    """
    Create or get merchant QR token for national checkout.
    
    If merchant already has a qr_token, returns it.
    Otherwise, generates a new secure token and stores it.
    
    Args:
        db: Database session
        merchant: DomainMerchant instance
        
    Returns:
        Dict with "token" and "url" keys
    """
    # If merchant already has a QR token, return it
    if merchant.qr_token:
        base_url = getattr(settings, 'public_base_url', 'https://my.nerava.network')
        qr_url = f"{base_url}/v1/checkout/qr/{merchant.qr_token}"
        return {
            "token": merchant.qr_token,
            "url": qr_url
        }
    
    # Generate a new secure token (URL-safe, 32 bytes = 43 chars base64)
    token = secrets.token_urlsafe(32)
    
    # Ensure uniqueness (very unlikely collision, but check anyway)
    existing = db.query(DomainMerchant).filter(DomainMerchant.qr_token == token).first()
    if existing:
        # Regenerate if collision (extremely rare)
        token = secrets.token_urlsafe(32)
    
    # Set token and timestamps
    merchant.qr_token = token
    merchant.qr_created_at = datetime.utcnow()
    
    db.commit()
    db.refresh(merchant)
    
    # Build QR URL
    base_url = getattr(settings, 'public_base_url', 'https://my.nerava.network')
    qr_url = f"{base_url}/v1/checkout/qr/{token}"
    
    logger.info(f"Created QR token for merchant {merchant.id}: {token[:8]}...")
    
    return {
        "token": token,
        "url": qr_url
    }


def resolve_merchant_qr_token(db: Session, token: str) -> Optional[DomainMerchant]:
    """
    Resolve merchant QR token to DomainMerchant (for national checkout).
    
    This is separate from the legacy pilot code resolution (resolve_qr_token)
    and works with DomainMerchant.qr_token field.
    
    Args:
        db: Database session
        token: QR token string
        
    Returns:
        DomainMerchant if found, None otherwise
    """
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.qr_token == token,
        DomainMerchant.status == "active"  # Only return active merchants
    ).first()
    
    if merchant:
        # Update last used timestamp
        merchant.qr_last_used_at = datetime.utcnow()
        db.commit()
    
    return merchant

