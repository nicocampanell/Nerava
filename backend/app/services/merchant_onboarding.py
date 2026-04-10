"""
Merchant Onboarding Service
Extracts merchant creation/validation logic from merchants_domain.py and related services.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..models.domain import DomainMerchant, Zone
from .qr_service import create_or_get_merchant_qr
from .square_service import SquareOAuthResult, fetch_square_location_stats
from .token_encryption import encrypt_token

logger = logging.getLogger(__name__)


def validate_merchant_location(
    db: Session,
    zone_slug: str,
    lat: float,
    lng: float
) -> Zone:
    """
    Validate merchant location is within zone bounds.
    
    Args:
        db: Database session
        zone_slug: Zone identifier
        lat: Merchant latitude
        lng: Merchant longitude
        
    Returns:
        Zone object if valid
        
    Raises:
        ValueError: If zone not found or location outside bounds
    """
    zone = db.query(Zone).filter(Zone.slug == zone_slug).first()
    if not zone:
        raise ValueError(f"Invalid zone: {zone_slug}")
    
    # Import haversine_distance from drivers_domain
    from ..routers.drivers_domain import haversine_distance
    distance = haversine_distance(
        zone.center_lat, zone.center_lng,
        lat, lng
    )
    
    if distance > zone.radius_m:
        raise ValueError(f"Location must be within {zone.radius_m}m of {zone.name} center")
    
    return zone


def normalize_merchant_data(
    business_name: str,
    google_place_id: Optional[str] = None,
    addr_line1: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Normalize merchant name/address/category as current logic dictates.
    
    Args:
        business_name: Raw business name
        google_place_id: Optional Google Place ID
        addr_line1: Address line 1
        city: City
        state: State
        postal_code: Postal code
        
    Returns:
        Dict with normalized merchant data
    """
    # Normalize name (trim, capitalize appropriately)
    normalized_name = business_name.strip()
    
    # For now, just return normalized data
    # Could add more sophisticated normalization here
    return {
        "name": normalized_name,
        "google_place_id": google_place_id,
        "addr_line1": addr_line1,
        "city": city,
        "state": state,
        "postal_code": postal_code,
    }


def check_duplicate_merchant(
    db: Session,
    google_place_id: Optional[str] = None,
    name: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> Optional[DomainMerchant]:
    """
    Check if merchant already exists (duplicate detection).
    
    Args:
        db: Database session
        google_place_id: Google Place ID to check
        name: Business name to check
        lat: Latitude for location-based check
        lng: Longitude for location-based check
        
    Returns:
        Existing DomainMerchant if found, None otherwise
    """
    # Check by Google Place ID first (most reliable)
    if google_place_id:
        existing = db.query(DomainMerchant).filter(
            DomainMerchant.google_place_id == google_place_id
        ).first()
        if existing:
            return existing
    
    # Check by name and location (within small radius)
    if name and lat is not None and lng is not None:
        from ..routers.drivers_domain import haversine_distance
        # Check merchants with similar names within 100m
        candidates = db.query(DomainMerchant).filter(
            DomainMerchant.name.ilike(f"%{name}%")
        ).all()
        
        for candidate in candidates:
            distance = haversine_distance(candidate.lat, candidate.lng, lat, lng)
            if distance < 100:  # Within 100 meters
                return candidate
    
    return None


def _calculate_recommended_perk(aov_cents: int) -> int:
    """
    Calculate recommended perk amount based on AOV.
    
    Rules:
    - 15% of AOV, rounded to nearest 50 cents or $1
    - Minimum: $1 (100 cents)
    - Maximum: $5 (500 cents) for initial perk
    
    Args:
        aov_cents: Average order value in cents
        
    Returns:
        Recommended perk amount in cents
    """
    # 15% of AOV
    raw_perk = int(round(0.15 * aov_cents))
    
    # Round to nearest 50 cents
    rounded_perk = round(raw_perk / 50) * 50
    
    # Ensure minimum $1
    rounded_perk = max(100, rounded_perk)
    
    # Cap at $5 for initial perk
    rounded_perk = min(500, rounded_perk)
    
    return rounded_perk


def _format_perk_label(perk_cents: int) -> str:
    """
    Format perk amount as a human-readable label.
    
    Args:
        perk_cents: Perk amount in cents
        
    Returns:
        Formatted label (e.g., "$3 off any order")
    """
    dollars = perk_cents / 100
    if dollars == int(dollars):
        return f"${int(dollars)} off any order"
    else:
        return f"${dollars:.2f} off any order"


async def onboard_merchant_via_square(
    db: Session,
    user_id: Optional[int],
    square_result: SquareOAuthResult,
) -> DomainMerchant:
    """
    Onboard a merchant via Square OAuth.
    
    Steps:
    1. Look up or create merchant via square_merchant_id
    2. Store Square connection details
    3. Fetch AOV from Square
    4. Calculate recommended perk
    5. Create or get QR token
    6. Commit and return merchant
    
    Args:
        db: Database session
        user_id: Optional user ID (merchant owner)
        square_result: Square OAuth result from exchange_square_oauth_code
        
    Returns:
        DomainMerchant instance (created or updated)
    """
    # Check if merchant already exists by square_merchant_id
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.square_merchant_id == square_result.merchant_id
    ).first()
    
    if merchant:
        # Update existing merchant with Square connection
        logger.info(f"Updating existing merchant {merchant.id} with Square connection")
    else:
        # Create new merchant
        merchant_id = str(uuid.uuid4())
        merchant = DomainMerchant(
            id=merchant_id,
            name="Square Merchant",  # Will be updated when we fetch merchant details
            status="active",
            zone_slug="national",  # Default zone for national merchants
            nova_balance=0,
            lat=0.0,  # Placeholder - will need to fetch from Square or Google Places
            lng=0.0,
        )
        db.add(merchant)
        db.flush()
        logger.info(f"Created new merchant {merchant.id} for Square merchant {square_result.merchant_id}")
    
    # Store Square connection details
    merchant.square_merchant_id = square_result.merchant_id
    merchant.square_location_id = square_result.location_id
    # Encrypt token before storing
    merchant.square_access_token = encrypt_token(square_result.access_token)
    merchant.square_connected_at = datetime.utcnow()
    
    if user_id:
        merchant.owner_user_id = user_id
    
    # Fetch AOV from Square
    try:
        location_stats = await fetch_square_location_stats(
            square_result.access_token,
            square_result.location_id
        )
        merchant.avg_order_value_cents = location_stats.avg_order_value_cents
        
        # Calculate recommended perk
        merchant.recommended_perk_cents = _calculate_recommended_perk(
            location_stats.avg_order_value_cents
        )
        merchant.perk_label = _format_perk_label(merchant.recommended_perk_cents)
        
        logger.info(
            f"Set AOV: ${merchant.avg_order_value_cents / 100:.2f}, "
            f"Recommended perk: ${merchant.recommended_perk_cents / 100:.2f}"
        )
    except Exception as e:
        logger.error(f"Failed to fetch Square location stats: {e}", exc_info=True)
        # Continue with defaults if AOV fetch fails
        merchant.avg_order_value_cents = 1500  # Default $15
        merchant.recommended_perk_cents = 300  # Default $3
        merchant.perk_label = "$3 off any order"
    
    # Create or get QR token
    qr_result = create_or_get_merchant_qr(db, merchant)
    # qr_result is a dict with "token" and "url" - merchant.qr_token is already set by create_or_get_merchant_qr
    
    db.commit()
    db.refresh(merchant)
    
    return merchant

