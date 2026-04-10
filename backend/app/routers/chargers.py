# app/routers/chargers.py
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.dependencies.driver import get_current_driver
from app.models import User
from app.models.campaign import Campaign
from app.models.favorite_charger import FavoriteCharger
from app.models.session_event import SessionEvent
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.chargers_openmap import fetch_chargers
from app.services.google_places_new import _haversine_distance

logger = logging.getLogger(__name__)

router = APIRouter()  # main.py mounts with prefix="/v1/chargers"


def _network_matches(charger_network: str, rule_networks: list) -> bool:
    """Fuzzy network matching — 'Tesla Supercharger' matches 'Tesla Destination' etc."""
    if not charger_network or not rule_networks:
        return False
    cn = charger_network.lower()
    cn_first = cn.split()[0]
    for rn in rule_networks:
        rn_lower = rn.lower()
        if cn == rn_lower or cn.startswith(rn_lower) or rn_lower.startswith(cn):
            return True
        if cn_first == rn_lower.split()[0]:
            return True
    return False


def _match_campaign_reward(campaign, charger_id: str, charger_network: str) -> bool:
    """Check if a campaign matches a charger. Returns True if matched."""
    # Check charger ID rule
    rule_ids = campaign.rule_charger_ids
    if rule_ids:
        ids_list = rule_ids if isinstance(rule_ids, list) else json.loads(rule_ids) if isinstance(rule_ids, str) else []
        if ids_list and charger_id not in ids_list:
            return False

    # Check network rule
    rule_networks = campaign.rule_charger_networks
    if rule_networks:
        nets_list = rule_networks if isinstance(rule_networks, list) else json.loads(rule_networks) if isinstance(rule_networks, str) else []
        if nets_list and not _network_matches(charger_network, nets_list):
            return False

    return True


def _get_reward_for_charger(campaigns, charger_id: str, charger_network: str) -> Optional[int]:
    """Get the best campaign reward for a charger. Returns cents or None."""
    now = datetime.utcnow()
    for campaign in campaigns:
        if campaign.end_date and campaign.end_date < now:
            continue
        if _match_campaign_reward(campaign, charger_id, charger_network or ""):
            return campaign.cost_per_session_cents
    return None


# ==================== Geo Helpers ====================

def _bounding_box(lat: float, lng: float, radius_km: float):
    """Returns (south, north, west, east) using lat/lng degree approximation."""
    lat_delta = radius_km / 111.0
    lng_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta)


def _query_nearby_chargers(db, lat: float, lng: float, radius_km: float = 50.0, max_results: int = 100):
    """SQL bounding box pre-filter + Python haversine on small set.
    Uses existing composite index idx_chargers_location on (lat, lng)."""
    south, north, west, east = _bounding_box(lat, lng, radius_km)
    chargers = db.query(Charger).filter(
        Charger.lat.between(south, north),
        Charger.lng.between(west, east),
    ).all()
    results = [(c, _haversine_distance(lat, lng, c.lat, c.lng))
               for c in chargers]
    results = [(c, d) for c, d in results if d <= radius_km * 1000]
    results.sort(key=lambda x: x[1])
    return results[:max_results]


class NearbyMerchantResponse(BaseModel):
    place_id: str
    name: str
    photo_url: str
    distance_m: float
    walk_time_min: int
    has_exclusive: bool
    phone: Optional[str] = None
    website: Optional[str] = None
    category: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    exclusive_title: Optional[str] = None
    is_nerava_merchant: bool = False
    join_request_count: int = 0


class DiscoveryChargerResponse(BaseModel):
    id: str
    name: str
    address: str
    lat: float
    lng: float
    distance_m: float
    drive_time_min: int
    network: str
    stalls: int
    kw: float
    photo_url: str
    nearby_merchants: List[NearbyMerchantResponse]
    campaign_reward_cents: Optional[int] = None
    has_merchant_perk: bool = False
    pricing_per_kwh: Optional[float] = None


class DiscoveryResponse(BaseModel):
    within_radius: bool
    nearest_charger_id: Optional[str]
    nearest_distance_m: float
    radius_m: int
    chargers: List[DiscoveryChargerResponse]


@router.get("/nearby", response_model=List[Dict[str, Any]])
async def nearby(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(2.0, ge=0.1, le=50.0),
    max_results: int = Query(50, ge=1, le=200)
):
    try:
        items = await fetch_chargers(lat=lat, lng=lng, radius_km=radius_km, max_results=max_results)
        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chargers_fetch_failed: {e}")


@router.get("/discovery", response_model=DiscoveryResponse)
async def discovery(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(50.0, ge=1.0, le=200.0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Get charger discovery data with nearby merchants.

    Returns chargers sorted by distance, each with 2 nearest merchants.
    Sets within_radius=True if user is within 400m of nearest charger.
    Uses SQL bounding-box pre-filter for performance (~50K chargers → ~500 candidates).
    """
    db = SessionLocal()
    try:
        # SQL bounding-box pre-filter + haversine sort
        charger_distances = _query_nearby_chargers(db, lat, lng, radius_km, limit)

        if not charger_distances:
            return DiscoveryResponse(
                within_radius=False,
                nearest_charger_id=None,
                nearest_distance_m=float('inf'),
                radius_m=400,
                chargers=[]
            )

        # Find nearest charger
        nearest_charger, nearest_distance_m = charger_distances[0]
        within_radius = nearest_distance_m <= 400

        # Bulk-load merchant links for all chargers (replaces N+1 queries)
        charger_ids = [c.id for c, _ in charger_distances]
        all_links = (
            db.query(ChargerMerchant, Merchant)
            .join(Merchant, ChargerMerchant.merchant_id == Merchant.id)
            .filter(ChargerMerchant.charger_id.in_(charger_ids))
            .order_by(ChargerMerchant.charger_id, ChargerMerchant.distance_m)
            .all()
        )
        # Group by charger_id, keep top 2 per charger
        from collections import defaultdict
        links_by_charger: dict[str, list] = defaultdict(list)
        for link, merchant in all_links:
            if len(links_by_charger[link.charger_id]) < 2:
                links_by_charger[link.charger_id].append((link, merchant))

        # Load active campaigns once for reward matching
        now = datetime.utcnow()
        active_campaigns = db.query(Campaign).filter(
            Campaign.status == "active",
            Campaign.start_date <= now,
            Campaign.spent_cents < Campaign.budget_cents,
        ).order_by(Campaign.priority.asc()).all()

        # Build response for each charger
        discovery_chargers = []
        for charger, distance_m in charger_distances:
            drive_time_min = max(1, math.ceil(distance_m / 500))

            nearby_merchants = []
            seen_names = set()
            _test_names = {"test", "test2", "test3", "test merchant"}
            for link, merchant in links_by_charger.get(charger.id, []):
                merchant_name_lower = (merchant.name or "").lower().strip()
                # Skip test merchants
                if merchant_name_lower in _test_names:
                    continue
                # Deduplicate: substring match (e.g. "Heights Pizzeria" vs "Heights Pizzeria & Drafthouse")
                is_dup = False
                for seen in list(seen_names):
                    if merchant_name_lower in seen or seen in merchant_name_lower:
                        if link.exclusive_title:
                            seen_names.discard(seen)
                            nearby_merchants[:] = [m for m in nearby_merchants if m.name and m.name.lower() != seen]
                        else:
                            is_dup = True
                        break
                if is_dup:
                    continue
                dedup_key = merchant_name_lower
                if dedup_key in seen_names:
                    continue
                seen_names.add(dedup_key)
                walk_time_min = max(1, math.ceil(link.distance_m / 80))
                if "asadas" in merchant_name_lower and "grill" in merchant_name_lower:
                    photo_url = "/static/merchant_photos_asadas_grill/asadas_grill_01.jpg"
                elif getattr(merchant, 'primary_photo_url', None):
                    photo_url = merchant.primary_photo_url
                elif merchant.place_id:
                    photo_url = f"/static/demo_chargers/{charger.id}/merchants/{merchant.place_id}_0.jpg"
                else:
                    photo_url = merchant.photo_url or ""

                has_exclusive = link.exclusive_title is not None and link.exclusive_title != ""

                nearby_merchants.append(NearbyMerchantResponse(
                    place_id=merchant.place_id or merchant.id,
                    name=merchant.name,
                    photo_url=photo_url,
                    distance_m=link.distance_m,
                    walk_time_min=walk_time_min,
                    has_exclusive=has_exclusive,
                    phone=merchant.phone,
                    website=merchant.website,
                    category=merchant.category,
                    lat=merchant.lat,
                    lng=merchant.lng,
                    exclusive_title=link.exclusive_title,
                    is_nerava_merchant=has_exclusive,
                ))

            charger_photo_url = f"/static/demo_chargers/{charger.id}/hero.jpg"
            stalls = len(charger.connector_types) if charger.connector_types else 0

            reward_cents = _get_reward_for_charger(active_campaigns, charger.id, charger.network_name or "")
            has_perk = len(links_by_charger.get(charger.id, [])) > 0 and any(
                link.exclusive_title for link, _ in links_by_charger.get(charger.id, [])
            )

            discovery_chargers.append(DiscoveryChargerResponse(
                id=charger.id,
                name=charger.name,
                address=charger.address or "",
                lat=charger.lat,
                lng=charger.lng,
                distance_m=distance_m,
                drive_time_min=drive_time_min,
                network=charger.network_name or "Unknown",
                stalls=stalls,
                kw=charger.power_kw or 0.0,
                photo_url=charger_photo_url,
                nearby_merchants=nearby_merchants,
                campaign_reward_cents=reward_cents,
                has_merchant_perk=has_perk,
                pricing_per_kwh=getattr(charger, 'pricing_per_kwh', None),
            ))

        return DiscoveryResponse(
            within_radius=within_radius,
            nearest_charger_id=nearest_charger.id,
            nearest_distance_m=nearest_distance_m,
            radius_m=400,
            chargers=discovery_chargers
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"discovery_failed: {e}")
    finally:
        db.close()


class ChargerDetailResponse(BaseModel):
    id: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    lat: float
    lng: float
    network_name: Optional[str] = None
    connector_types: List[str] = []
    power_kw: Optional[float] = None
    num_evse: Optional[int] = None
    status: str = "available"
    distance_m: float = 0.0
    drive_time_min: int = 0
    total_sessions_30d: int = 0
    unique_drivers_30d: int = 0
    avg_duration_min: float = 0.0
    active_reward_cents: Optional[int] = None
    nearby_merchants: List[NearbyMerchantResponse] = []
    # New fields for enhanced detail
    pricing_per_kwh: Optional[float] = None
    pricing_source: Optional[str] = None
    nerava_score: Optional[float] = None
    drivers_charging_now: int = 0


@router.get("/{charger_id}/detail", response_model=ChargerDetailResponse)
async def charger_detail(
    charger_id: str,
    lat: float = Query(0.0, ge=-90, le=90),
    lng: float = Query(0.0, ge=-180, le=180),
):
    """
    Get detailed charger info with session stats and nearby merchants.
    """
    db = SessionLocal()
    try:
        charger = db.query(Charger).filter(Charger.id == charger_id).first()
        if not charger:
            raise HTTPException(status_code=404, detail="Charger not found")

        # Distance from user
        distance_m = _haversine_distance(lat, lng, charger.lat, charger.lng) if lat and lng else 0.0
        drive_time_min = max(1, math.ceil(distance_m / 500))

        # Session stats (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        stats = db.query(
            func.count(SessionEvent.id),
            func.count(func.distinct(SessionEvent.driver_user_id)),
            func.coalesce(func.avg(SessionEvent.duration_minutes), 0),
        ).filter(
            SessionEvent.charger_id == charger_id,
            SessionEvent.session_start >= thirty_days_ago,
        ).first()

        total_sessions_30d = stats[0] if stats else 0
        unique_drivers_30d = stats[1] if stats else 0
        avg_duration_min = round(float(stats[2]), 1) if stats else 0.0

        # Active campaign reward for this charger
        active_reward_cents = None
        now = datetime.utcnow()
        campaigns = db.query(Campaign).filter(
            Campaign.status == "active",
            Campaign.start_date <= now,
            Campaign.spent_cents < Campaign.budget_cents,
        ).order_by(Campaign.priority.asc()).all()

        active_reward_cents = _get_reward_for_charger(campaigns, charger_id, charger.network_name or "")

        # Nearby merchants (up to 6)
        merchant_links = db.query(ChargerMerchant).filter(
            ChargerMerchant.charger_id == charger.id
        ).order_by(ChargerMerchant.distance_m.asc()).limit(6).all()

        nearby_merchants = []
        seen_merchant_names = set()
        # Names that are clearly test/placeholder data
        _test_names = {"test", "test2", "test3", "test merchant"}
        for link in merchant_links:
            merchant = db.query(Merchant).filter(Merchant.id == link.merchant_id).first()
            if not merchant:
                continue
            merchant_name_lower = (merchant.name or "").lower().strip()
            # Skip test merchants
            if merchant_name_lower in _test_names:
                continue
            # Deduplicate: skip if this name is a substring of an already-seen name
            # or if an already-seen name is a substring of this one
            is_dup = False
            for seen in list(seen_merchant_names):
                if merchant_name_lower in seen or seen in merchant_name_lower:
                    # Keep the one with the exclusive, or the longer name
                    if link.exclusive_title:
                        seen_merchant_names.discard(seen)
                        nearby_merchants[:] = [m for m in nearby_merchants if m.name and m.name.lower() != seen]
                    else:
                        is_dup = True
                    break
            if is_dup:
                continue
            dedup_key = merchant_name_lower
            if dedup_key in seen_merchant_names:
                continue
            seen_merchant_names.add(dedup_key)
            walk_time_min = max(1, math.ceil(link.distance_m / 80))
            merchant_name_lower = merchant.name.lower() if merchant.name else ""
            if "asadas" in merchant_name_lower and "grill" in merchant_name_lower:
                photo_url = "/static/merchant_photos_asadas_grill/asadas_grill_01.jpg"
            elif getattr(merchant, 'primary_photo_url', None):
                photo_url = merchant.primary_photo_url
            elif merchant.place_id:
                photo_url = f"/static/demo_chargers/{charger.id}/merchants/{merchant.place_id}_0.jpg"
            else:
                photo_url = merchant.photo_url or ""
            has_exclusive = link.exclusive_title is not None and link.exclusive_title != ""
            is_nerava = has_exclusive  # On Nerava = has an active exclusive/perk

            # Get join request count for non-Nerava merchants
            join_count = 0
            if not is_nerava:
                merchant_place_id = merchant.place_id or merchant.id
                try:
                    from app.services.merchant_reward_service import get_join_request_count
                    join_count = get_join_request_count(db, merchant_place_id)
                except Exception:
                    pass

            nearby_merchants.append(NearbyMerchantResponse(
                place_id=merchant.place_id or merchant.id,
                name=merchant.name,
                photo_url=photo_url,
                distance_m=link.distance_m,
                walk_time_min=walk_time_min,
                has_exclusive=has_exclusive,
                phone=merchant.phone,
                website=merchant.website,
                category=merchant.category,
                lat=merchant.lat,
                lng=merchant.lng,
                exclusive_title=link.exclusive_title,
                is_nerava_merchant=is_nerava,
                join_request_count=join_count,
            ))

        # Drivers currently charging at this station
        drivers_now = db.query(func.count(SessionEvent.id)).filter(
            SessionEvent.charger_id == charger_id,
            SessionEvent.session_end.is_(None),
        ).scalar() or 0

        # Nerava Score — use cached value or compute fresh
        nerava_score = getattr(charger, 'nerava_score', None)
        if nerava_score is None and total_sessions_30d >= 5:
            try:
                from app.services.charger_score import compute_nerava_score
                nerava_score = compute_nerava_score(charger_id, db)
                # Cache on the charger row
                charger.nerava_score = nerava_score
                db.commit()
            except Exception:
                pass

        return ChargerDetailResponse(
            id=charger.id,
            name=charger.name,
            address=charger.address,
            city=charger.city,
            state=charger.state,
            lat=charger.lat,
            lng=charger.lng,
            network_name=charger.network_name,
            connector_types=charger.connector_types or [],
            power_kw=charger.power_kw,
            num_evse=charger.num_evse,
            status=charger.status or "available",
            distance_m=distance_m,
            drive_time_min=drive_time_min,
            total_sessions_30d=total_sessions_30d,
            unique_drivers_30d=unique_drivers_30d,
            avg_duration_min=avg_duration_min,
            active_reward_cents=active_reward_cents,
            nearby_merchants=nearby_merchants,
            pricing_per_kwh=getattr(charger, 'pricing_per_kwh', None),
            pricing_source=getattr(charger, 'pricing_source', None),
            nerava_score=nerava_score,
            drivers_charging_now=drivers_now,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"charger_detail failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"charger_detail_failed: {e}")
    finally:
        db.close()


# ==================== Charger Favorites ====================

@router.post("/{charger_id}/favorite")
def toggle_charger_favorite(
    charger_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Toggle a charger favorite. If already favorited, removes it; otherwise adds it."""
    # Verify charger exists
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail="Charger not found")

    # Check if already favorited
    existing = db.query(FavoriteCharger).filter(
        FavoriteCharger.user_id == driver.id,
        FavoriteCharger.charger_id == charger_id,
    ).first()

    if existing:
        # Toggle off — remove the favorite
        db.delete(existing)
        db.commit()
        return {"favorited": False}

    # Toggle on — create the favorite
    favorite = FavoriteCharger(user_id=driver.id, charger_id=charger_id)
    db.add(favorite)
    db.commit()
    return {"favorited": True}


@router.delete("/{charger_id}/favorite")
def remove_charger_favorite(
    charger_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Remove a charger from favorites (idempotent)."""
    existing = db.query(FavoriteCharger).filter(
        FavoriteCharger.user_id == driver.id,
        FavoriteCharger.charger_id == charger_id,
    ).first()

    if existing:
        db.delete(existing)
        db.commit()

    return {"ok": True}


@router.get("/favorites")
def list_charger_favorites(
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """List user's favorite charger IDs."""
    favorites = db.query(FavoriteCharger).filter(
        FavoriteCharger.user_id == driver.id,
    ).all()

    return {"favorites": [f.charger_id for f in favorites]}


# ==================== Street View Proxy ====================

@router.get("/{charger_id}/streetview")
async def get_streetview(charger_id: str):
    """
    Return a Street View Static API URL for the charger location.
    Proxies the Google API key so it's not exposed to the frontend.
    """
    import os
    db = SessionLocal()
    try:
        charger = db.query(Charger).filter(Charger.id == charger_id).first()
        if not charger:
            raise HTTPException(status_code=404, detail="Charger not found")

        api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
        if not api_key:
            return {"url": None}

        url = f"https://maps.googleapis.com/maps/api/streetview?size=600x300&location={charger.lat},{charger.lng}&key={api_key}"
        return {"url": url}
    finally:
        db.close()


# ==================== Charger Search (Geocoded) ====================

@router.get("/search")
async def search_chargers(
    q: str = Query("", min_length=0),
    lat: float = Query(None, ge=-90, le=90),
    lng: float = Query(None, ge=-180, le=180),
):
    """
    Search for chargers by location name/address, or by coordinates.
    If q is empty but lat/lng are provided, returns nearby chargers at that location.
    Uses Google Geocoding to resolve the query, then finds nearby chargers.
    """
    import os

    import httpx

    db = SessionLocal()
    try:
        geocoded_location = None
        search_lat = lat
        search_lng = lng

        # If lat/lng provided with empty query, skip geocoding and use coordinates directly
        if not q.strip() and search_lat is not None and search_lng is not None:
            geocoded_location = {
                "lat": search_lat,
                "lng": search_lng,
                "name": "Map area",
            }
        elif q.strip():
            # Geocode query — use free Nominatim, fall back to Google if configured
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://nominatim.openstreetmap.org/search",
                        params={"q": q, "format": "json", "limit": 1},
                        headers={"User-Agent": "Nerava/1.0"},
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data:
                            search_lat = float(data[0]["lat"])
                            search_lng = float(data[0]["lon"])
                            geocoded_location = {
                                "lat": search_lat,
                                "lng": search_lng,
                                "name": data[0].get("display_name", q),
                            }
            except Exception as e:
                logger.warning(f"Nominatim geocoding failed for '{q}': {e}")
                # Fall back to Google Geocoding if API key is available
                api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
                if api_key:
                    try:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(
                                "https://maps.googleapis.com/maps/api/geocode/json",
                                params={"address": q, "key": api_key},
                                timeout=5.0,
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                results = data.get("results", [])
                                if results:
                                    loc = results[0]["geometry"]["location"]
                                    search_lat = loc["lat"]
                                    search_lng = loc["lng"]
                                    geocoded_location = {
                                        "lat": search_lat,
                                        "lng": search_lng,
                                        "name": results[0].get("formatted_address", q),
                                    }
                    except Exception as e2:
                        logger.warning(f"Google geocoding fallback also failed for '{q}': {e2}")

        if search_lat is None or search_lng is None:
            return {"chargers": [], "location": None}

        # SQL bounding-box pre-filter + haversine sort (replaces full table scan)
        results = _query_nearby_chargers(db, search_lat, search_lng, radius_km=25.0, max_results=20)

        charger_list = []
        charger_ids = [c.id for c, _ in results]

        # Bulk-load exclusive merchant perks for all result chargers (gold star on map)
        perk_by_charger = {}
        if charger_ids:
            try:
                perk_links = (
                    db.query(ChargerMerchant)
                    .filter(
                        ChargerMerchant.charger_id.in_(charger_ids),
                        ChargerMerchant.exclusive_title.isnot(None),
                        ChargerMerchant.exclusive_title != "",
                    )
                    .all()
                )
                for link in perk_links:
                    if link.charger_id not in perk_by_charger:
                        perk_by_charger[link.charger_id] = link.exclusive_title
            except Exception:
                pass

        for charger, distance_m in results:
            charger_list.append({
                "id": charger.id,
                "name": charger.name,
                "lat": charger.lat,
                "lng": charger.lng,
                "distance_m": distance_m,
                "network_name": charger.network_name,
                "power_kw": charger.power_kw,
                "num_evse": charger.num_evse,
                "connector_types": charger.connector_types or [],
                "pricing_per_kwh": getattr(charger, 'pricing_per_kwh', None),
                "has_merchant_perk": charger.id in perk_by_charger,
                "merchant_perk_title": perk_by_charger.get(charger.id),
            })

        return {"chargers": charger_list, "location": geocoded_location}
    finally:
        db.close()


# ==================== Admin: Seeding ====================

@router.get("/admin/stats")
async def charger_stats():
    """Get charger counts by network and state (no auth required, read-only)."""
    db = SessionLocal()
    try:
        total = db.query(func.count(Charger.id)).scalar()
        by_network = db.query(
            Charger.network_name, func.count(Charger.id)
        ).group_by(Charger.network_name).order_by(func.count(Charger.id).desc()).all()
        by_state = db.query(
            Charger.state, func.count(Charger.id)
        ).group_by(Charger.state).order_by(func.count(Charger.id).desc()).limit(10).all()

        # Houston-area count
        houston_count = db.query(func.count(Charger.id)).filter(
            Charger.lat.between(29.52, 30.11),
            Charger.lng.between(-95.79, -95.01),
        ).scalar()

        # Merchant count
        merchant_total = db.query(func.count(Merchant.id)).scalar()
        junction_total = db.query(func.count(ChargerMerchant.charger_id)).scalar()

        return {
            "total_chargers": total,
            "houston_area_chargers": houston_count,
            "total_merchants": merchant_total,
            "total_junctions": junction_total,
            "by_network": {n or "Unknown": c for n, c in by_network},
            "top_states": {s or "?": c for s, c in by_state},
        }
    finally:
        db.close()


_seed_status = {"running": False, "last_result": None, "started_at": None}
_grid_seed_status = {"running": False, "last_result": None, "started_at": None}


@router.post("/admin/seed")
async def trigger_seed(
    states: Optional[List[str]] = Query(None),
    seed_merchants_flag: bool = Query(True, alias="seed_merchants"),
):
    """
    Trigger charger seeding from NREL in a background thread.
    Returns immediately. Check /admin/seed/status for progress.
    """
    import threading

    if _seed_status["running"]:
        return {"status": "already_running", "started_at": _seed_status["started_at"]}

    target_states = states or ["TX"]

    def _run_seed():
        import asyncio

        from scripts.seed_chargers_bulk import seed_chargers
        from scripts.seed_merchants_city import seed_city

        _seed_status["running"] = True
        _seed_status["started_at"] = datetime.utcnow().isoformat()
        _seed_status["last_result"] = None

        db = SessionLocal()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Seed chargers
            charger_result = loop.run_until_complete(seed_chargers(db, states=target_states))
            logger.info(f"Charger seed result: {charger_result}")

            merchant_results = []
            if seed_merchants_flag:
                state_city_map = {
                    "TX": ["houston", "austin", "dallas", "san_antonio"],
                    "AZ": ["phoenix"],
                    "CA": ["los_angeles", "san_francisco", "san_jose", "san_diego",
                           "sacramento", "oakland", "fresno", "bakersfield",
                           "riverside", "irvine"],
                    "FL": ["miami", "fort_lauderdale", "west_palm_beach", "orlando",
                           "tampa", "jacksonville", "st_petersburg", "naples",
                           "sarasota", "tallahassee"],
                }
                for state in target_states:
                    cities = state_city_map.get(state, [])
                    for city in cities:
                        try:
                            result = loop.run_until_complete(seed_city(db, city))
                            merchant_results.append(result)
                            logger.info(f"Merchant seed for {city}: {result}")
                        except Exception as e:
                            merchant_results.append({"city": city, "error": str(e)})
                            logger.error(f"Merchant seed failed for {city}: {e}")

            loop.close()
            _seed_status["last_result"] = {
                "charger_seed": charger_result,
                "merchant_seed": merchant_results,
                "completed_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Seed failed: {e}", exc_info=True)
            _seed_status["last_result"] = {"error": str(e), "completed_at": datetime.utcnow().isoformat()}
        finally:
            db.close()
            _seed_status["running"] = False

    thread = threading.Thread(target=_run_seed, daemon=True)
    thread.start()

    return {"status": "started", "states": target_states, "seed_merchants": seed_merchants_flag}


@router.get("/admin/seed/status")
async def seed_status():
    """Check the status of a running or completed seed job."""
    return _seed_status


@router.post("/admin/seed-grid")
async def trigger_grid_seed(
    states: Optional[List[str]] = Query(None),
    batch_size: int = Query(2000, ge=0, le=50000),
    reset_progress: bool = Query(False),
):
    """
    Trigger charger seeding from NREL using the lat/lng grid approach.
    Returns immediately. Check /admin/seed-grid/status for progress.

    The grid approach queries by lat/lng + small radius with spacing that
    varies by state density tier, keeping each query under the 200-result cap.
    Use batch_size to limit queries per state (0=unlimited).
    """
    import threading

    if _grid_seed_status["running"]:
        return {"status": "already_running", "started_at": _grid_seed_status["started_at"]}

    target_states = states  # None = all metros

    def _run_grid_seed():
        import asyncio

        from scripts.seed_chargers_grid import PROGRESS_FILE, seed_chargers_grid

        _grid_seed_status["running"] = True
        _grid_seed_status["started_at"] = datetime.utcnow().isoformat()
        _grid_seed_status["last_result"] = None

        if reset_progress:
            import os
            if os.path.exists(PROGRESS_FILE):
                os.remove(PROGRESS_FILE)

        db = SessionLocal()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                seed_chargers_grid(db, states=target_states, batch_size=batch_size)
            )
            logger.info(f"Grid seed result: {result}")
            loop.close()

            _grid_seed_status["last_result"] = {
                **result,
                "completed_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Grid seed failed: {e}", exc_info=True)
            _grid_seed_status["last_result"] = {
                "error": str(e),
                "completed_at": datetime.utcnow().isoformat(),
            }
        finally:
            db.close()
            _grid_seed_status["running"] = False

    thread = threading.Thread(target=_run_grid_seed, daemon=True)
    thread.start()

    return {
        "status": "started",
        "states": target_states,
        "batch_size": batch_size,
        "reset_progress": reset_progress,
    }


@router.get("/admin/seed-grid/status")
async def grid_seed_status():
    """Check the status of a running or completed grid seed job."""
    return _grid_seed_status


@router.post("/admin/seed-pricing")
async def seed_charger_pricing(
    seed_key: str = Header(..., alias="X-Seed-Key"),
):
    """
    Populate pricing_per_kwh for chargers based on network averages.
    Uses known rates: Tesla $0.42/kWh, ChargePoint $0.35, EVgo $0.39, etc.
    """
    from app.core.config import settings
    if seed_key != settings.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid seed key")

    NETWORK_PRICING = {
        "tesla": 0.42, "supercharger": 0.42,
        "chargepoint": 0.35, "evgo": 0.39,
        "electrify america": 0.45, "blink": 0.49,
        "flo": 0.35, "semaconnect": 0.30,
        "volta": 0.00, "greenlots": 0.32,
        "shell recharge": 0.42, "bp pulse": 0.40,
        "ev connect": 0.30,
    }

    db = SessionLocal()
    try:
        chargers = db.query(Charger).filter(
            Charger.pricing_per_kwh.is_(None),
            Charger.network_name.isnot(None),
        ).all()

        updated = 0
        for charger in chargers:
            network_lower = charger.network_name.lower().strip()
            price = None
            for key, value in NETWORK_PRICING.items():
                if key in network_lower:
                    price = value
                    break
            if price is not None:
                charger.pricing_per_kwh = price
                charger.pricing_source = "network_average"
                updated += 1

        db.commit()
        total = len(chargers)
        return {"updated": updated, "checked": total, "status": "done"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/admin/seed-exclusives")
async def seed_merchant_exclusives(
    seed_key: str = Header(..., alias="X-Seed-Key"),
):
    """Set exclusive titles on ChargerMerchant rows by merchant name patterns."""
    from app.core.config import settings
    if seed_key != settings.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid seed key")

    # Define merchant exclusives: merchant name pattern → exclusive title
    EXCLUSIVES = {
        "heights pizzeria": "Free Garlic Knots",
        "asadas grill": "Free Margarita",
        "schlotzsky": "Free Drink with Sandwich",
    }

    db = SessionLocal()
    try:
        updated = 0
        for pattern, title in EXCLUSIVES.items():
            # Find merchants matching this pattern
            merchants = db.query(Merchant).filter(
                func.lower(Merchant.name).contains(pattern)
            ).all()
            merchant_ids = [m.id for m in merchants]
            if not merchant_ids:
                continue
            # Update all ChargerMerchant links for these merchants
            count = db.query(ChargerMerchant).filter(
                ChargerMerchant.merchant_id.in_(merchant_ids)
            ).update(
                {ChargerMerchant.exclusive_title: title},
                synchronize_session=False,
            )
            updated += count
        db.commit()
        return {"updated": updated, "status": "done"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
