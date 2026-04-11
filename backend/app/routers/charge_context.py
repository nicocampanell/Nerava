"""
Charge Context Router — /v1/charge-context/nearby

Replaces the old /v1/intent/capture endpoint.
Returns nearby merchants for a given charging location.
No "intent" language — this is purely context for what's near the charger.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies.driver import get_current_driver_optional
from app.models import User
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.analytics import get_analytics_client
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/charge-context", tags=["charge-context"])


class NearbyCharger(BaseModel):
    charger_id: str
    name: str
    network: Optional[str] = None
    lat: float
    lng: float
    distance_m: float
    open_stalls: Optional[int] = None


class NearbyMerchant(BaseModel):
    merchant_id: str
    name: str
    category: Optional[str] = None
    lat: float
    lng: float
    address: Optional[str] = None
    photo_url: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    open_now: Optional[bool] = None
    walk_minutes: Optional[int] = None
    distance_m: Optional[float] = None
    ordering_url: Optional[str] = None
    verified_visit_count: int = 0
    active_arrival_count: int = 0


class NearbyResponse(BaseModel):
    charger: Optional[NearbyCharger] = None
    merchants: List[NearbyMerchant]
    total: int


@router.get("/nearby", response_model=NearbyResponse)
async def get_nearby_context(
    lat: float = Query(..., description="Driver latitude"),
    lng: float = Query(..., description="Driver longitude"),
    accuracy_m: Optional[float] = Query(None),
    category: Optional[str] = Query(None, description="Filter by category"),
    driver: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Get nearby chargers and merchants for a driver's location.
    This replaces the old /v1/intent/capture endpoint.
    """
    search_radius_m = float(settings.GOOGLE_PLACES_SEARCH_RADIUS_M)

    # Find nearest charger
    chargers = db.query(Charger).all()
    nearest_charger = None
    nearest_distance = float("inf")

    for ch in chargers:
        dist = haversine_m(lat, lng, ch.lat, ch.lng)
        if dist < nearest_distance and dist <= search_radius_m:
            nearest_distance = dist
            nearest_charger = ch

    charger_response = None
    if nearest_charger:
        charger_response = NearbyCharger(
            charger_id=nearest_charger.id,
            name=nearest_charger.name,
            network=nearest_charger.network_name,
            lat=nearest_charger.lat,
            lng=nearest_charger.lng,
            distance_m=round(nearest_distance, 1),
        )

    # Find merchants near the charger (or near driver if no charger found)
    merchants_query = db.query(Merchant)
    if category:
        merchants_query = merchants_query.filter(
            (Merchant.category == category) | (Merchant.primary_category == category)
        )

    merchants = merchants_query.all()
    nearby_merchants = []

    ref_lat = nearest_charger.lat if nearest_charger else lat
    ref_lng = nearest_charger.lng if nearest_charger else lng

    for m in merchants:
        dist = haversine_m(ref_lat, ref_lng, m.lat, m.lng)
        if dist <= search_radius_m:
            # Get walk time from ChargerMerchant if available
            walk_minutes = None
            if nearest_charger:
                cm = (
                    db.query(ChargerMerchant)
                    .filter(
                        ChargerMerchant.charger_id == nearest_charger.id,
                        ChargerMerchant.merchant_id == m.id,
                    )
                    .first()
                )
                if cm:
                    walk_minutes = cm.walk_duration_s // 60 if cm.walk_duration_s else None

            # Count active arrivals for social proof
            from app.models.arrival_session import ACTIVE_STATUSES, ArrivalSession
            active_count = (
                db.query(ArrivalSession)
                .filter(
                    ArrivalSession.merchant_id == m.id,
                    ArrivalSession.status.in_(ACTIVE_STATUSES),
                )
                .count()
            )

            # Count verified visits
            from app.models.verified_visit import VerifiedVisit
            visit_count = (
                db.query(VerifiedVisit)
                .filter(VerifiedVisit.merchant_id == m.id)
                .count()
            )

            nearby_merchants.append(NearbyMerchant(
                merchant_id=m.id,
                name=m.name,
                category=m.category or m.primary_category,
                lat=m.lat,
                lng=m.lng,
                address=m.address,
                photo_url=m.photo_url or m.primary_photo_url,
                rating=m.rating,
                user_rating_count=m.user_rating_count,
                open_now=m.open_now,
                walk_minutes=walk_minutes,
                distance_m=round(dist, 1),
                ordering_url=getattr(m, "ordering_url", None),
                verified_visit_count=visit_count,
                active_arrival_count=active_count,
            ))

    # Sort by distance
    nearby_merchants.sort(key=lambda m: m.distance_m or 0)

    # Analytics
    if driver:
        try:
            analytics = get_analytics_client()
            if analytics:
                analytics.capture(
                    distinct_id=str(driver.id),
                    event="charge_context.nearby",
                    properties={
                        "lat": lat,
                        "lng": lng,
                        "charger_id": nearest_charger.id if nearest_charger else None,
                        "merchant_count": len(nearby_merchants),
                        "category_filter": category,
                    },
                )
        except Exception:
            pass

    return NearbyResponse(
        charger=charger_response,
        merchants=nearby_merchants,
        total=len(nearby_merchants),
    )
