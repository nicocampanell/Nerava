# app/services/hubs_dynamic.py
import hashlib
from typing import Dict, List

from .cache import cache
from .chargers_openmap import fetch_chargers  # async
from .reservations import reserve_preview_for


def _stable_hub_id(lat: float, lng: float, member_ids: List[int]) -> str:
    sig = f"{round(lat,6)}|{round(lng,6)}|{','.join(map(str,sorted(member_ids)))}"
    h = hashlib.md5(sig.encode()).hexdigest()[:8]
    return f"hub_dyn_{h}"

def _status_from_free(free_ports: int) -> str:
    if free_ports is None:
        return "unknown"
    if free_ports == 0:
        return "busy"
    return "open" if free_ports >= 1 else "unknown"

def _eta_hint_min(distance_km: float = 1.0, free_ports: int = 1) -> int:
    base = max(3, int(distance_km * 6))
    return max(3, base - 2 if free_ports > 0 else base + 4)

async def build_dynamic_hubs(lat: float, lng: float, radius_km: float = 2.0, max_results: int = 10) -> List[Dict]:
    """
    Clusters are simple: each charger becomes its own 'mini hub' for the demo.
    (You can replace with true clustering later.)
    """
    chargers = await fetch_chargers(lat=lat, lng=lng, radius_km=radius_km, max_results=max_results)

    hubs: List[Dict] = []
    for ch in chargers:
        member_ids = [ch["id"]]
        hub_id = _stable_hub_id(ch["location"]["lat"], ch["location"]["lng"], member_ids)
        total_ports = int(ch.get("total_ports") or 2)
        free_ports = max(0, int(ch.get("free_ports") or 0))
        status = _status_from_free(free_ports)
        operator = (ch.get("operator") or "unknown").lower()
        network_mix = [operator]
        tier = "reservable" if "ampup" in operator else ("premium" if "tesla" in operator else "standard")

        hubs.append({
            "id": hub_id,
            "name": f"Nerava Hub • {len(hubs)+1}",
            "lat": ch["location"]["lat"],
            "lng": ch["location"]["lng"],
            "total_ports": total_ports,
            "free_ports": free_ports,
            "network_mix": network_mix,
            "tier": tier,
            "members": member_ids,
            "status": status
        })
    return hubs

def score_hub(h: Dict, user_prefs: List[str]) -> Dict:
    score = 0.0
    reason = []
    free = h.get("free_ports") or 0
    if free >= 2:
        score += 33; reason.append(f"{free}-ports-free")
    elif free == 1:
        score += 22; reason.append("1-ports-free")
    tier = h.get("tier")
    if tier == "premium":
        score += 33; reason.append("premium")
    if h.get("status") == "open":
        score += 14; reason.append("open")
    return {**h, "score": round(score, 1), "reason_tags": reason}

def hydrate_hub(h: Dict, lat: float, lng: float, prefs: List[str]) -> Dict:
    from .merchants_google import search_nearby  # local import to avoid cycle
    eta_hint = _eta_hint_min(distance_km=0.8, free_ports=int(h.get("free_ports") or 0))
    preview = reserve_preview_for(h)

    cache_key = f"m:{round(h['lat'],5)}:{round(h['lng'],5)}:{','.join(sorted(prefs) if prefs else [])}"
    merchants = cache.get(cache_key)
    if merchants is None:
        merchants = search_nearby(lat=h["lat"], lng=h["lng"], radius_m=600, prefs=prefs, limit=12)
        cache.set(cache_key, merchants, ttl_seconds=600)

    base = score_hub(h, prefs)
    base["eta_hint_min"] = eta_hint
    base["reserve_preview"] = preview
    base["merchants"] = merchants
    return base
