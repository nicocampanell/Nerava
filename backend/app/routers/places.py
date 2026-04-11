"""
Merchant places API — uses free OpenStreetMap/Overpass by default,
falls back to Google Places only if GOOGLE_PLACES_API_KEY is configured.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_driver import get_current_driver
from app.models.while_you_charge import Merchant
from app.services.merchant_enrichment import enrich_from_google_places

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchants/places", tags=["places"])

def _has_google_places() -> bool:
    """Check if Google Places API key is configured."""
    return bool(getattr(settings, 'GOOGLE_PLACES_API_KEY', ''))


class PlaceSearchResponse(BaseModel):
    place_id: str
    name: str
    lat: float
    lng: float
    address: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_url: Optional[str] = None
    types: List[str] = []


class PlaceDetailsResponse(BaseModel):
    place_id: str
    name: str
    lat: float
    lng: float
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    price_level: Optional[int] = None
    business_status: Optional[str] = None
    types: List[str] = []
    photo_urls: List[str] = []


@router.get("/search", response_model=List[PlaceSearchResponse])
async def search_places(
    q: str = Query(..., description="Search query (e.g., 'Asadas Grill')"),
    lat: Optional[float] = Query(None, description="Latitude for location bias"),
    lng: Optional[float] = Query(None, description="Longitude for location bias"),
    max_results: int = Query(10, ge=1, le=20, description="Maximum number of results"),
):
    """
    Search for places. Uses free OpenStreetMap/Overpass by default.
    Falls back to Google Places only if GOOGLE_PLACES_API_KEY is set.
    """
    if _has_google_places():
        return await _search_google(q, lat, lng, max_results)
    return await _search_osm(q, lat, lng, max_results)


async def _search_osm(
    q: str,
    lat: Optional[float],
    lng: Optional[float],
    max_results: int,
) -> List[PlaceSearchResponse]:
    """Search using free OpenStreetMap Overpass API."""
    from app.integrations.overpass_client import OverpassClient

    if lat is None or lng is None:
        # Without coordinates, use Nominatim free geocoding search
        return await _search_nominatim(q, max_results)

    client = OverpassClient()
    # Search within 1500m radius for POIs
    pois = await client.find_pois_near(lat, lng, radius_m=1500)

    # Filter by name match (case-insensitive substring)
    q_lower = q.lower()
    matched = [p for p in pois if q_lower in p["name"].lower()]

    # If no name match, return all nearby sorted by relevance
    if not matched:
        matched = pois

    response = []
    for poi in matched[:max_results]:
        response.append(PlaceSearchResponse(
            place_id=f"osm_{poi['osm_id']}",
            name=poi["name"],
            lat=poi["lat"],
            lng=poi["lng"],
            address=None,
            rating=None,
            user_rating_count=None,
            photo_url=None,
            types=[poi["type"]] if poi.get("type") else [],
        ))
    return response


async def _search_nominatim(q: str, max_results: int) -> List[PlaceSearchResponse]:
    """Free geocoding search via Nominatim (no API key needed)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": q,
                    "format": "json",
                    "limit": max_results,
                    "addressdetails": 1,
                },
                headers={"User-Agent": "Nerava/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"[Places] Nominatim search failed: {e}")
        return []

    response = []
    for item in data:
        response.append(PlaceSearchResponse(
            place_id=f"osm_node_{item.get('osm_id', '')}",
            name=item.get("display_name", "").split(",")[0],
            lat=float(item["lat"]),
            lng=float(item["lon"]),
            address=item.get("display_name"),
            rating=None,
            user_rating_count=None,
            photo_url=None,
            types=[item.get("type", "")] if item.get("type") else [],
        ))
    return response


async def _search_google(
    q: str,
    lat: Optional[float],
    lng: Optional[float],
    max_results: int,
) -> List[PlaceSearchResponse]:
    """Search using Google Places API (paid, $7/1K calls)."""
    from app.services.google_places_new import get_photo_url, search_text

    location_bias = None
    if lat is not None and lng is not None:
        location_bias = {"lat": lat, "lng": lng}

    results = await search_text(q, location_bias=location_bias, max_results=max_results)

    response = []
    for result in results:
        photo_url = None
        if result.get("photo_url", "").startswith("photo_ref:"):
            photo_ref = result["photo_url"].replace("photo_ref:", "")
            photo_url = await get_photo_url(photo_ref, max_width=400)
        else:
            photo_url = result.get("photo_url")

        response.append(PlaceSearchResponse(
            place_id=result["place_id"],
            name=result["name"],
            lat=result["lat"],
            lng=result["lng"],
            address=result.get("address"),
            rating=result.get("rating"),
            user_rating_count=result.get("user_rating_count"),
            photo_url=photo_url,
            types=result.get("types", []),
        ))
    return response


@router.get("/{place_id}", response_model=PlaceDetailsResponse)
async def get_place_details(place_id: str):
    """
    Get place details. Uses free OSM/Nominatim for OSM IDs,
    Google Places for Google Place IDs (only if API key configured).
    """
    # OSM-sourced place IDs start with "osm_"
    if place_id.startswith("osm_"):
        return await _get_osm_details(place_id)

    if not _has_google_places():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google Places API not configured. Use OSM place IDs."
        )

    return await _get_google_details(place_id)


async def _get_osm_details(place_id: str) -> PlaceDetailsResponse:
    """Get place details from OSM via Nominatim (free)."""
    import httpx

    # Extract OSM type and ID from "osm_node_12345" or "osm_way_12345"
    parts = place_id.replace("osm_", "").split("_", 1)
    if len(parts) == 2:
        osm_type, osm_id = parts
    else:
        osm_type, osm_id = "node", parts[0]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/lookup",
                params={
                    "osm_ids": f"{osm_type[0].upper()}{osm_id}",
                    "format": "json",
                    "addressdetails": 1,
                    "extratags": 1,
                },
                headers={"User-Agent": "Nerava/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"[Places] Nominatim lookup failed: {e}")
        raise HTTPException(status_code=404, detail="Place not found")

    if not data:
        raise HTTPException(status_code=404, detail="Place not found")

    item = data[0]
    extratags = item.get("extratags", {})

    return PlaceDetailsResponse(
        place_id=place_id,
        name=item.get("name", item.get("display_name", "").split(",")[0]),
        lat=float(item.get("lat", 0)),
        lng=float(item.get("lon", 0)),
        address=item.get("display_name"),
        phone=extratags.get("phone") or extratags.get("contact:phone"),
        website=extratags.get("website") or extratags.get("contact:website"),
        rating=None,
        user_rating_count=None,
        price_level=None,
        business_status=None,
        types=[item.get("type", "")] if item.get("type") else [],
        photo_urls=[],
    )


async def _get_google_details(place_id: str) -> PlaceDetailsResponse:
    """Get place details from Google Places API (paid)."""
    from app.services.google_places_new import get_photo_url, place_details

    place_data = await place_details(place_id)

    if not place_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Place '{place_id}' not found"
        )

    display_name = place_data.get("displayName", {})
    name = display_name.get("text", "") if isinstance(display_name, dict) else str(display_name)
    location = place_data.get("location", {})

    photo_urls = []
    photos = place_data.get("photos", [])
    for photo in photos[:5]:
        photo_name = photo.get("name", "")
        if photo_name:
            photo_ref = photo_name.replace("places/", "").split("/photos/")[-1]
            if photo_ref:
                photo_url = await get_photo_url(photo_ref, max_width=800)
                if photo_url:
                    photo_urls.append(photo_url)

    return PlaceDetailsResponse(
        place_id=place_id.replace("places/", ""),
        name=name,
        lat=location.get("latitude", 0),
        lng=location.get("longitude", 0),
        address=place_data.get("formattedAddress"),
        phone=place_data.get("nationalPhoneNumber"),
        website=place_data.get("websiteUri"),
        rating=place_data.get("rating"),
        user_rating_count=place_data.get("userRatingCount"),
        price_level=place_data.get("priceLevel"),
        business_status=place_data.get("businessStatus"),
        types=place_data.get("types", []),
        photo_urls=photo_urls,
    )


@router.post("/merchants/{merchant_id}/refresh")
async def refresh_merchant_from_places(
    merchant_id: str,
    db: Session = Depends(get_db),
    user = Depends(get_current_driver),  # Require auth
):
    """
    Refresh merchant data from Google Places API.
    Rate-limited: max 1 refresh per day per merchant.
    """
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Merchant '{merchant_id}' not found"
        )
    
    if not merchant.place_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Merchant does not have a place_id"
        )
    
    # Check rate limit (simple check - in production, use Redis)
    from datetime import datetime, timedelta
    if merchant.google_places_updated_at:
        age = datetime.utcnow() - merchant.google_places_updated_at
        if age < timedelta(hours=24):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Merchant was refreshed less than 24 hours ago"
            )
    
    # Enrich merchant
    success = await enrich_from_google_places(db, merchant, merchant.place_id, force_refresh=True)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to refresh merchant data"
        )
    
    return {"status": "success", "merchant_id": merchant_id}
