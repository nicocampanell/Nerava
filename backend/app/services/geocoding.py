"""
Google Geocoding API service for resolving addresses/places to lat/lng.
Uses the existing GOOGLE_PLACES_API_KEY from config.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def geocode_address(query: str):
    """
    Resolve an address or place name to lat/lng using Google Geocoding API.

    Returns: {"lat": float, "lng": float, "name": str} or None
    """
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        logger.warning("GOOGLE_PLACES_API_KEY not set, cannot geocode")
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": query, "key": api_key},
                timeout=5.0,
            )

            if resp.status_code != 200:
                logger.warning(f"Geocoding HTTP {resp.status_code} for '{query}'")
                return None

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None

            location = results[0]["geometry"]["location"]
            return {
                "lat": location["lat"],
                "lng": location["lng"],
                "name": results[0].get("formatted_address", query),
            }
    except Exception as e:
        logger.warning(f"Geocoding failed for '{query}': {e}")
        return None
