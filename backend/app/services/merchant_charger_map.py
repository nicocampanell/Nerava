"""
Merchant charger mapping service.

Computes the nearest charger for a merchant location using Haversine formula.
"""
import math
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.while_you_charge import Charger


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in meters using Haversine formula."""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


def compute_nearest_charger(
    db: Session,
    merchant_lat: float,
    merchant_lng: float,
    zone_slug: Optional[str] = None
) -> Tuple[Optional[str], Optional[int]]:
    """
    Compute the nearest charger to a merchant location.
    
    Args:
        db: Database session
        merchant_lat: Merchant latitude
        merchant_lng: Merchant longitude
        zone_slug: Optional zone slug to filter chargers (not used currently, but kept for future use)
    
    Returns:
        Tuple of (charger_id, distance_m) or (None, None) if no chargers found
    """
    # Query all chargers (zone filtering can be added later if needed)
    chargers = db.query(Charger).filter(
        Charger.lat.isnot(None),
        Charger.lng.isnot(None)
    ).all()
    
    if not chargers:
        return (None, None)
    
    # Find nearest charger using Haversine distance
    nearest_charger = None
    min_distance = float('inf')
    
    for charger in chargers:
        distance = haversine_distance(
            merchant_lat, merchant_lng,
            charger.lat, charger.lng
        )
        if distance < min_distance:
            min_distance = distance
            nearest_charger = charger
    
    if nearest_charger:
        return (nearest_charger.id, int(round(min_distance)))
    
    return (None, None)








