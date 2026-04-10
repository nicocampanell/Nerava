"""
Service for fetching merchant details
"""
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.while_you_charge import AmenityVote, ChargerMerchant, Merchant, MerchantPerk
from app.schemas.merchants import (
    ActionsInfo,
    MerchantDetailsResponse,
    MerchantInfo,
    MomentInfo,
    PerkInfo,
    WalletInfo,
)

logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in miles"""
    R = 3959  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def _convert_price_level(price_level: Any) -> Optional[int]:
    """
    Convert Google Places API price_level to integer.

    Google Places API v1 returns price level as string enum:
    - PRICE_LEVEL_FREE = 0
    - PRICE_LEVEL_INEXPENSIVE = 1
    - PRICE_LEVEL_MODERATE = 2
    - PRICE_LEVEL_EXPENSIVE = 3
    - PRICE_LEVEL_VERY_EXPENSIVE = 4
    """
    if price_level is None:
        return None
    if isinstance(price_level, int):
        return price_level
    if isinstance(price_level, str):
        mapping = {
            'PRICE_LEVEL_FREE': 0,
            'PRICE_LEVEL_INEXPENSIVE': 1,
            'PRICE_LEVEL_MODERATE': 2,
            'PRICE_LEVEL_EXPENSIVE': 3,
            'PRICE_LEVEL_VERY_EXPENSIVE': 4,
        }
        return mapping.get(price_level)
    return None


def format_hours_today(hours_json: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Format hours_json into 'HH AM-HH PM · Open now' string.

    Uses weekdayDescriptions from Google Places API.

    Args:
        hours_json: Dictionary with opening hours from Google Places API

    Returns:
        Formatted string like "11 AM-11 PM · Open now" or None if no hours available
    """
    if not hours_json:
        return None

    weekday_desc = hours_json.get("weekdayDescriptions", [])
    if not weekday_desc:
        return None

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    today = days[datetime.now().weekday()]

    for desc in weekday_desc:
        if today in desc:
            hours_part = desc.replace(f"{today}: ", "")
            status = "Open now" if hours_json.get("openNow") else "Closed"
            return f"{hours_part} · {status}"

    return None


def _get_hours_display(merchant) -> Optional[str]:
    """
    Get hours display string from merchant, trying multiple sources.

    Priority:
    1. hours_json (Google Places API format)
    2. hours_text + open_now (simple string format)

    Returns formatted string like "11 AM–11 PM · Open now" or None
    """
    # Try hours_json first (Google Places API format)
    hours_json = getattr(merchant, 'hours_json', None)
    if hours_json:
        result = format_hours_today(hours_json)
        if result:
            return result

    # Fallback to hours_text + open_now
    hours_text = getattr(merchant, 'hours_text', None)
    if hours_text:
        open_now = getattr(merchant, 'open_now', None)
        status = "Open now" if open_now else "Closed" if open_now is False else ""
        if status:
            return f"{hours_text} · {status}"
        return hours_text

    return None


def _get_mock_merchant_for_details(merchant_id: str) -> Optional[Merchant]:
    """
    Return mock Merchant object for fixture merchants when MOCK_PLACES is enabled.
    Creates a temporary Merchant object (not persisted to DB).
    """
    mock_merchants = {
        "mock_asadas_grill": Merchant(
            id="m_mock_asadas",
            external_id="mock_asadas_grill",
            name="Asadas Grill",
            category="Restaurant",
            primary_category="food",
            lat=30.2680,
            lng=-97.7435,
            address="123 Main St, Austin, TX",
            rating=4.5,
            price_level=2,
            photo_url=None,
        ),
        "mock_eggman_atx": Merchant(
            id="m_mock_eggman",
            external_id="mock_eggman_atx",
            name="Eggman ATX",
            category="Restaurant",
            primary_category="food",
            lat=30.2665,
            lng=-97.7425,
            address="456 Main St, Austin, TX",
            rating=4.7,
            price_level=2,
            photo_url=None,
        ),
        "mock_coffee_shop": Merchant(
            id="m_mock_coffee",
            external_id="mock_coffee_shop",
            name="Test Coffee Shop",
            category="Coffee",
            primary_category="coffee",
            lat=30.2675,
            lng=-97.7440,
            address="789 Main St, Austin, TX",
            rating=4.3,
            price_level=1,
            photo_url=None,
        ),
    }
    return mock_merchants.get(merchant_id)


async def get_merchant_details(
    db: Session,
    merchant_id: str,
    session_id: Optional[str] = None,
    driver_user_id: Optional[int] = None,
) -> MerchantDetailsResponse:
    """
    Get merchant details for a given merchant ID.
    
    Args:
        db: Database session
        merchant_id: Merchant ID (can be internal ID or Google Places external_id)
        session_id: Optional intent session ID for context (distance calculation)
    
    Returns:
        MerchantDetailsResponse with merchant info, moment, perk, wallet state, and actions
    """
    # Try to find merchant by ID or external_id
    # Handle Google Places IDs that may have "google_" prefix
    clean_merchant_id = merchant_id
    if merchant_id.startswith("google_"):
        clean_merchant_id = merchant_id.replace("google_", "", 1)
    
    merchant = db.query(Merchant).filter(
        (Merchant.id == merchant_id) | 
        (Merchant.external_id == merchant_id) |
        (Merchant.external_id == clean_merchant_id) |
        (Merchant.place_id == clean_merchant_id)
    ).first()
    
    # MOCK_PLACES support: return mock data for fixture merchants if not in DB
    if not merchant and os.getenv('MOCK_PLACES', 'false').lower() == 'true':
        merchant = _get_mock_merchant_for_details(merchant_id)
    
    if not merchant:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Merchant not found")
    
    # Get active perk (first active perk)
    # For mock merchants, skip DB query
    # Only merchants with perks in the database will show exclusive offers
    perk = None
    if merchant.id and not merchant.id.startswith("m_mock_"):
        perk = db.query(MerchantPerk).filter(
            MerchantPerk.merchant_id == merchant.id,
            MerchantPerk.is_active == True
        ).first()

    # Only set perk info if a perk exists in the database
    perk_info = None
    if perk:
        perk_info = PerkInfo(
            title=perk.title or "EV Rewards",
            badge="Exclusive",
            description=perk.description or "Show your Nerava app to redeem your reward while you charge."
        )

    # Calculate distance and walk time from ChargerMerchant link
    distance_miles = 0.0
    moment_label = None
    moment_copy = "Fits your charge window"

    # Query ChargerMerchant for walk time and distance (only for real merchants)
    link = None
    if merchant.id and not merchant.id.startswith("m_mock_"):
        link = db.query(ChargerMerchant).filter(
            ChargerMerchant.merchant_id == merchant.id
        ).first()

    # Fallback: if no MerchantPerk, use ChargerMerchant.exclusive_title
    if not perk_info and link and link.exclusive_title:
        perk_info = PerkInfo(
            title=link.exclusive_title,
            badge="Exclusive",
            description=link.exclusive_description or "Show your Nerava app to redeem your reward while you charge.",
        )
    
    if link:
        if link.walk_duration_s is not None:
            walk_mins = max(1, link.walk_duration_s // 60)
            moment_label = f"{walk_mins} min walk"
        elif link.distance_m is not None:
            walk_mins = max(1, int(link.distance_m / 80 / 60))
            moment_label = f"{walk_mins} min walk"
        else:
            # Link exists but no distance/time data - default to 1 min
            moment_label = "1 min walk"
        
        if link.distance_m is not None:
            distance_miles = round(link.distance_m / 1609.34, 1)
    
    # Format category
    category = merchant.category or "Restaurant"
    if merchant.primary_category:
        category_map = {
            "coffee": "Coffee • Bakery",
            "food": "Restaurant • Food",
            "other": "Shop • Services"
        }
        category = category_map.get(merchant.primary_category, category)
    
    # Ensure merchant has Google Places photos - enrich if missing
    # Only enrich if merchant has a real DB ID (not mock) and place_id
    if merchant.id and not merchant.id.startswith("m_mock_") and not getattr(merchant, 'primary_photo_url', None) and getattr(merchant, 'place_id', None):
        from app.services.merchant_enrichment import enrich_from_google_places
        try:
            await enrich_from_google_places(db, merchant, merchant.place_id, force_refresh=False)
            # Refresh merchant from DB to get updated photo URLs
            try:
                db.refresh(merchant)
            except Exception as refresh_error:
                logger.warning(f"[MerchantDetails] Failed to refresh merchant after enrichment: {refresh_error}")
        except Exception as e:
            logger.warning(f"[MerchantDetails] Failed to enrich photos for merchant {merchant.id}: {e}", exc_info=True)
    
    # Get activation counts (only for real merchants, not mocks)
    from app.services.merchant_activation_counts import get_merchant_activation_counts
    if merchant.id and not merchant.id.startswith("m_mock_"):
        counts = get_merchant_activation_counts(db, merchant.id)
    else:
        counts = {"activations_today": 0, "verified_visits_today": 0}
    
    # Aggregate amenity votes (only for real merchants, not mocks)
    amenity_votes = {}
    if merchant.id and not merchant.id.startswith("m_mock_"):
        # Single query to get all vote counts grouped by amenity and vote_type
        vote_counts = db.query(
            AmenityVote.amenity,
            AmenityVote.vote_type,
            func.count(AmenityVote.id).label('count')
        ).filter(
            AmenityVote.merchant_id == merchant.id
        ).group_by(
            AmenityVote.amenity,
            AmenityVote.vote_type
        ).all()
        
        # Initialize both amenities with zero counts
        amenity_votes = {
            'bathroom': {'upvotes': 0, 'downvotes': 0},
            'wifi': {'upvotes': 0, 'downvotes': 0}
        }
        
        # Update from query results
        for amenity, vote_type, count in vote_counts:
            if amenity in amenity_votes:
                if vote_type == 'up':
                    amenity_votes[amenity]['upvotes'] = count
                elif vote_type == 'down':
                    amenity_votes[amenity]['downvotes'] = count
    else:
        # Mock merchants: return empty amenities
        amenity_votes = {
            'bathroom': {'upvotes': 0, 'downvotes': 0},
            'wifi': {'upvotes': 0, 'downvotes': 0}
        }
    
    # Build merchant info - only use primary_photo_url from Google Places (no fallback)
    merchant_info = MerchantInfo(
        id=merchant.id or merchant_id,  # Fallback to merchant_id if id is None
        name=merchant.name or "Unknown Merchant",
        category=category,
        photo_url=getattr(merchant, 'primary_photo_url', None),  # Only Google Places photos, no fallback
        photo_urls=getattr(merchant, 'photo_urls', None) if getattr(merchant, 'photo_urls', None) else [],  # Ensure array, not None
        description=getattr(merchant, 'description', None),  # Add: description
        hours_today=_get_hours_display(merchant),  # Add: hours
        address=getattr(merchant, 'address', None),
        phone=getattr(merchant, 'phone', None),
        website=getattr(merchant, 'website', None),
        rating=getattr(merchant, 'rating', None),
        price_level=_convert_price_level(getattr(merchant, 'price_level', None)),
        activations_today=counts["activations_today"],
        verified_visits_today=counts["verified_visits_today"],
        amenities=amenity_votes,
        place_id=getattr(merchant, 'place_id', None)  # Google Places ID
    )
    
    # Build moment info
    moment_info = MomentInfo(
        label=moment_label,
        distance_miles=round(distance_miles, 1),
        moment_copy=moment_copy
    )
    
    # Build wallet info (check if wallet pass exists for this session+merchant)
    # Only allow adding to wallet if there's a perk
    wallet_state = "INACTIVE"
    can_add = perk_info is not None
    if session_id and merchant.id and not merchant.id.startswith("m_mock_"):
        # Check if wallet pass already exists
        from datetime import datetime

        from app.models.wallet_pass import WalletPassActivation, WalletPassStateEnum
        existing_pass = db.query(WalletPassActivation).filter(
            WalletPassActivation.session_id == session_id,
            WalletPassActivation.merchant_id == merchant.id,
            WalletPassActivation.state == WalletPassStateEnum.ACTIVE,
            WalletPassActivation.expires_at > datetime.utcnow()
        ).first()
        if existing_pass:
            wallet_state = "ACTIVE"
            can_add = False
        else:
            wallet_state = "INACTIVE"
            can_add = True
    
    wallet_info = WalletInfo(
        can_add=can_add,
        state=wallet_state,
        active_copy="Active while charging" if wallet_state == "ACTIVE" else None
    )
    
    # Build actions info — use merchant name + address for better Google Maps pin accuracy
    directions_url = None
    if merchant.lat and merchant.lng:
        import urllib.parse
        address = getattr(merchant, 'formatted_address', None) or getattr(merchant, 'address', None)
        if merchant.name and address:
            query = urllib.parse.quote(f"{merchant.name}, {address}")
            directions_url = f"https://maps.google.com/maps/search/?api=1&query={query}"
        elif merchant.name:
            query = urllib.parse.quote(merchant.name)
            directions_url = f"https://maps.google.com/maps/search/?api=1&query={query}"
        else:
            directions_url = f"https://maps.google.com/?q={merchant.lat},{merchant.lng}"
    
    actions_info = ActionsInfo(
        add_to_wallet=True,
        get_directions_url=directions_url
    )
    
    # Build reward state (for CTA logic in frontend)
    reward_state_data = None
    try:
        from app.schemas.merchants import MerchantRewardStateInfo
        from app.services.merchant_reward_service import get_merchant_reward_state
        raw_state = get_merchant_reward_state(
            db=db,
            place_id=getattr(merchant, 'place_id', None),
            merchant_id=merchant.id if merchant.id and not merchant.id.startswith("m_mock_") else None,
            driver_user_id=driver_user_id,
        )
        reward_state_data = MerchantRewardStateInfo(**raw_state)
    except Exception as e:
        logger.warning(f"[MerchantDetails] Failed to get reward state: {e}")

    return MerchantDetailsResponse(
        merchant=merchant_info,
        moment=moment_info,
        perk=perk_info,
        wallet=wallet_info,
        actions=actions_info,
        reward_state=reward_state_data,
    )

