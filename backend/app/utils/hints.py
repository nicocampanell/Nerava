"""Empty state hints for discovery endpoints."""

from app.config import settings


def build_empty_state_hint(
    kind: str,
    lat: float,
    lng: float,
    radius_m: int,
    category: str = None
) -> dict:
    """
    Build hint suggestions for empty results.
    
    Args:
        kind: "merchants" or "chargers"
        lat: User latitude
        lng: User longitude
        radius_m: Current search radius
        category: Current category filter (if any)
        
    Returns:
        Hint dict with suggestions and next_steps
    """
    suggestions = []
    next_steps = ""
    
    if kind == "merchants":
        # Suggest widening radius
        if radius_m < 2000:
            suggestions.append(f"Increase radius to {radius_m + 1000}m")
            suggestions.append(f"Increase radius to {radius_m + 2000}m")
        
        # Suggest category filters
        if not category:
            suggestions.append("Try category=coffee")
            suggestions.append("Try category=gym")
            suggestions.append("Try category=wellness")
        
        # Suggest city fallback
        suggestions.append(f"Try city={settings.city_fallback}")
        
        next_steps = "Ask GPT: 'Search 2km radius for coffee shops' or 'Show me nearby wellness options'"
    
    elif kind == "chargers":
        # Suggest widening radius
        if radius_m < 3000:
            suggestions.append(f"Increase radius to {radius_m + 1000}m")
            suggestions.append(f"Increase radius to {radius_m + 2000}m")
        
        # Suggest city fallback
        suggestions.append(f"Try city={settings.city_fallback}")
        
        next_steps = "Ask GPT: 'Search 3km radius for chargers'"
    
    return {
        "suggestions": suggestions,
        "next_steps": next_steps
    }


