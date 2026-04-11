"""
Geographic utility functions
"""
from math import asin, cos, radians, sin, sqrt


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two lat/lng points in meters using Haversine formula.
    
    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates
    
    Returns:
        Distance in meters
    """
    R = 6371000.0  # Earth radius in meters
    
    # Convert to radians
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    
    # Haversine formula
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    
    return R * c

