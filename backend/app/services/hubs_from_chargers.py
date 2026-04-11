import math
from typing import Any, Dict, List, Tuple


# Simple grid clustering for now
def _grid_key(lat: float, lng: float, cell_m: float = 150.0) -> Tuple[int, int]:
    # rough meters per degree lat/lng near Austin
    m_per_deg_lat = 111_132.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(lat or 0.0))
    cell_deg_lat = cell_m / m_per_deg_lat
    cell_deg_lng = cell_m / max(m_per_deg_lng, 1e-3)
    gx = int((lng or 0.0) / cell_deg_lng)
    gy = int((lat or 0.0) / cell_deg_lat)
    return gx, gy

def _tier_from_counts(total_ports: int) -> str:
    if total_ports >= 12:
        return "premium"
    if total_ports >= 4:
        return "standard"
    return "lite"

def build_hubs_from_chargers(chargers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(chargers, list):
        return []

    buckets: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
    for ch in chargers:
        if not isinstance(ch, dict):
            continue
        loc = ch.get("location") or {}
        lat = loc.get("lat")
        lng = loc.get("lng")
        if lat is None or lng is None:
            continue
        key = _grid_key(lat, lng, cell_m=150.0)
        buckets.setdefault(key, []).append(ch)

    hubs: List[Dict[str, Any]] = []
    idx = 0
    for key, items in buckets.items():
        if not items:
            continue
        # centroid
        lats = [i.get("location", {}).get("lat") for i in items if isinstance(i, dict)]
        lngs = [i.get("location", {}).get("lng") for i in items if isinstance(i, dict)]
        lat_c = sum([x for x in lats if isinstance(x, (int, float))]) / max(len(lats), 1)
        lng_c = sum([x for x in lngs if isinstance(x, (int, float))]) / max(len(lngs), 1)

        total_ports = sum(int(i.get("total_ports") or 0) for i in items if isinstance(i, dict))
        networks = set()
        for i in items:
            op = i.get("operator")
            if isinstance(op, str) and op:
                networks.add(op.lower())

        hub = {
            "id": f"hub_dyn_{idx}",
            "name": f"Nerava Hub • {idx+1}",
            "lat": lat_c,
            "lng": lng_c,
            "total_ports": total_ports,
            "free_ports": max(0, math.floor(total_ports * 0.25)),  # placeholder until live status
            "network_mix": sorted(list(networks))[:4],
            "tier": _tier_from_counts(total_ports),
            "members": [i.get("id") for i in items if isinstance(i, dict)],
        }
        hubs.append(hub)
        idx += 1

    # sort largest first
    hubs.sort(key=lambda h: h.get("total_ports", 0), reverse=True)
    return hubs

def build_hubs_nearby(
    lat: float,
    lng: float,
    radius_km: float = 2.0,
    max_results: int = 120,
) -> List[Dict[str, Any]]:
    """
    This module shouldn’t call external APIs; your router calls fetch_chargers
    and passes the list in, or you keep that in a different service.
    If you currently import fetch_chargers here, ensure you handle None safely.
    """
    # If you currently have this module calling fetch_chargers directly,
    # you can import and wire it, but keep the same defensive stance:
    from app.services.chargers_openmap import fetch_chargers
    chargers = fetch_chargers(lat=lat, lng=lng, distance_km=radius_km, limit=max_results)
    if not isinstance(chargers, list):
        chargers = []
    return build_hubs_from_chargers(chargers)
