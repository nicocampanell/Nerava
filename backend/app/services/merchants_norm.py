from typing import Dict, List, Optional

from app.services.places_google import search_nearby


def _dd_pickup_link(name: str, lat: float, lng: float) -> Optional[str]:
    return f"https://www.doordash.com/search/store/{name.replace(' ','%20')}/?lat={lat}&lng={lng}"

def _ot_reserve_link(name: str, lat: float, lng: float) -> Optional[str]:
    return f"https://www.opentable.com/s?covers=2&currentlocationid=0&latitude={lat}&longitude={lng}&term={name.replace(' ','%20')}"

def nearby_normalized(lat: float, lng: float, radius_m: int = 450) -> List[Dict]:
    places = search_nearby(lat, lng, radius_m)
    out = []
    for p in places:
        cats = p.get("nerava_categories", [])
        badge = p.get("nerava_badge")
        card = {
            "name": p["name"],
            "badge": badge,
            "categories": cats,
            "logo": p.get("logo"),
            "distance_hint": "walkable",
            "links": {}
        }
        if "quick_bite" in cats or "coffee_drinks" in cats:
            card["links"]["pickup"] = _dd_pickup_link(p["name"], lat, lng)
        if "dining_sitdown" in cats:
            card["links"]["reserve"] = _ot_reserve_link(p["name"], lat, lng)
        out.append(card)
    return out
