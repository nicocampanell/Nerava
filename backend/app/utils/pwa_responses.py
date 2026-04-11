import os

"""
PWA Response Shaping Utilities

Helpers for normalizing API responses to be PWA-friendly:
- All numbers as integers (no floats)
- Consistent object shapes
- Remove internal fields
"""
from typing import Any, Dict, List, Optional


def google_photo_url(photo_reference: Optional[str], max_width: int = 160) -> Optional[str]:
    """
    Convert Google Places photo reference to a full photo URL.

    Args:
        photo_reference: Google Places photo_reference string
        max_width: Maximum width in pixels (default 160)

    Returns:
        Full Google Places photo URL or None if photo_reference is missing
    """
    if not photo_reference:
        return None

    # Hardcoded API key (matches other Google Places integrations)
    API_KEY = os.getenv("GOOGLE_API_KEY", "")

    return (
        "https://maps.googleapis.com/maps/api/place/photo"
        f"?maxwidth={max_width}"
        f"&photoreference={photo_reference}"
        f"&key={API_KEY}"
    )


def normalize_number(value: Any) -> int:
    """Convert any number-like value to integer."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(value))
    try:
        return int(round(float(value)))
    except (ValueError, TypeError):
        return 0


def shape_charger(
    charger: Dict[str, Any],
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    merchants: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Shape charger object for PWA consumption.

    Args:
        charger: Charger dict to shape
        user_lat: Optional user latitude for distance calculation
        user_lng: Optional user longitude for distance calculation
        merchants: Optional list of merchants to attach to this charger

    Returns consistent shape:
    {
        "id": str,
        "name": str,
        "lat": float,
        "lng": float,
        "network_name": str (optional),
        "distance_m": int (if user_lat/lng provided),
        "walk_time_s": int (if user_lat/lng provided),
        "merchants": List[Dict] (if merchants provided)
    }
    """
    result = {
        "id": str(charger.get("id", "")),
        "name": str(charger.get("name", "")),
        "lat": float(charger.get("lat", 0)),
        "lng": float(charger.get("lng", 0)),
    }

    # Optional fields
    if "network_name" in charger:
        result["network_name"] = str(charger.get("network_name", ""))

    # Distance if user location provided
    if user_lat is not None and user_lng is not None:
        from app.services.geo import haversine_m

        distance = haversine_m(user_lat, user_lng, result["lat"], result["lng"])
        result["distance_m"] = normalize_number(distance)

    # Walk time (if provided in charger dict)
    if "walk_time_s" in charger:
        result["walk_time_s"] = normalize_number(charger.get("walk_time_s"))
    elif "walk_duration_s" in charger:
        result["walk_time_s"] = normalize_number(charger.get("walk_duration_s"))

    # Attach merchants if provided (merchants should already be shaped separately)
    if merchants is not None:
        result["merchants"] = merchants
    elif "merchants" in charger:
        # Keep existing merchants array if already present
        result["merchants"] = charger["merchants"]

    return result


def shape_merchant(
    merchant: Dict[str, Any], user_lat: Optional[float] = None, user_lng: Optional[float] = None
) -> Dict[str, Any]:
    """
    Shape merchant object for PWA consumption.

    Returns consistent shape:
    {
        "id": str,
        "name": str,
        "lat": float,
        "lng": float,
        "category": str (optional),
        "nova_reward": int (optional),
        "logo_url": str (optional),
        "distance_m": int (if user_lat/lng provided),
        "walk_time_s": int (if user_lat/lng provided)
    }
    """
    result = {
        "id": str(merchant.get("id", "")),
        "name": str(merchant.get("name", "")),
        "lat": float(merchant.get("lat", 0)),
        "lng": float(merchant.get("lng", 0)),
    }

    # Optional fields
    if "category" in merchant:
        result["category"] = str(merchant.get("category", ""))

    # Nova reward (important for perk display)
    if "nova_reward" in merchant:
        result["nova_reward"] = normalize_number(merchant.get("nova_reward", 0))

    # Logo URL - handle multiple sources (prioritize actual photos over generic icons):
    # 1. photo_url (Google Places photo reference) - convert to full URL (best option)
    # 2. Direct logo_url (if already a full URL, but prefer photo_url if both exist)
    # 3. icon (from Google Places icon) - fallback
    logo_url = None

    # Prioritize photo_url over logo_url (photos are better than generic icons)
    if "photo_url" in merchant and merchant.get("photo_url"):
        photo_ref = merchant.get("photo_url")
        # If it's not already a full URL, convert it using Google Places photo API
        if photo_ref and str(photo_ref).strip():
            photo_ref_str = str(photo_ref).strip()
            if photo_ref_str.startswith("http"):
                # Already a full URL
                logo_url = photo_ref_str
            elif photo_ref_str.startswith("/"):
                # Local asset path - use as-is (don't transform to Google Places URL)
                logo_url = photo_ref_str
            else:
                # Assume Google Places photo reference - convert to full URL
                logo_url = google_photo_url(photo_ref_str)

    # If no photo_url, try logo_url (might be a custom logo or icon)
    if not logo_url and "logo_url" in merchant and merchant.get("logo_url"):
        logo_url_val = merchant.get("logo_url", "")
        # Only use if it's a valid URL (not just empty string or None)
        if logo_url_val and str(logo_url_val).strip():
            logo_url_str = str(logo_url_val).strip()
            if logo_url_str.startswith("http") or logo_url_str.startswith("/"):
                # Full URL or local asset path - use as-is
                logo_url = logo_url_str
            else:
                # Assume Google Places photo reference - convert to full URL
                logo_url = google_photo_url(logo_url_str)

    # If still no logo, try icon (Google Places generic icon) as last resort
    # For now, include even generic icons so we can see what merchants have
    # TODO: Once we confirm merchants have proper photos, we can filter generic icons
    if not logo_url and "icon" in merchant and merchant.get("icon"):
        icon_val = merchant.get("icon", "")
        if icon_val and str(icon_val).strip():
            logo_url = str(icon_val).strip()

    # Include logo_url if we have any URL (even generic icons for debugging)
    if logo_url and logo_url.strip():
        result["logo_url"] = logo_url.strip()
    # Also check if logo_url was explicitly set to None/empty in original merchant
    elif "logo_url" in merchant:
        # Explicitly set to None if it was in the original but empty
        # This helps frontend distinguish between "not present" and "explicitly empty"
        pass  # Don't include it if it's empty

    # Distance if user location provided
    if user_lat is not None and user_lng is not None:
        from app.services.geo import haversine_m

        distance = haversine_m(user_lat, user_lng, result["lat"], result["lng"])
        result["distance_m"] = normalize_number(distance)

    # Walk time (if provided)
    if "walk_time_s" in merchant:
        result["walk_time_s"] = normalize_number(merchant.get("walk_time_s"))
    elif "walk_duration_s" in merchant:
        result["walk_time_s"] = normalize_number(merchant.get("walk_duration_s"))
    elif "walk_minutes" in merchant:
        # Convert walk_minutes to walk_time_s
        result["walk_time_s"] = normalize_number(merchant.get("walk_minutes", 0) * 60)

    return result


def shape_error(error_type: str, message: str) -> Dict[str, Any]:
    """
    Shape error response for PWA consumption.

    Args:
        error_type: "NotFound" | "BadRequest" | "Unauthorized" | "Internal"
        message: Human-readable error message

    Returns:
        {
            "error": {
                "type": str,
                "message": str
            }
        }
    """
    return {"error": {"type": error_type, "message": message}}
