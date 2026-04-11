import os

"""
Google Distance Matrix API client
https://developers.google.com/maps/documentation/distance-matrix
"""
import logging
from typing import Dict, List, Tuple

import httpx

logger = logging.getLogger(__name__)

# Hardcoded API key (same as Places API)
GOOGLE_DISTANCE_MATRIX_API_KEY = os.getenv("GOOGLE_API_KEY", "")

GOOGLE_DISTANCE_MATRIX_BASE_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


async def get_walk_times(
    origins: List[Tuple[float, float]], destinations: List[Tuple[float, float]]
) -> Dict[Tuple[Tuple[float, float], Tuple[float, float]], Dict]:
    """
    Get walking times between multiple origin-destination pairs.

    Args:
        origins: List of (lat, lng) tuples
        destinations: List of (lat, lng) tuples

    Returns:
        Dict mapping (origin, destination) -> {
            "duration_s": int,
            "distance_m": float,
            "status": str
        }
    """
    # Google Distance Matrix API supports up to 25 origins/destinations per request
    # For larger batches, we'd need to chunk, but for now assume reasonable sizes

    origins_str = "|".join([f"{lat},{lng}" for lat, lng in origins])
    destinations_str = "|".join([f"{lat},{lng}" for lat, lng in destinations])

    params = {
        "key": GOOGLE_DISTANCE_MATRIX_API_KEY,
        "origins": origins_str,
        "destinations": destinations_str,
        "mode": "walking",
        "units": "metric",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(GOOGLE_DISTANCE_MATRIX_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

            results = {}
            rows = data.get("rows", [])

            for i, row in enumerate(rows):
                origin = origins[i]
                elements = row.get("elements", [])

                for j, element in enumerate(elements):
                    destination = destinations[j]
                    status = element.get("status", "UNKNOWN_ERROR")

                    if status == "OK":
                        duration = element.get("duration", {}).get("value", 0)  # seconds
                        distance = element.get("distance", {}).get("value", 0)  # meters
                        results[(origin, destination)] = {
                            "duration_s": duration,
                            "distance_m": distance,
                            "status": "OK",
                        }
                    else:
                        # Fallback to estimation
                        results[(origin, destination)] = _estimate_single_walk_time(
                            origin, destination
                        )

            return results

    except Exception as e:
        logger.error(f"Google Distance Matrix error: {e}", exc_info=True)
        # Fallback to estimation
        return _estimate_walk_times(origins, destinations)


def _estimate_walk_times(
    origins: List[Tuple[float, float]], destinations: List[Tuple[float, float]]
) -> Dict[Tuple[Tuple[float, float], Tuple[float, float]], Dict]:
    """Estimate walk times using straight-line distance"""
    results = {}
    for origin in origins:
        for destination in destinations:
            results[(origin, destination)] = _estimate_single_walk_time(origin, destination)
    return results


def _estimate_single_walk_time(
    origin: Tuple[float, float], destination: Tuple[float, float]
) -> Dict:
    """Estimate walk time from straight-line distance"""
    from math import asin, cos, radians, sin, sqrt

    # Haversine formula for distance
    lat1, lon1 = radians(origin[0]), radians(origin[1])
    lat2, lon2 = radians(destination[0]), radians(destination[1])

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))

    # Earth radius in meters
    R = 6371000
    distance_m = R * c

    # Estimate walking speed: 1.4 m/s (5 km/h)
    # Add 20% overhead for turns, obstacles
    duration_s = int((distance_m / 1.4) * 1.2)

    return {"duration_s": duration_s, "distance_m": distance_m, "status": "ESTIMATED"}
