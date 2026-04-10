"""
Intent Service
Handles intent capture logic: confidence tier assignment, charger lookup, session creation
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Charger, IntentSession, MerchantCache
from app.services.google_places_new import _get_geo_cell

logger = logging.getLogger(__name__)


def find_nearest_charger(
    db: Session, lat: float, lng: float, radius_m: float = 50000
) -> Optional[Tuple[Charger, float]]:
    """
    Find the nearest public charger using PostgreSQL spatial query.

    Args:
        db: Database session
        lat: Latitude
        lng: Longitude
        radius_m: Search radius in meters (default 50km)

    Returns:
        Tuple of (Charger, distance_m) or None if no charger found
    """
    import math

    # Bounding box pre-filter (approx 1 degree = 111km)
    lat_delta = radius_m / 111000
    lng_delta = radius_m / (111000 * math.cos(math.radians(lat)))

    # PostgreSQL Haversine query with bounding box filter
    result = (
        db.query(
            Charger,
            (
                6371000
                * func.acos(
                    func.cos(func.radians(lat))
                    * func.cos(func.radians(Charger.lat))
                    * func.cos(func.radians(Charger.lng) - func.radians(lng))
                    + func.sin(func.radians(lat)) * func.sin(func.radians(Charger.lat))
                )
            ).label("distance_m"),
        )
        .filter(
            Charger.is_public == True,
            Charger.lat.between(lat - lat_delta, lat + lat_delta),
            Charger.lng.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by("distance_m")
        .limit(1)
        .first()
    )

    if result:
        charger, distance_m = result
        return (charger, distance_m)

    return None


def find_nearest_chargers(
    db: Session, lat: float, lng: float, radius_m: float = 25000, limit: int = 20
) -> List[Tuple[Charger, float]]:
    """
    Find the nearest public chargers using PostgreSQL spatial query.

    Args:
        db: Database session
        lat: Latitude
        lng: Longitude
        radius_m: Search radius in meters (default 25km - reasonable driving distance)
        limit: Maximum number of chargers to return (default 5)

    Returns:
        List of (Charger, distance_m) tuples, sorted by distance
    """
    import math

    # Bounding box pre-filter (approx 1 degree = 111km)
    lat_delta = radius_m / 111000
    lng_delta = radius_m / (111000 * math.cos(math.radians(lat)))

    # PostgreSQL Haversine query with bounding box filter
    results = (
        db.query(
            Charger,
            (
                6371000
                * func.acos(
                    func.cos(func.radians(lat))
                    * func.cos(func.radians(Charger.lat))
                    * func.cos(func.radians(Charger.lng) - func.radians(lng))
                    + func.sin(func.radians(lat)) * func.sin(func.radians(Charger.lat))
                )
            ).label("distance_m"),
        )
        .filter(
            Charger.is_public == True,
            Charger.lat.between(lat - lat_delta, lat + lat_delta),
            Charger.lng.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by("distance_m")
        .limit(limit)
        .all()
    )

    # Filter to only include chargers within the actual radius (bounding box is approximate)
    chargers_in_radius = []
    for charger, distance_m in results:
        if distance_m <= radius_m:
            chargers_in_radius.append((charger, distance_m))

    return chargers_in_radius


def assign_confidence_tier(distance_m: Optional[float]) -> str:
    """
    Assign confidence tier based on distance to nearest charger.

    Args:
        distance_m: Distance to nearest charger in meters (None if no charger)

    Returns:
        Confidence tier: "A", "B", or "C"
    """
    if distance_m is None:
        return "C"

    if distance_m <= settings.CONFIDENCE_TIER_A_THRESHOLD_M:
        return "A"
    elif distance_m <= settings.CONFIDENCE_TIER_B_THRESHOLD_M:
        return "B"
    else:
        return "C"


def validate_location_accuracy(accuracy_m: Optional[float]) -> bool:
    """
    Validate that location accuracy meets threshold.

    Args:
        accuracy_m: Location accuracy in meters (None if not provided)

    Returns:
        True if accuracy is acceptable, False otherwise
    """
    if accuracy_m is None:
        # If accuracy not provided, allow but log warning
        logger.warning("Location accuracy not provided, allowing request")
        return True

    threshold = settings.LOCATION_ACCURACY_THRESHOLD_M
    if accuracy_m > threshold:
        logger.warning(f"Location accuracy {accuracy_m}m exceeds threshold {threshold}m")
        return False

    return True


async def create_intent_session(
    db: Session,
    user_id: int,
    lat: float,
    lng: float,
    accuracy_m: Optional[float] = None,
    client_ts: Optional[datetime] = None,
    source: str = "web",
) -> IntentSession:
    """
    Create an intent session with confidence tier assignment.

    Args:
        db: Database session
        user_id: User ID
        lat: Latitude
        lng: Longitude
        accuracy_m: Location accuracy in meters
        client_ts: Client timestamp
        source: Source of the intent (default "web")

    Returns:
        Created IntentSession
    """
    # Validate location accuracy
    if not validate_location_accuracy(accuracy_m):
        raise ValueError(
            f"Location accuracy {accuracy_m}m exceeds threshold {settings.LOCATION_ACCURACY_THRESHOLD_M}m"
        )

    # Find nearest charger
    charger_result = find_nearest_charger(db, lat, lng)
    charger_id = None
    charger_distance_m = None

    if charger_result:
        charger, distance = charger_result
        charger_id = charger.id
        charger_distance_m = distance

    # Assign confidence tier
    confidence_tier = assign_confidence_tier(charger_distance_m)

    # Create intent session
    session = IntentSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        lat=lat,
        lng=lng,
        accuracy_m=accuracy_m,
        client_ts=client_ts,
        charger_id=charger_id,
        charger_distance_m=charger_distance_m,
        confidence_tier=confidence_tier,
        source=source,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    logger.info(
        f"Created intent session {session.id} for user {user_id}: "
        f"tier={confidence_tier}, charger_distance={charger_distance_m}m"
    )

    return session


async def get_merchants_for_intent(
    db: Session,
    lat: float,
    lng: float,
    confidence_tier: str,
    charger_id: Optional[str] = None,
) -> List[Dict]:
    """
    Get merchants for intent session based on confidence tier.

    First tries to get linked merchants from ChargerMerchant database.
    Falls back to Google Places API search if no database merchants found.
    Applies placement rules (boost_weight, badges, daily_cap_cents) if available.

    Args:
        db: Database session
        lat: Latitude
        lng: Longitude
        confidence_tier: Confidence tier ("A", "B", or "C")
        charger_id: Optional charger ID to get linked merchants from database

    Returns:
        List of merchant dictionaries with placement rules applied
    """
    merchants = []

    # First, try to get merchants from ChargerMerchant database if charger_id provided
    if charger_id:
        from app.models.while_you_charge import ChargerMerchant, Merchant

        # Query linked merchants for this charger
        merchant_links = (
            db.query(ChargerMerchant, Merchant)
            .join(Merchant, ChargerMerchant.merchant_id == Merchant.id)
            .filter(ChargerMerchant.charger_id == charger_id)
            .order_by(ChargerMerchant.is_primary.desc(), ChargerMerchant.distance_m.asc())
            .limit(20)
            .all()
        )

        if merchant_links:
            logger.info(f"Found {len(merchant_links)} linked merchants for charger {charger_id}")
            for link, merchant in merchant_links:
                merchant_dict = {
                    "place_id": merchant.place_id or merchant.id,
                    "name": merchant.name,
                    "lat": merchant.lat,
                    "lng": merchant.lng,
                    "distance_m": link.distance_m or 0,
                    "types": merchant.place_types
                    or [merchant.category or merchant.primary_category or "place"],
                    "photo_url": merchant.primary_photo_url
                    or merchant.photo_url
                    or merchant.logo_url,
                    "icon_url": None,
                    "badges": ["Exclusive"] if link.exclusive_title else [],
                    "is_primary": link.is_primary,
                    "exclusive_title": link.exclusive_title,
                    "exclusive_description": link.exclusive_description,
                }
                merchants.append(merchant_dict)

    # No Google Places fallback — only show merchants linked in the database
    if not merchants:
        logger.info(
            f"No database merchants linked for charger_id={charger_id} at lat={lat}, lng={lng}"
        )

    # Cache merchants in database — batch query to reduce DB round trips
    geo_cell_lat, geo_cell_lng = _get_geo_cell(lat, lng)
    expires_at = datetime.utcnow() + timedelta(seconds=settings.MERCHANT_CACHE_TTL_SECONDS)

    # Collect all place_ids in a single pass
    place_ids_to_cache = [m.get("place_id") for m in merchants if m.get("place_id")]

    if place_ids_to_cache:
        # Single IN query to fetch all existing cache entries at once
        existing_entries = (
            db.query(MerchantCache)
            .filter(
                MerchantCache.place_id.in_(place_ids_to_cache),
                MerchantCache.geo_cell_lat == geo_cell_lat,
                MerchantCache.geo_cell_lng == geo_cell_lng,
            )
            .all()
        )
        existing_by_place_id = {entry.place_id: entry for entry in existing_entries}

        # Bulk upsert: update existing, insert new
        for merchant in merchants:
            place_id = merchant.get("place_id")
            if not place_id:
                continue

            cached = existing_by_place_id.get(place_id)
            if cached:
                # Update existing cache entry
                cached.merchant_data = merchant
                cached.photo_url = merchant.get("photo_url")
                cached.expires_at = expires_at
                cached.updated_at = datetime.utcnow()
            else:
                # Create new cache entry
                cache_entry = MerchantCache(
                    place_id=place_id,
                    geo_cell_lat=geo_cell_lat,
                    geo_cell_lng=geo_cell_lng,
                    merchant_data=merchant,
                    photo_url=merchant.get("photo_url"),
                    expires_at=expires_at,
                )
                db.add(cache_entry)

        db.commit()

    # Query placement rules for all merchants
    from app.models import MerchantPlacementRule

    place_ids = [m.get("place_id") for m in merchants if m.get("place_id")]
    placement_rules = {}
    if place_ids:
        rules = (
            db.query(MerchantPlacementRule)
            .filter(
                MerchantPlacementRule.place_id.in_(place_ids),
                MerchantPlacementRule.status == "ACTIVE",
            )
            .all()
        )
        placement_rules = {rule.place_id: rule for rule in rules}

    # Apply placement rules and calculate boosted scores
    merchants_with_scores = []
    for merchant in merchants:
        place_id = merchant.get("place_id")
        if not place_id:
            continue

        # Base score is inverse of distance (closer = higher score)
        base_score = 1000.0 / max(merchant.get("distance_m", 1), 1)

        # Apply placement rule if exists
        rule = placement_rules.get(place_id)
        badges = []
        daily_cap_cents = None

        if rule:
            # Apply boost_weight additively
            boosted_score = base_score + rule.boost_weight

            # Add badges
            if rule.boost_weight > 0:
                badges.append("Boosted")
            if rule.perks_enabled:
                badges.append("Perks available")

            # Include daily_cap_cents (internal use only)
            daily_cap_cents = rule.daily_cap_cents
        else:
            boosted_score = base_score

        merchants_with_scores.append(
            {
                **merchant,
                "_boosted_score": boosted_score,
                "badges": badges if badges else None,
                "daily_cap_cents": daily_cap_cents,
            }
        )

    # Sort by boosted score (descending)
    merchants_sorted = sorted(
        merchants_with_scores, key=lambda m: m.get("_boosted_score", 0), reverse=True
    )

    # Remove internal score field
    for merchant in merchants_sorted:
        merchant.pop("_boosted_score", None)

    return merchants_sorted[:20]  # Return top 20


def get_intent_session_count(db: Session, user_id: int) -> int:
    """
    Get count of intent sessions for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Count of intent sessions
    """
    return db.query(IntentSession).filter(IntentSession.user_id == user_id).count()


def requires_vehicle_onboarding(db: Session, user_id: int, confidence_tier: str) -> bool:
    """
    Check if user requires vehicle onboarding based on session count, completion status, and confidence tier.

    Only requires onboarding when:
    - User has >= N intent sessions (configurable via INTENT_SESSION_ONBOARDING_THRESHOLD)
    - User has NOT completed onboarding (no APPROVED status in VehicleOnboarding)
    - Confidence tier is A or B (not C)

    Args:
        db: Database session
        user_id: User ID
        confidence_tier: Current confidence tier ("A", "B", or "C")

    Returns:
        True if onboarding required, False otherwise
    """
    # Don't require onboarding for Tier C (avoid annoying low-confidence cases)
    if confidence_tier == "C":
        return False

    # Check session count
    session_count = get_intent_session_count(db, user_id)
    threshold = settings.INTENT_SESSION_ONBOARDING_THRESHOLD
    if session_count < threshold:
        return False

    # Check if user has already completed onboarding
    from app.models import VehicleOnboarding

    completed = (
        db.query(VehicleOnboarding)
        .filter(
            VehicleOnboarding.user_id == user_id,
            VehicleOnboarding.status == "APPROVED",
        )
        .first()
    )

    if completed:
        return False

    return True
