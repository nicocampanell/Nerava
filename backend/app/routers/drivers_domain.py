"""
Domain Charge Party MVP Driver Router
Driver-specific endpoints for charging sessions and Nova operations
"""
import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_driver import get_current_driver, get_current_driver_optional
from app.models import User
from app.models_domain import DomainChargingSession, DomainMerchant, NovaTransaction
from app.services.incentives import get_offpeak_state
from app.services.nova_service import NovaService

# Location check response cache — keyed by rounded coordinates
# Reduces DB load for repeated location checks from the same area
# Bounded TTLCache: max 2,000 entries, auto-expire after 10 seconds
_location_check_cache: TTLCache = TTLCache(maxsize=2000, ttl=10.0)

router = APIRouter(prefix="/v1/drivers", tags=["drivers"])


# Request/Response Models
class JoinChargePartyRequest(BaseModel):
    charger_id: Optional[str] = None  # event_slug comes from path parameter
    merchant_id: Optional[str] = None  # Optional merchant for the session
    user_lat: Optional[float] = None  # Optional user location for verify_dwell initialization
    user_lng: Optional[float] = None


class JoinChargePartyResponse(BaseModel):
    session_id: str
    event_id: str
    status: str


class NearbyMerchantResponse(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    zone_slug: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    # Additional fields for frontend compatibility
    merchant_id: Optional[str] = None
    nova_reward: Optional[int] = None
    walk_time_s: Optional[int] = None
    walk_time_seconds: Optional[int] = None
    distance_m: Optional[int] = None
    logo_url: Optional[str] = None
    category: Optional[str] = None
    # Primary merchant override fields
    is_primary: Optional[bool] = None
    exclusive_title: Optional[str] = None
    exclusive_description: Optional[str] = None
    open_now: Optional[bool] = None
    open_until: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_urls: Optional[List[str]] = None


class RedeemNovaRequest(BaseModel):
    merchant_id: str
    amount: int
    session_id: Optional[str] = None
    idempotency_key: Optional[str] = None  # Optional idempotency key for deduplication


class RedeemNovaResponse(BaseModel):
    transaction_id: str
    driver_balance: int
    merchant_balance: int
    amount: int


@router.post("/charge_events/{event_slug}/join", response_model=JoinChargePartyResponse)
def join_charge_party(
    event_slug: str,
    request: JoinChargePartyRequest,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Join a charge party event and create a charging session.
    
    New events are configured via EnergyEvent rows (event_slug, zone_slug),
    not by adding new endpoints. This endpoint works for any active event.
    """
    from app.models_domain import EnergyEvent
    
    # Look up event by slug
    event = db.query(EnergyEvent).filter(
        EnergyEvent.slug == event_slug,
        EnergyEvent.status == "active"
    ).first()
    
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event '{event_slug}' not found or not active"
        )
    
    # Create charging session with event_id
    session_id = str(uuid.uuid4())
    session = DomainChargingSession(
        id=session_id,
        driver_user_id=user.id,
        charger_provider="tesla" if request.charger_id else "manual",
        start_time=datetime.utcnow(),
        event_id=event.id,
        verified=False
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    
    # Initialize old sessions table entry for verify_dwell bridge
    # TODO: Migrate verify_dwell to work directly with DomainChargingSession
    from app.services.session_service import SessionService
    SessionService.initialize_verify_dwell_session(
        db=db,
        session_id=session_id,
        driver_user_id=user.id,
        charger_id=request.charger_id,
        merchant_id=request.merchant_id,
        user_lat=request.user_lat,
        user_lng=request.user_lng
    )
    
    return JoinChargePartyResponse(
        session_id=session_id,
        event_id=event_slug,
        status="started"
    )


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters"""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


@router.get("/merchants/nearby")
async def get_nearby_merchants(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    zone_slug: str = Query(..., description="Zone slug (e.g., domain_austin)"),
    radius_m: float = Query(5000, description="Radius in meters"),
    q: Optional[str] = Query(None, description="Search query (merchant name)"),
    category: Optional[str] = Query(None, description="Category filter (e.g., coffee, food)"),
    nova_only: bool = Query(True, description="Filter to Nova-accepting merchants only"),
    max_distance_to_charger_m: Optional[int] = Query(None, description="Maximum distance to charger in meters"),
    while_you_charge: bool = Query(False, description="Filter to merchants within 0.5 miles (805m) of nearest charger"),
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get nearby merchants in a zone.
    
    Bridge to while_you_charge service to get full merchant data with perks, logos, walk times.
    Returns the same shape as the pilot while_you_charge endpoint for compatibility.
    
    Zones are data-scoped (configured via Zone rows), not path-scoped.
    New zones/events don't require new endpoints.
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        "Nearby merchants requested",
        extra={
            "lat": lat, "lng": lng, "zone_slug": zone_slug, "radius_m": radius_m,
            "q": q, "category": category, "nova_only": nova_only,
            "max_distance_to_charger_m": max_distance_to_charger_m, "while_you_charge": while_you_charge, "user_id": user.id
        }
    )
    # For now, bridge to the Domain hub view which has all the merchant data we need
    # TODO: Eventually refactor to query DomainMerchant + enrich with perks/logo/walk_time
    if zone_slug == "domain_austin":
        from app.models.while_you_charge import ChargerMerchant, MerchantPerk
        from app.services.while_you_charge import (
            build_recommended_merchants_from_chargers,
            get_domain_hub_view_async,
        )
        from app.utils.pwa_responses import shape_charger, shape_merchant
        
        # Get Domain hub view with chargers and merchants
        hub_view = await get_domain_hub_view_async(db)
        
        # Get shaped chargers with merchants
        shaped_chargers = []
        for charger in hub_view.get("chargers", []):
            shaped = shape_charger(
                charger,
                user_lat=lat,
                user_lng=lng
            )
            # Attach merchants if present
            if "merchants" in charger:
                shaped_merchants = []
                for merchant in charger["merchants"]:
                    # Convert walk_minutes to walk_time_s if needed
                    if "walk_minutes" in merchant and "walk_time_s" not in merchant:
                        merchant["walk_time_s"] = merchant["walk_minutes"] * 60
                    shaped_m = shape_merchant(
                        merchant,
                        user_lat=lat,
                        user_lng=lng
                    )
                    # Ensure walk_time_seconds for aggregation
                    if "walk_time_s" in shaped_m:
                        shaped_m["walk_time_seconds"] = shaped_m["walk_time_s"]
                    elif "walk_minutes" in merchant:
                        shaped_m["walk_time_seconds"] = int(merchant["walk_minutes"] * 60)
                    # Ensure merchant_id for aggregation
                    if "id" in shaped_m and "merchant_id" not in shaped_m:
                        shaped_m["merchant_id"] = shaped_m["id"]
                    shaped_merchants.append(shaped_m)
                shaped["merchants"] = shaped_merchants
            shaped_chargers.append(shaped)
        
        # Build recommended merchants from chargers (same logic as pilot endpoint)
        recommended_merchants = build_recommended_merchants_from_chargers(shaped_chargers, limit=20)
        
        # Enrich merchants with distance_to_charger_m and nova_accepted
        merchant_ids = [m.get("id") or m.get("merchant_id") for m in recommended_merchants if m.get("id") or m.get("merchant_id")]
        
        # Get Merchant records to access cached fields (primary_category, nearest_charger_distance_m)
        from app.models.while_you_charge import Merchant
        merchant_records = db.query(Merchant).filter(Merchant.id.in_(merchant_ids)).all()
        merchant_cache = {m.id: m for m in merchant_records}
        
        # Get ChargerMerchant links for distance_to_charger_m (fallback if cached field not available)
        charger_merchant_links = db.query(ChargerMerchant).filter(
            ChargerMerchant.merchant_id.in_(merchant_ids)
        ).all()
        
        # Build map: merchant_id -> best (shortest) distance_to_charger_m (from ChargerMerchant, fallback)
        distance_map = {}
        for link in charger_merchant_links:
            merchant_id = link.merchant_id
            distance = link.distance_m
            if merchant_id not in distance_map or distance < distance_map[merchant_id]:
                distance_map[merchant_id] = distance
        
        # Get MerchantPerk to determine nova_accepted
        perks = db.query(MerchantPerk).filter(
            MerchantPerk.merchant_id.in_(merchant_ids),
            MerchantPerk.is_active == True
        ).all()
        
        # Build map: merchant_id -> (has_active_perk, nova_reward)
        perk_map = {}
        for perk in perks:
            merchant_id = perk.merchant_id
            if merchant_id not in perk_map or perk.nova_reward > perk_map[merchant_id][1]:
                perk_map[merchant_id] = (True, perk.nova_reward)
        
        # Enrich each merchant with cached fields, distance_to_charger_m, and nova_accepted
        enriched_merchants = []
        for merchant in recommended_merchants:
            merchant_id = merchant.get("id") or merchant.get("merchant_id")
            if not merchant_id:
                continue
            
            # Get cached fields from Merchant model (defensive - handle missing columns)
            merchant_record = merchant_cache.get(merchant_id)
            if merchant_record:
                try:
                    # Use cached nearest_charger_distance_m if available
                    if hasattr(merchant_record, 'nearest_charger_distance_m') and merchant_record.nearest_charger_distance_m is not None:
                        merchant["nearest_charger_distance_m"] = merchant_record.nearest_charger_distance_m
                        merchant["distance_to_charger_m"] = merchant_record.nearest_charger_distance_m
                    else:
                        # Fallback to ChargerMerchant distance
                        merchant["distance_to_charger_m"] = int(round(distance_map[merchant_id])) if merchant_id in distance_map else None
                        merchant["nearest_charger_distance_m"] = merchant["distance_to_charger_m"]
                    
                    # Add primary_category (defensive - handle missing column)
                    if hasattr(merchant_record, 'primary_category') and merchant_record.primary_category:
                        merchant["primary_category"] = merchant_record.primary_category
                    else:
                        merchant["primary_category"] = merchant.get("category", "other")
                except (AttributeError, KeyError) as e:
                    # Fallback if column doesn't exist in database
                    logger.warning(f"Merchant {merchant_id} missing schema columns, using fallbacks: {e}")
                    merchant["distance_to_charger_m"] = int(round(distance_map[merchant_id])) if merchant_id in distance_map else None
                    merchant["nearest_charger_distance_m"] = merchant["distance_to_charger_m"]
                    merchant["primary_category"] = merchant.get("category", "other")
            else:
                # Fallback if merchant record not found
                merchant["distance_to_charger_m"] = int(round(distance_map[merchant_id])) if merchant_id in distance_map else None
                merchant["nearest_charger_distance_m"] = merchant["distance_to_charger_m"]
                merchant["primary_category"] = merchant.get("category", "other")
            
            # Add nova_accepted
            has_perk, nova_reward = perk_map.get(merchant_id, (False, 0))
            merchant["nova_accepted"] = has_perk and nova_reward > 0
            
            # Ensure nova_reward is set (use from perk if available)
            if has_perk and nova_reward > 0:
                merchant["nova_reward"] = nova_reward
            
            enriched_merchants.append(merchant)
        
        # Apply filters
        filtered = []
        for merchant in enriched_merchants:
            # Filter by radius (distance from user)
            if lat and lng:
                from app.services.verify_dwell import haversine_m
                merchant_lat = merchant.get("lat")
                merchant_lng = merchant.get("lng")
                if merchant_lat and merchant_lng:
                    distance = haversine_m(lat, lng, merchant_lat, merchant_lng)
                    if distance > radius_m:
                        continue
                    merchant["distance_m"] = int(round(distance))
            
            # Filter by nova_only
            if nova_only and not merchant.get("nova_accepted", False):
                continue
            
            # Filter by name search (q)
            if q:
                merchant_name = merchant.get("name", "").lower()
                if q.lower() not in merchant_name:
                    continue
            
            # Filter by category (use primary_category if available, fallback to category)
            if category:
                merchant_primary_category = merchant.get("primary_category", "").lower()
                merchant_category = merchant.get("category", "").lower()
                category_match = merchant_primary_category == category.lower() or merchant_category == category.lower()
                if not category_match:
                    continue
            
            # Filter by while_you_charge (merchants within 805m of nearest charger)
            if while_you_charge:
                nearest_distance = merchant.get("nearest_charger_distance_m")
                if nearest_distance is None or nearest_distance > 805:
                    continue
            
            # Filter by max_distance_to_charger_m (backward compatibility)
            if max_distance_to_charger_m is not None:
                distance_to_charger = merchant.get("distance_to_charger_m") or merchant.get("nearest_charger_distance_m")
                if distance_to_charger is None or distance_to_charger > max_distance_to_charger_m:
                    continue
            
            filtered.append(merchant)
        
        # Sort by distance_to_charger_m ascending
        filtered.sort(key=lambda m: m.get("distance_to_charger_m") or float('inf'))
        
        # Add activation counts for each merchant
        from app.services.merchant_activation_counts import get_merchant_activation_counts
        for merchant in filtered:
            merchant_id = merchant.get("id") or merchant.get("merchant_id")
            if merchant_id:
                counts = get_merchant_activation_counts(db, merchant_id)
                merchant["activations_today"] = counts["activations_today"]
                merchant["verified_visits_today"] = counts["verified_visits_today"]
            else:
                merchant["activations_today"] = 0
                merchant["verified_visits_today"] = 0
        
        return filtered
    else:
        # For other zones, fall back to DomainMerchant query
        # TODO: Enrich with perks/logo/walk_time data
        merchants = db.query(DomainMerchant).filter(
            DomainMerchant.zone_slug == zone_slug,
            DomainMerchant.status == "active"
        ).all()
        
        nearby = []
        for merchant in merchants:
            distance = haversine_distance(lat, lng, merchant.lat, merchant.lng)
            if distance <= radius_m:
                address = merchant.addr_line1
                if merchant.city:
                    address = f"{address}, {merchant.city}, {merchant.state}" if address else f"{merchant.city}, {merchant.state}"
                
                # Add activation counts
                from app.services.merchant_activation_counts import get_merchant_activation_counts
                counts = get_merchant_activation_counts(db, merchant.id)
                
                nearby.append({
                    "id": merchant.id,
                    "merchant_id": merchant.id,
                    "name": merchant.name,
                    "lat": merchant.lat,
                    "lng": merchant.lng,
                    "zone_slug": merchant.zone_slug,
                    "address": address,
                    "phone": merchant.public_phone,
                    "nova_reward": 10,  # Default
                    "walk_time_s": 0,
                    "walk_time_seconds": 0,
                    "distance_m": int(round(distance)),
                    "activations_today": counts["activations_today"],
                    "verified_visits_today": counts["verified_visits_today"]
                })
        
        return nearby


@router.get("/merchants/open")
async def get_merchants_for_charger(
    charger_id: str = Query(..., description="Charger ID"),
    state: str = Query("charging", description="State: 'pre-charge' or 'charging'"),
    open_only: bool = Query(False, description="Filter to open merchants only"),
    user: Optional[User] = Depends(get_current_driver_optional),  # Optional auth - endpoint excluded from middleware
    db: Session = Depends(get_db)
):
    """
    Get merchants for a specific charger with primary override support.
    
    In pre-charge state: Returns only the primary merchant if override exists.
    In charging state: Returns primary merchant first, then secondary merchants (up to 3 total).
    """
    import logging

    from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
    from app.services.merchant_enrichment import enrich_from_google_places, format_open_until
    
    logger = logging.getLogger(__name__)
    user_id = user.id if user else "anonymous"
    logger.info(
        f"Merchants for charger requested: charger_id={charger_id}, state={state}, open_only={open_only}, user_id={user_id}"
    )
    
    # Load charger
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Charger '{charger_id}' not found"
        )
    
    # Check for primary merchant override
    primary_override = db.query(ChargerMerchant).filter(
        ChargerMerchant.charger_id == charger_id,
        ChargerMerchant.is_primary == True
    ).first()
    
    if primary_override:
        # Load primary merchant
        primary_merchant = db.query(Merchant).filter(Merchant.id == primary_override.merchant_id).first()
        if not primary_merchant:
            logger.warning(f"Primary override exists but merchant {primary_override.merchant_id} not found")
            return []
        
        # Enrich from Google Places if needed
        if primary_merchant.place_id:
            await enrich_from_google_places(db, primary_merchant, primary_merchant.place_id, force_refresh=False)
            db.refresh(primary_merchant)
        
        # Apply open_only filter (only filter if explicitly False, not if None/unknown)
        if open_only and primary_merchant.open_now is False:
            logger.info(f"Primary merchant {primary_merchant.id} filtered out (open_now=False, open_only=True)")
            return []
        
        # Build response for primary merchant
        # Calculate walk time if not set (approximate: 80m/min walking speed)
        walk_time_s = primary_override.walk_duration_s
        if not walk_time_s and primary_override.distance_m:
            walk_time_s = int(round(primary_override.distance_m / 80 * 60))
        elif not walk_time_s:
            walk_time_s = 180  # Default 3 minutes
        
        # Get activation counts
        from app.services.merchant_activation_counts import get_merchant_activation_counts
        counts = get_merchant_activation_counts(db, primary_merchant.id)
        
        response = {
            "id": primary_merchant.id,
            "merchant_id": primary_merchant.id,
            "place_id": primary_merchant.place_id or primary_merchant.id,  # Frontend expects place_id
            "name": primary_merchant.name,
            "lat": primary_merchant.lat,
            "lng": primary_merchant.lng,
            "address": primary_merchant.address,
            "phone": primary_merchant.phone,
            "logo_url": primary_merchant.primary_photo_url or primary_merchant.photo_url or primary_merchant.logo_url,
            "photo_url": primary_merchant.primary_photo_url or primary_merchant.photo_url or primary_merchant.logo_url,  # Also include photo_url for compatibility
            "photo_urls": primary_merchant.photo_urls or [],
            "category": primary_merchant.category or primary_merchant.primary_category,
            "types": [primary_merchant.category or primary_merchant.primary_category or "restaurant"] if primary_merchant.category or primary_merchant.primary_category else ["restaurant"],
            "is_primary": True,
            "exclusive_title": primary_override.exclusive_title,
            "exclusive_description": primary_override.exclusive_description,
            "open_now": primary_merchant.open_now if primary_merchant.open_now is not None else True,  # Default to True if not set
            "open_until": format_open_until(primary_merchant.hours_json) if primary_merchant.hours_json else None,
            "rating": primary_merchant.rating,
            "user_rating_count": primary_merchant.user_rating_count,
            "walk_time_s": walk_time_s,
            "walk_time_seconds": walk_time_s,
            "distance_m": int(round(primary_override.distance_m)) if primary_override.distance_m else 0,
            "activations_today": counts["activations_today"],
            "verified_visits_today": counts["verified_visits_today"],
        }
        
        # In pre-charge state with suppress_others, return only primary
        if state == "pre-charge" and primary_override.suppress_others:
            return [response]
        
        # In charging state, get secondary merchants (up to 2 more = 3 total)
        if state == "charging":
            # Get other merchants for this charger (non-primary)
            other_links = db.query(ChargerMerchant).filter(
                ChargerMerchant.charger_id == charger_id,
                ChargerMerchant.is_primary == False,
                ChargerMerchant.merchant_id != primary_merchant.id
            ).order_by(ChargerMerchant.distance_m.asc()).limit(2).all()
            
            results = [response]  # Start with primary
            
            for link in other_links:
                merchant = db.query(Merchant).filter(Merchant.id == link.merchant_id).first()
                if not merchant:
                    continue
                
                # Apply open_only filter
                if open_only:
                    # Refresh status if needed
                    if merchant.place_id:
                        from app.services.merchant_enrichment import refresh_open_status
                        await refresh_open_status(db, merchant, force_refresh=False)
                        db.refresh(merchant)
                    if merchant.open_now is False:
                        continue
                
                # Get activation counts
                counts = get_merchant_activation_counts(db, merchant.id)
                
                # Build secondary merchant response
                secondary_response = {
                    "id": merchant.id,
                    "merchant_id": merchant.id,
                    "name": merchant.name,
                    "lat": merchant.lat,
                    "lng": merchant.lng,
                    "address": merchant.address,
                    "phone": merchant.phone,
                    "logo_url": merchant.primary_photo_url or merchant.photo_url or merchant.logo_url,
                    "photo_urls": merchant.photo_urls or [],
                    "category": merchant.category or merchant.primary_category,
                    "is_primary": False,
                    "open_now": merchant.open_now,
                    "open_until": format_open_until(merchant.hours_json) if merchant.hours_json else None,
                    "rating": merchant.rating,
                    "user_rating_count": merchant.user_rating_count,
                    "walk_time_s": link.walk_duration_s,
                    "walk_time_seconds": link.walk_duration_s,
                    "distance_m": int(round(link.distance_m)),
                    "activations_today": counts["activations_today"],
                    "verified_visits_today": counts["verified_visits_today"],
                }
                results.append(secondary_response)
            
            return results
        
        # Default: return primary only
        return [response]
    
    # No primary override - use existing nearby merchants logic
    # For now, delegate to nearby endpoint logic but filter by charger
    # This is a simplified version - in production you might want to enhance this
    logger.info(f"No primary override for charger {charger_id}, using default merchant search")
    
    # Get merchants linked to this charger
    charger_merchant_links = db.query(ChargerMerchant).filter(
        ChargerMerchant.charger_id == charger_id
    ).order_by(ChargerMerchant.distance_m.asc()).limit(10).all()
    
    results = []
    for link in charger_merchant_links:
        merchant = db.query(Merchant).filter(Merchant.id == link.merchant_id).first()
        if not merchant:
            continue
        
        # Apply open_only filter
        if open_only:
            if merchant.place_id:
                from app.services.merchant_enrichment import refresh_open_status
                await refresh_open_status(db, merchant, force_refresh=False)
                db.refresh(merchant)
            if merchant.open_now is False:
                continue
        
        # Get activation counts
        from app.services.merchant_activation_counts import get_merchant_activation_counts
        counts = get_merchant_activation_counts(db, merchant.id)
        
        result = {
            "id": merchant.id,
            "merchant_id": merchant.id,
            "name": merchant.name,
            "lat": merchant.lat,
            "lng": merchant.lng,
            "address": merchant.address,
            "phone": merchant.phone,
            "logo_url": merchant.primary_photo_url or merchant.photo_url or merchant.logo_url,
            "photo_urls": merchant.photo_urls or [],
            "category": merchant.category or merchant.primary_category,
            "is_primary": False,
            "open_now": merchant.open_now,
            "open_until": format_open_until(merchant.hours_json) if merchant.hours_json else None,
            "rating": merchant.rating,
            "user_rating_count": merchant.user_rating_count,
            "walk_time_s": link.walk_duration_s,
            "walk_time_seconds": link.walk_duration_s,
            "distance_m": int(round(link.distance_m)),
            "activations_today": counts["activations_today"],
            "verified_visits_today": counts["verified_visits_today"],
        }
        results.append(result)
    
    # Limit to 3 for charging state, 1 for pre-charge
    if state == "pre-charge":
        return results[:1]
    else:
        return results[:3]


@router.post("/nova/redeem", response_model=RedeemNovaResponse)
def redeem_nova(
    request: RedeemNovaRequest,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """Redeem Nova from driver to merchant"""
    
    # Require idempotency key in non-local environments
    from app.core.env import is_local_env
    
    idempotency_key = request.idempotency_key if hasattr(request, 'idempotency_key') else None
    if not idempotency_key:
        if not is_local_env():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="idempotency_key is required in non-local environment"
            )
        # In local, generate deterministic idempotency key from request (dev only)
        idempotency_key = f"redeem_{user.id}_{request.merchant_id}_{request.amount}_{request.session_id or 'none'}"
    
    try:
        result = NovaService.redeem_from_driver(
            db=db,
            driver_id=user.id,
            merchant_id=request.merchant_id,
            amount=request.amount,
            session_id=request.session_id,
            idempotency_key=idempotency_key
        )
        return RedeemNovaResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Redemption failed: {str(e)}"
        )


@router.get("/me/wallet")
def get_driver_wallet(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """Get driver wallet balance"""
    wallet = NovaService.get_driver_wallet(db, user.id)
    return {
        "nova_balance": wallet.nova_balance,
        "energy_reputation_score": wallet.energy_reputation_score
    }


@router.get("/me/wallet/summary")
def get_driver_wallet_summary(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get comprehensive wallet summary for driver.
    
    Returns all wallet data needed by the UI including balance, conversion rate,
    charging status, off-peak state, reputation tier, and recent activity.
    """
    # Get driver wallet and refresh to ensure we have latest data
    wallet = NovaService.get_driver_wallet(db, user.id)
    db.refresh(wallet)  # Ensure we get the latest reputation score and balance
    
    # Get conversion rate from settings
    conversion_rate_cents = settings.NOVA_TO_USD_CONVERSION_RATE_CENTS
    
    # Calculate USD equivalent
    nova_balance = wallet.nova_balance or 0
    nova_balance_cents = nova_balance * conversion_rate_cents
    usd_cents = nova_balance_cents
    usd_dollars = usd_cents / 100.0
    usd_equivalent = f"${usd_dollars:.2f}"
    
    # Get charging detected status
    charging_detected = wallet.charging_detected or False
    
    # Get timezone (check user preferences if available, else use default)
    tz_str = settings.DEFAULT_TIMEZONE
    # TODO: Check user.preferences.timezone if field exists in future
    tz = ZoneInfo(tz_str)
    
    # Calculate off-peak state
    now = datetime.now(tz)
    offpeak_active, window_ends_in_seconds = get_offpeak_state(now, tz)
    
    # Calculate reputation tier using service (with error handling)
    import logging

    from app.services.reputation import compute_reputation
    logger = logging.getLogger(__name__)
    reputation_score = wallet.energy_reputation_score or 0
    try:
        reputation = compute_reputation(reputation_score)
    except Exception as e:
        logger.error(f"Error computing reputation for user {user.id}: {e}", exc_info=True)
        # Fallback to Bronze tier (0 points)
        reputation = compute_reputation(0)
    
    # Get recent activity (last 5 items)
    activities = []
    
    # Aggregate charging sessions by day
    # Query: Group sessions by date and sum Nova earned
    # SQLite: Use DATE() function to extract date from start_time
    # PostgreSQL: Use DATE() or CAST(... AS DATE)
    daily_sessions = db.query(
        func.date(DomainChargingSession.start_time).label('session_date'),
        func.count(DomainChargingSession.id).label('session_count'),
        func.max(DomainChargingSession.start_time).label('latest_time')
    ).filter(
        DomainChargingSession.driver_user_id == user.id,
        DomainChargingSession.start_time.isnot(None)
    ).group_by(
        func.date(DomainChargingSession.start_time)
    ).order_by(
        func.date(DomainChargingSession.start_time).desc()
    ).limit(5).all()
    
    # For each day, calculate total Nova earned from related transactions
    for day_result in daily_sessions:
        session_date_raw = day_result.session_date
        session_count = day_result.session_count
        latest_time = day_result.latest_time

        # SQLite returns dates as strings (YYYY-MM-DD), PostgreSQL returns datetime.date
        # Normalize to datetime.date for datetime.combine()
        if isinstance(session_date_raw, str):
            from datetime import date as date_type
            session_date = date_type.fromisoformat(session_date_raw)
        else:
            session_date = session_date_raw

        # Get all sessions for this day
        day_start = datetime.combine(session_date, datetime.min.time())
        day_end = datetime.combine(session_date, datetime.max.time())
        
        day_sessions = db.query(DomainChargingSession.id).filter(
            DomainChargingSession.driver_user_id == user.id,
            DomainChargingSession.start_time >= day_start,
            DomainChargingSession.start_time <= day_end
        ).all()
        
        session_ids = [s.id for s in day_sessions]
        
        # Sum Nova earned from transactions linked to these sessions
        # Handle missing payload_hash column gracefully
        try:
            total_nova = db.query(
                func.sum(NovaTransaction.amount)
            ).filter(
                NovaTransaction.session_id.in_(session_ids),
                NovaTransaction.type == 'driver_earn',
                NovaTransaction.driver_user_id == user.id
            ).scalar() or 0
        except Exception as e:
            if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                # Column doesn't exist - use raw SQL
                from sqlalchemy import text
                if session_ids:
                    placeholders = ','.join([':id' + str(i) for i in range(len(session_ids))])
                    params = {f'id{i}': sid for i, sid in enumerate(session_ids)}
                    params['user_id'] = user.id
                    result = db.execute(text(f"""
                        SELECT COALESCE(SUM(amount), 0)
                        FROM nova_transactions
                        WHERE session_id IN ({placeholders})
                        AND type = 'driver_earn'
                        AND driver_user_id = :user_id
                    """), params)
                    total_nova = result.scalar() or 0
                else:
                    total_nova = 0
            else:
                raise
        
        # Fallback: If no Nova transactions found but sessions exist, estimate Nova earned
        # This handles cases where sessions exist but Nova wasn't granted yet (demo/legacy data)
        # NOTE: This is a demo fallback - in production, Nova should always be granted via transactions
        if total_nova == 0 and session_count > 0:
            # For demo purposes, estimate Nova based on session count
            # Use 10 Nova per session (10 Nova = $1.00 at 10¢/Nova conversion rate)
            # This ensures the UI shows a non-zero amount for demo data
            total_nova = session_count * 10  # 10 Nova per session as demo fallback
        
        # Build aggregated activity item
        activities.append({
            "id": f"charging_day_{session_date.isoformat()}",
            "type": "charging_session",
            "aggregation": "daily",
            "session_date": session_date.isoformat(),
            "session_count": int(session_count),
            "nova_earned": int(total_nova),
            "amount_cents": int(total_nova * conversion_rate_cents),
            "created_at": latest_time.isoformat() if latest_time else None,
            # Note: is_off_peak omitted - do not infer off-peak status per day
        })
    
    # Aggregate Nova earned transactions (driver_earn without session_id) by day
    # Query: Group driver_earn transactions by date and sum amounts
    try:
        daily_nova_earned = db.query(
            func.date(NovaTransaction.created_at).label('transaction_date'),
            func.sum(NovaTransaction.amount).label('total_amount'),
            func.count(NovaTransaction.id).label('transaction_count'),
            func.max(NovaTransaction.created_at).label('latest_time')
        ).filter(
            NovaTransaction.driver_user_id == user.id,
            NovaTransaction.type == 'driver_earn',
            NovaTransaction.session_id.is_(None)  # Only transactions without session_id (not from charging sessions)
        ).group_by(
            func.date(NovaTransaction.created_at)
        ).order_by(
            func.date(NovaTransaction.created_at).desc()
        ).limit(5).all()
    except Exception as e:
        if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
            # Column doesn't exist - use raw SQL
            from sqlalchemy import text
            result = db.execute(text("""
                SELECT DATE(created_at) as transaction_date,
                       SUM(amount) as total_amount,
                       COUNT(id) as transaction_count,
                       MAX(created_at) as latest_time
                FROM nova_transactions
                WHERE driver_user_id = :user_id 
                AND type = 'driver_earn'
                AND session_id IS NULL
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at) DESC
                LIMIT 5
            """), {"user_id": user.id})
            rows = result.fetchall()
            daily_nova_earned = []
            for row in rows:
                daily_nova_earned.append({
                    "transaction_date": row[0],
                    "total_amount": row[1] or 0,
                    "transaction_count": row[2] or 0,
                    "latest_time": row[3]
                })
        else:
            raise
    
    # Add aggregated daily Nova earned transactions
    for day_result in daily_nova_earned:
        if isinstance(day_result, dict):
            transaction_date_raw = day_result.get("transaction_date")
            total_amount = day_result.get("total_amount") or 0
            transaction_count = day_result.get("transaction_count") or 0
            latest_time = day_result.get("latest_time")
        else:
            transaction_date_raw = day_result.transaction_date
            total_amount = day_result.total_amount or 0
            transaction_count = day_result.transaction_count or 0
            latest_time = day_result.latest_time
        
        # Normalize date
        if isinstance(transaction_date_raw, str):
            from datetime import date as date_type
            transaction_date = date_type.fromisoformat(transaction_date_raw)
        else:
            transaction_date = transaction_date_raw
        
        activities.append({
            "id": f"nova_earned_day_{transaction_date.isoformat()}",
            "type": "nova_transaction",
            "transaction_type": "driver_earn",
            "aggregation": "daily",
            "transaction_date": transaction_date.isoformat(),
            "transaction_count": int(transaction_count),
            "amount": int(total_amount),
            "amount_cents": int(total_amount * conversion_rate_cents),
            "created_at": latest_time.isoformat() if latest_time and hasattr(latest_time, 'isoformat') else (latest_time if latest_time else None)
        })
    
    # Get other Nova transactions (redemptions, grants, etc.) - exclude driver_earn (already aggregated above)
    # Use raw SQL to avoid selecting payload_hash if column doesn't exist
    try:
        # Try querying with payload_hash first (if column exists)
        nova_transactions = db.query(NovaTransaction).filter(
            NovaTransaction.driver_user_id == user.id,
            NovaTransaction.type != 'driver_earn'  # Exclude all driver_earn (now aggregated by day)
        ).order_by(NovaTransaction.created_at.desc()).limit(5).all()
    except Exception as e:
        if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
            # Column doesn't exist - query without selecting payload_hash explicitly
            # Use raw SQL to select only columns that exist
            from sqlalchemy import text
            result = db.execute(text("""
                SELECT id, type, driver_user_id, merchant_id, amount, stripe_payment_id, 
                       session_id, event_id, metadata, idempotency_key, created_at
                FROM nova_transactions
                WHERE driver_user_id = :user_id 
                AND type != 'driver_earn'
                ORDER BY created_at DESC
                LIMIT 5
            """), {"user_id": user.id})
            rows = result.fetchall()
            # Convert to dict format for compatibility
            nova_transactions = []
            for row in rows:
                nova_transactions.append({
                    "id": row[0],
                    "type": row[1],
                    "driver_user_id": row[2],
                    "merchant_id": row[3],
                    "amount": row[4],
                    "stripe_payment_id": row[5],
                    "session_id": row[6],
                    "event_id": row[7],
                    "transaction_meta": row[8] if row[8] else {},
                    "idempotency_key": row[9],
                    "created_at": row[10]
                })
        else:
            raise
    
    for tx in nova_transactions:
        # Handle both ORM objects and dicts
        if isinstance(tx, dict):
            tx_id = tx.get("id")
            tx_type = tx.get("type")
            tx_amount = tx.get("amount")
            tx_created_at = tx.get("created_at")
            tx_merchant_id = tx.get("merchant_id")
        else:
            tx_id = tx.id
            tx_type = tx.type
            tx_amount = tx.amount
            tx_created_at = tx.created_at
            tx_merchant_id = tx.merchant_id
        
        activities.append({
            "id": tx_id,
            "type": "nova_transaction",
            "transaction_type": tx_type,
            "amount": tx_amount,
            "amount_cents": tx_amount * conversion_rate_cents if tx_type in ['driver_earn', 'admin_grant'] else -(tx_amount * conversion_rate_cents),
            "created_at": tx_created_at.isoformat() if hasattr(tx_created_at, 'isoformat') and tx_created_at else (tx_created_at if tx_created_at else None),
            "merchant_id": tx_merchant_id
        })
    
    # Get wallet transactions if CreditLedger table exists
    try:
        from app.models_extra import CreditLedger
        transactions = db.query(CreditLedger).filter(
            CreditLedger.user_ref == str(user.id)
        ).order_by(CreditLedger.id.desc()).limit(5).all()
        
        for tx in transactions:
            activities.append({
                "id": str(tx.id),
                "type": "wallet_transaction",
                "amount_cents": tx.cents,
                "reason": tx.reason,
                "created_at": tx.created_at.isoformat() if hasattr(tx, 'created_at') and tx.created_at else None,
                "meta": tx.meta if hasattr(tx, 'meta') else {}
            })
    except Exception:
        # CreditLedger table might not exist - skip wallet transactions
        pass
    
    # Sort by most recent and limit to 5
    activities.sort(key=lambda x: (
        x.get("start_time") or x.get("created_at") or "1970-01-01"
    ), reverse=True)
    recent_activity = activities[:5]
    
    return {
        "nova_balance": nova_balance,
        "nova_balance_cents": nova_balance_cents,
        "conversion_rate_cents": conversion_rate_cents,
        "usd_equivalent": usd_equivalent,
        "charging_detected": charging_detected,
        "offpeak_active": offpeak_active,
        "window_ends_in_seconds": window_ends_in_seconds,
        "reputation": reputation,
        "recent_activity": recent_activity,
        "last_updated_at": datetime.utcnow().isoformat() + "Z"
    }


@router.get("/activity")
def get_driver_activity(
    limit: int = Query(50, ge=1, le=100, description="Number of activities to return"),
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get driver activity/transactions.
    
    Returns recent charging sessions and wallet transactions for the driver.
    """
    activities = []
    
    # Get recent charging sessions
    sessions = db.query(DomainChargingSession).filter(
        DomainChargingSession.driver_user_id == user.id
    ).order_by(DomainChargingSession.start_time.desc()).limit(limit).all()
    
    for session in sessions:
        activities.append({
            "id": session.id,
            "type": "charging_session",
            "status": "verified" if session.verified else "pending",
            "kwh": session.kwh_estimate,
            "start_time": session.start_time.isoformat() if session.start_time else None,
            "end_time": session.end_time.isoformat() if session.end_time else None,
            "verified": session.verified,
            "verification_source": session.verification_source
        })
    
    # Get wallet transactions if CreditLedger table exists
    try:
        from app.models_extra import CreditLedger
        transactions = db.query(CreditLedger).filter(
            CreditLedger.user_ref == user.id
        ).order_by(CreditLedger.id.desc()).limit(limit).all()
        
        for tx in transactions:
            activities.append({
                "id": str(tx.id),
                "type": "wallet_transaction",
                "amount_cents": tx.cents,
                "reason": tx.reason,
                "created_at": tx.created_at.isoformat() if hasattr(tx, 'created_at') and tx.created_at else None,
                "meta": tx.meta if hasattr(tx, 'meta') else {}
            })
    except Exception:
        # CreditLedger table might not exist - skip wallet transactions
        pass
    
    # Sort by most recent (by timestamp if available)
    activities.sort(key=lambda x: (
        x.get("start_time") or x.get("created_at") or "1970-01-01"
    ), reverse=True)
    
    # Return only the requested limit
    return activities[:limit]


# Session ping/cancel endpoints
class SessionPingRequest(BaseModel):
    lat: float
    lng: float


class SessionPingResponse(BaseModel):
    verified: bool
    reward_earned: bool
    verified_at_charger: bool
    ready_to_claim: bool
    nova_awarded: int = 0
    wallet_balance_nova: int = 0
    distance_to_charger_m: int = 0
    dwell_seconds: int = 0
    needed_seconds: Optional[int] = None
    charger_radius_m: Optional[int] = None
    distance_to_merchant_m: Optional[int] = None
    within_merchant_radius: Optional[bool] = None
    verification_score: Optional[int] = None


@router.post("/sessions/{session_id}/ping", response_model=SessionPingResponse)
def ping_session_v1(
    session_id: str,
    payload: SessionPingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_driver),
):
    """
    Ping a session to update location and verification status.
    
    Canonical v1 endpoint - replaces /v1/pilot/verify_ping
    """
    from app.services.session_service import SessionService
    
    result = SessionService.ping_session(
        db=db,
        session_id=session_id,
        driver_user_id=current_user.id,
        lat=payload.lat,
        lng=payload.lng,
        accuracy_m=50.0  # Default accuracy
    )
    
    return SessionPingResponse(**result)


@router.post("/sessions/{session_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_session_v1(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_driver),
):
    """
    Cancel a charging session.
    
    Canonical v1 endpoint - replaces /v1/pilot/session/cancel
    """
    from app.services.session_service import SessionService
    
    SessionService.cancel_session(
        db=db,
        session_id=session_id,
        driver_user_id=current_user.id
    )
    
    return None  # 204 No Content


# Location check endpoint
class LocationCheckResponse(BaseModel):
    in_charger_radius: bool
    nearest_charger_id: Optional[str] = None
    distance_m: Optional[float] = None


@router.get("/location/check", response_model=LocationCheckResponse)
def check_location(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_driver),
):
    """
    Check if driver is within charger radius.
    Returns charger proximity information.
    Supports demo static driver mode via DEMO_STATIC_DRIVER_ENABLED.
    """
    import logging
    import os

    from app.models_while_you_charge import Charger
    logger = logging.getLogger(__name__)

    # Check for demo static driver mode
    demo_enabled = os.getenv("DEMO_STATIC_DRIVER_ENABLED", "false").lower() == "true"
    if demo_enabled:
        # In demo mode, check if admin has set a static location
        # For now, return a mock response indicating user is at a charger
        demo_charger_id = os.getenv("DEMO_STATIC_CHARGER_ID", "demo_charger_1")
        logger.info(f"[Driver][Location] Demo mode: returning static charger {demo_charger_id}")
        return LocationCheckResponse(
            in_charger_radius=True,
            nearest_charger_id=demo_charger_id,
            distance_m=0.0
        )

    # Check TTLCache — round to 4 decimal places (~11m) for cache key
    rounded_lat = round(lat, 4)
    rounded_lng = round(lng, 4)
    cache_key = f"{rounded_lat},{rounded_lng}"

    cached = _location_check_cache.get(cache_key)
    if cached is not None:
        logger.debug(f"[Driver][Location] Cache hit for {cache_key}")
        return cached

    # Find nearest charger
    CHARGER_RADIUS_M = 150  # Same as exclusive activation radius

    # Import haversine function
    from app.services.verify_dwell import haversine_m

    # Query chargers and calculate distance
    chargers = db.query(Charger).all()

    nearest_charger = None
    min_distance = float('inf')

    for charger in chargers:
        if charger.lat and charger.lng:
            distance = haversine_m(lat, lng, charger.lat, charger.lng)
            if distance < min_distance:
                min_distance = distance
                nearest_charger = charger

    in_radius = nearest_charger is not None and min_distance <= CHARGER_RADIUS_M

    logger.info(
        f"[Driver][Location] Check: lat={lat}, lng={lng}, "
        f"in_radius={in_radius}, distance={min_distance:.1f}m, "
        f"charger_id={nearest_charger.id if nearest_charger else None}"
    )

    response = LocationCheckResponse(
        in_charger_radius=in_radius,
        nearest_charger_id=str(nearest_charger.id) if nearest_charger else None,
        distance_m=min_distance if nearest_charger else None
    )

    # Store in cache
    _location_check_cache[cache_key] = response

    return response

