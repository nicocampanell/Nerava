from typing import Dict, List

import httpx

from app.services.categorize import categorize_google_types, summarize_for_badge


def search_nearby(lat: float, lng: float, radius_m: int = 450) -> List[Dict]:
    api_key = "AIzaSyAs0PVYXj3-ztRXCjdd0ztUGUSjQR73FFg"
    if not api_key:
        return []  # seed-only mode
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {"location": f"{lat},{lng}", "radius": radius_m, "key": api_key}
    try:
        r = httpx.get(url, params=params, timeout=10.0)
        r.raise_for_status()
        data = r.json()
        out = []
        for item in data.get("results",[])[:12]:
            gtypes = item.get("types", []) or []
            cats, pref_hits = categorize_google_types(gtypes)
            out.append({
                "name": item.get("name"),
                "logo": item.get("icon"),
                "place_id": item.get("place_id"),
                "vicinity": item.get("vicinity"),
                "google_types": gtypes,
                "nerava_categories": cats,
                "nerava_badge": summarize_for_badge(cats),
                "pref_hits": pref_hits
            })
        return out
    except Exception:
        return []
