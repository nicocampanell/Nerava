# app/services/chargers_openmap.py
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

import requests

OPENCHARGEMAP_API_KEY = "a6ffbff8-a8d5-4314-a0fb-0e599d05f72f"

# Simple in-proc cache: {(gridLat, gridLng, radius_km): (ts, data)}
_CACHE: Dict[Tuple[int, int, int], Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SEC = 120

OPERATOR_MAP = {
    23: "tesla",
    3534: "tesla",
    5: "chargepoint",
    29: "evgo",
    52: "blink",
    1025: "ampup",
}

def _grid_key(lat: float, lng: float, radius_km: float) -> Tuple[int, int, int]:
    return (int(lat * 1000), int(lng * 1000), int(radius_km * 10))

def _normalize_operator(op_id: Any) -> str:
    try:
        op_id = int(op_id) if op_id is not None else None
    except Exception:
        op_id = None
    return OPERATOR_MAP.get(op_id, "other")

async def fetch_chargers(lat: float, lng: float, radius_km: float = 2.0, max_results: int = 50) -> List[Dict[str, Any]]:
    key = _grid_key(lat, lng, radius_km)
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < _CACHE_TTL_SEC:
            return data

    url = "https://api.openchargemap.io/v3/poi/"
    params = {
        "output": "json",
        "countrycode": "US",
        "latitude": lat,
        "longitude": lng,
        "distance": radius_km,
        "maxresults": max_results,
        "compact": "true",
        "key": OPENCHARGEMAP_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    items = resp.json()

    results: List[Dict[str, Any]] = []
    for it in items:
        addr = it.get("AddressInfo") or {}
        conns = it.get("Connections") or []
        total_ports = 0
        if isinstance(conns, list) and conns:
            qty_sum = 0
            for c in conns:
                qty = c.get("Quantity")
                try:
                    qty_sum += int(qty) if qty is not None else 1
                except Exception:
                    qty_sum += 1
            total_ports = max(1, qty_sum)

        op_name = _normalize_operator(it.get("OperatorID"))
        results.append({
            "id": it.get("ID"),
            "title": addr.get("Title") or it.get("GeneralComments") or "Charger",
            "operator": op_name,
            "status_type_id": it.get("StatusTypeID"),
            "usage_cost": it.get("UsageCost"),
            "total_ports": total_ports,
            "address": {
                "line1": addr.get("AddressLine1"),
                "town": addr.get("Town"),
                "state": addr.get("StateOrProvince"),
                "postcode": addr.get("Postcode"),
            },
            "location": {
                "lat": addr.get("Latitude"),
                "lng": addr.get("Longitude"),
            },
            "last_status_ts": it.get("DateLastStatusUpdate"),
            "raw": {
                "OperatorID": it.get("OperatorID"),
                "Connections_len": len(conns) if isinstance(conns, list) else 0,
            }
        })

    _CACHE[key] = (now, results)
    return results
