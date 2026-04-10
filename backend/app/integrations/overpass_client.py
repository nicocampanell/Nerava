"""
OpenStreetMap Overpass API client for finding POIs near EV chargers.

100% free, no API key required. Rate-limited to ~2 requests/second.
https://wiki.openstreetmap.org/wiki/Overpass_API
"""
import asyncio
import logging
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

# Amenity and shop tags we care about
AMENITY_TAGS = [
    "restaurant", "cafe", "bar", "fast_food", "pub",
    "ice_cream", "food_court", "bakery",
]
SHOP_TAGS = [
    "supermarket", "convenience", "mall", "department_store",
    "clothes", "electronics", "bookshop", "gift",
]


def _build_bbox_query(south: float, west: float, north: float, east: float) -> str:
    """Build Overpass QL query for POIs in a bounding box."""
    bbox = f"{south},{west},{north},{east}"
    amenity_filter = "|".join(AMENITY_TAGS)
    shop_filter = "|".join(SHOP_TAGS)

    return f"""
[out:json][timeout:30];
(
  node["amenity"~"^({amenity_filter})$"]["name"]({bbox});
  way["amenity"~"^({amenity_filter})$"]["name"]({bbox});
  node["shop"~"^({shop_filter})$"]["name"]({bbox});
  way["shop"~"^({shop_filter})$"]["name"]({bbox});
);
out center tags;
"""


def _normalize_element(el: dict) -> Optional[dict]:
    """Normalize an Overpass element to a standard dict."""
    tags = el.get("tags", {})
    name = tags.get("name")
    if not name:
        return None

    # Get coordinates (nodes have lat/lon directly, ways have center)
    lat = el.get("lat") or (el.get("center", {}).get("lat"))
    lng = el.get("lon") or (el.get("center", {}).get("lon"))
    if not lat or not lng:
        return None

    osm_id = f"{el.get('type', 'node')}_{el.get('id', 0)}"
    poi_type = tags.get("amenity") or tags.get("shop") or "other"

    return {
        "osm_id": osm_id,
        "name": name,
        "lat": float(lat),
        "lng": float(lng),
        "type": poi_type,
        "cuisine": tags.get("cuisine"),
        "phone": tags.get("phone") or tags.get("contact:phone"),
        "website": tags.get("website") or tags.get("contact:website"),
        "opening_hours": tags.get("opening_hours"),
        "brand": tags.get("brand"),
        "brand_wikidata": tags.get("brand:wikidata"),
    }


class OverpassClient:
    BASE_URL = "https://overpass-api.de/api/interpreter"

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._last_request_time = 0.0

    async def _throttle(self):
        """Ensure we don't exceed ~2 req/s."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < 0.5:
            await asyncio.sleep(0.5 - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _query(self, overpass_ql: str) -> List[dict]:
        """Execute an Overpass QL query and return normalized results."""
        await self._throttle()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    self.BASE_URL,
                    data={"data": overpass_ql},
                )
                if response.status_code == 429:
                    logger.warning("[Overpass] Rate limited, waiting 10s")
                    await asyncio.sleep(10)
                    response = await client.post(
                        self.BASE_URL,
                        data={"data": overpass_ql},
                    )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[Overpass] HTTP error {e.response.status_code}: {e.response.text[:200]}")
                return []
            except Exception as e:
                logger.error(f"[Overpass] Request error: {e}")
                return []

        elements = data.get("elements", [])
        results = []
        for el in elements:
            normalized = _normalize_element(el)
            if normalized:
                results.append(normalized)
        return results

    async def find_pois_near(
        self, lat: float, lng: float, radius_m: int = 800
    ) -> List[dict]:
        """Find food/drink/retail POIs within a radius of a coordinate."""
        amenity_filter = "|".join(AMENITY_TAGS)
        shop_filter = "|".join(SHOP_TAGS)

        query = f"""
[out:json][timeout:30];
(
  node["amenity"~"^({amenity_filter})$"]["name"](around:{radius_m},{lat},{lng});
  way["amenity"~"^({amenity_filter})$"]["name"](around:{radius_m},{lat},{lng});
  node["shop"~"^({shop_filter})$"]["name"](around:{radius_m},{lat},{lng});
  way["shop"~"^({shop_filter})$"]["name"](around:{radius_m},{lat},{lng});
);
out center tags;
"""
        return await self._query(query)

    async def find_pois_in_bbox(
        self, south: float, west: float, north: float, east: float
    ) -> List[dict]:
        """Find all POIs in a bounding box (more efficient for grid cells)."""
        query = _build_bbox_query(south, west, north, east)
        return await self._query(query)
