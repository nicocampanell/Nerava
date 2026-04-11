"""
EV Context Router — /v1/ev-context

Detects EV browser and returns context-aware merchant recommendations.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies.driver import get_current_driver_optional
from app.models import User
from app.models.while_you_charge import Charger, Merchant
from app.services.analytics import get_analytics_client
from app.services.geo import haversine_m
from app.utils.ev_browser import EVBrowserInfo, detect_ev_browser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/ev-context", tags=["ev-context"])

CHARGER_MATCH_RADIUS_M = 150  # Must be within 150m to count as "at charger"


class EVContextRequest(BaseModel):
    lat: float
    lng: float
    accuracy_m: Optional[float] = None


class ChargerInfo(BaseModel):
    id: str
    name: str
    network: Optional[str] = None
    address: Optional[str] = None
    stall_count: Optional[int] = None


class MerchantInfo(BaseModel):
    id: str
    name: str
    category: Optional[str] = None
    rating: Optional[float] = None
    photo_url: Optional[str] = None
    distance_m: int
    walk_minutes: int
    ordering_url: Optional[str] = None


class EVContextResponse(BaseModel):
    # Browser detection
    is_ev_browser: bool
    ev_brand: Optional[str] = None
    ev_firmware: Optional[str] = None

    # Location context
    at_charger: bool
    charger: Optional[ChargerInfo] = None

    # Recommendations
    nearby_merchants: List[MerchantInfo]

    # Fulfillment options (both are Ready on Arrival)
    fulfillment_options: List[str]  # ['ev_dine_in', 'ev_curbside']

    # Vehicle setup needed
    vehicle_setup_needed: bool = False

    # Virtual Key status
    virtual_key_status: str = "none"  # 'none', 'pending', 'paired', 'active'
    virtual_key_id: Optional[str] = None
    arrival_tracking_enabled: bool = False
    show_virtual_key_prompt: bool = False


@router.post("", response_model=EVContextResponse)
async def get_ev_context(
    req: EVContextRequest,
    request: Request,
    driver: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Get EV-aware context for ordering.

    Detects:
    1. Is this an EV in-car browser? (Tesla, Polestar, etc.)
    2. Is the driver at a charger?
    3. What merchants are nearby?

    Returns optimized flow suggestion.
    """
    # Detect EV browser from User-Agent
    user_agent = request.headers.get("User-Agent", "")
    ev_info = detect_ev_browser(user_agent)

    # Find nearest charger
    charger = _find_nearest_charger(db, req.lat, req.lng)
    at_charger = charger is not None

    # Get nearby merchants (relative to charger if at one)
    anchor_lat = charger.lat if charger else req.lat
    anchor_lng = charger.lng if charger else req.lng
    merchants = _get_nearby_merchants(db, anchor_lat, anchor_lng)

    # Determine fulfillment options
    # Both are "Ready on Arrival" — difference is WHERE you eat
    if ev_info.is_ev_browser and at_charger:
        fulfillment_options = ["ev_dine_in", "ev_curbside"]
    else:
        fulfillment_options = ["standard"]

    # Check if vehicle setup needed (for authenticated users)
    vehicle_setup_needed = False
    if driver and ev_info.is_ev_browser:
        vehicle_setup_needed = not bool(getattr(driver, 'vehicle_color', None))

    # Get Virtual Key status (if feature enabled and user authenticated)
    virtual_key_status = "none"
    virtual_key_id = None
    arrival_tracking_enabled = False
    show_virtual_key_prompt = False
    
    if driver and ev_info.is_ev_browser and settings.FEATURE_VIRTUAL_KEY_ENABLED:
        try:
            from app.services.virtual_key_service import get_virtual_key_service
            service = get_virtual_key_service()
            active_key = await service.get_active_virtual_key(db, driver.id)
            
            if active_key:
                virtual_key_status = active_key.status
                virtual_key_id = str(active_key.id)
                arrival_tracking_enabled = active_key.status == 'active'
            else:
                # Check if user has any pending/paired keys
                all_keys = await service.get_user_virtual_keys(db, driver.id)
                if all_keys:
                    # User has keys but none active
                    latest_key = all_keys[0]  # Already sorted by created_at desc
                    virtual_key_status = latest_key.status
                    virtual_key_id = str(latest_key.id)
                else:
                    # First-time user - show prompt
                    show_virtual_key_prompt = True
        except Exception as e:
            logger.warning(f"Error checking Virtual Key status: {e}")

    # Track analytics
    _capture_ev_context_event(driver, ev_info, at_charger, charger)

    return EVContextResponse(
        is_ev_browser=ev_info.is_ev_browser,
        ev_brand=ev_info.brand,
        ev_firmware=ev_info.firmware_version,
        at_charger=at_charger,
        charger=ChargerInfo(
            id=charger.id,
            name=charger.name,
            network=getattr(charger, 'network_name', None),
            address=getattr(charger, 'address', None),
            stall_count=None,  # Not available in current model
        ) if charger else None,
        nearby_merchants=merchants,
        fulfillment_options=fulfillment_options,
        vehicle_setup_needed=vehicle_setup_needed,
        virtual_key_status=virtual_key_status,
        virtual_key_id=virtual_key_id,
        arrival_tracking_enabled=arrival_tracking_enabled,
        show_virtual_key_prompt=show_virtual_key_prompt,
    )


def _find_nearest_charger(db: Session, lat: float, lng: float) -> Optional[Charger]:
    """Find charger within CHARGER_MATCH_RADIUS_M of location."""
    chargers = db.query(Charger).all()

    for charger in chargers:
        distance = haversine_m(lat, lng, charger.lat, charger.lng)
        if distance <= CHARGER_MATCH_RADIUS_M:
            return charger

    return None


def _get_nearby_merchants(
    db: Session,
    lat: float,
    lng: float,
    limit: int = 10
) -> List[MerchantInfo]:
    """Get merchants near location, sorted by distance."""
    merchants = db.query(Merchant).filter(Merchant.is_active == True).all()

    results = []
    for merchant in merchants:
        distance = haversine_m(lat, lng, merchant.lat, merchant.lng)
        if distance <= 2000:  # Within 2km
            walk_minutes = max(1, int(distance / 80))  # ~80m/min walking
            results.append(MerchantInfo(
                id=merchant.id,
                name=merchant.name,
                category=getattr(merchant, 'category', None),
                rating=getattr(merchant, 'rating', None),
                photo_url=getattr(merchant, 'photo_url', None) or getattr(merchant, 'primary_photo_url', None),
                distance_m=int(distance),
                walk_minutes=walk_minutes,
                ordering_url=getattr(merchant, 'ordering_url', None),
            ))

    # Sort by distance
    results.sort(key=lambda m: m.distance_m)
    return results[:limit]


def _capture_ev_context_event(
    driver: Optional[User],
    ev_info: EVBrowserInfo,
    at_charger: bool,
    charger: Optional[Charger],
):
    """Track EV context analytics."""
    try:
        analytics = get_analytics_client()
        if analytics:
            analytics.capture(
                distinct_id=str(driver.id) if driver else "anonymous",
                event="ev_context.loaded",
                properties={
                    "is_ev_browser": ev_info.is_ev_browser,
                    "ev_brand": ev_info.brand,
                    "at_charger": at_charger,
                    "charger_id": charger.id if charger else None,
                },
            )
    except Exception as e:
        logger.warning(f"Analytics failed: {e}")
