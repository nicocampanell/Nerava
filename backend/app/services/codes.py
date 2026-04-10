"""
Merchant Offer Code Service

Generates and manages unique redemption codes for merchant discounts.
Code format: PREFIX-MERCHANT-#### (e.g., DOM-SB-4821)
"""
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domains.domain_hub import HUB_ID
from app.models_while_you_charge import Merchant, MerchantOfferCode
from app.utils.log import get_logger

logger = get_logger(__name__)

# Default expiration: 30 days
DEFAULT_EXPIRATION_DAYS = 30

# Merchant name abbreviations for code generation
MERCHANT_ABBREVIATIONS = {
    "starbucks": "SB",
    "target": "TG",
    "whole foods": "WF",
    "neiman marcus": "NM",
    "nordstrom": "ND",
    "macys": "MC",
    "gap": "GP",
    "banana republic": "BR",
}


def _get_merchant_abbreviation(merchant: Merchant) -> str:
    """
    Get abbreviation for merchant name.
    
    Args:
        merchant: Merchant object
        
    Returns:
        2-3 letter abbreviation
    """
    name_lower = merchant.name.lower().strip()
    
    # Check known abbreviations first
    for key, abbrev in MERCHANT_ABBREVIATIONS.items():
        if key in name_lower:
            return abbrev
    
    # Generate abbreviation from name (first 2-3 letters of words)
    words = name_lower.split()
    if len(words) >= 2:
        # Use first letter of first two words
        abbrev = "".join(w[0].upper() for w in words[:2])
    else:
        # Use first 2-3 letters of single word
        abbrev = name_lower[:3].upper()
    
    # Ensure it's 2-3 characters
    return abbrev[:3].upper() if len(abbrev) > 3 else abbrev


def _get_hub_prefix() -> str:
    """
    Get hub prefix for code generation.
    
    Returns:
        Hub prefix (e.g., "DOM" for Domain hub)
    """
    # For now, hardcode based on HUB_ID
    # Can be extended later for multiple hubs
    if HUB_ID == "domain":
        return "DOM"
    return "NER"  # Default fallback


def generate_code(merchant_id: str, db: Session, max_attempts: int = 10) -> str:
    """
    Generate a unique redemption code for a merchant.
    
    Format: PREFIX-MERCHANT-####
    Example: DOM-SB-4821
    
    Args:
        merchant_id: Merchant ID
        db: Database session
        max_attempts: Maximum attempts to generate unique code
        
    Returns:
        Unique redemption code
        
    Raises:
        ValueError: If merchant not found or unable to generate unique code
    """
    # Fetch merchant to get name for abbreviation
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if not merchant:
        raise ValueError(f"Merchant {merchant_id} not found")
    
    hub_prefix = _get_hub_prefix()
    merchant_abbrev = _get_merchant_abbreviation(merchant)
    
    # Try to generate unique code
    for attempt in range(max_attempts):
        # Generate 4-digit random number
        random_num = random.randint(1000, 9999)
        code = f"{hub_prefix}-{merchant_abbrev}-{random_num}"
        
        # Check if code already exists
        existing = db.query(MerchantOfferCode).filter(MerchantOfferCode.code == code).first()
        if not existing:
            logger.info(f"Generated unique code {code} for merchant {merchant_id}")
            return code
        
        logger.warning(f"Code {code} already exists, trying again (attempt {attempt + 1}/{max_attempts})")
    
    # If we exhausted attempts, try with UUID suffix
    uuid_suffix = str(uuid.uuid4())[:4].upper()
    code = f"{hub_prefix}-{merchant_abbrev}-{uuid_suffix}"
    
    # Final check
    existing = db.query(MerchantOfferCode).filter(MerchantOfferCode.code == code).first()
    if existing:
        raise ValueError(f"Unable to generate unique code after {max_attempts} attempts")
    
    logger.warning(f"Generated code with UUID suffix: {code}")
    return code


def store_code(
    db: Session,
    code: str,
    merchant_id: str,
    amount_cents: int,
    expiration_days: Optional[int] = None
) -> MerchantOfferCode:
    """
    Store a redemption code in the database.
    
    Args:
        db: Database session
        code: Unique redemption code
        merchant_id: Merchant ID
        amount_cents: Discount amount in cents
        expiration_days: Days until expiration (defaults to DEFAULT_EXPIRATION_DAYS)
        
    Returns:
        MerchantOfferCode object
        
    Raises:
        IntegrityError: If code already exists
    """
    if expiration_days is None:
        expiration_days = DEFAULT_EXPIRATION_DAYS
    
    expires_at = datetime.utcnow() + timedelta(days=expiration_days)
    
    offer_code = MerchantOfferCode(
        id=str(uuid.uuid4()),
        merchant_id=merchant_id,
        code=code,
        amount_cents=amount_cents,
        is_redeemed=False,
        expires_at=expires_at
    )
    
    db.add(offer_code)
    
    try:
        db.commit()
        db.refresh(offer_code)
        logger.info(f"Stored offer code {code} for merchant {merchant_id} (amount: {amount_cents} cents, expires: {expires_at})")
        return offer_code
    except IntegrityError as e:
        db.rollback()
        logger.error(f"Failed to store code {code}: code already exists")
        raise ValueError(f"Code {code} already exists") from e


def fetch_code(db: Session, code: str) -> Optional[MerchantOfferCode]:
    """
    Fetch a redemption code by code string.
    
    Args:
        db: Database session
        code: Redemption code to fetch
        
    Returns:
        MerchantOfferCode object if found, None otherwise
    """
    offer_code = db.query(MerchantOfferCode).filter(MerchantOfferCode.code == code).first()
    
    if offer_code:
        logger.info(f"Fetched offer code {code} (merchant: {offer_code.merchant_id}, redeemed: {offer_code.is_redeemed})")
    else:
        logger.warning(f"Offer code {code} not found")
    
    return offer_code


def is_code_valid(offer_code: MerchantOfferCode) -> bool:
    """
    Check if a code is valid (not redeemed and not expired).
    
    Args:
        offer_code: MerchantOfferCode object
        
    Returns:
        True if code is valid, False otherwise
    """
    if offer_code.is_redeemed:
        return False
    
    if offer_code.expires_at and offer_code.expires_at < datetime.utcnow():
        return False
    
    return True

