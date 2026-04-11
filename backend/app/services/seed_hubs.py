from typing import Dict, List, Optional

_SEEDED = [
    {
        "id": "hub_domain_A",
        "name": "Domain • Hub A",
        "lat": 30.4021, "lng": -97.7265,
        "total_ports": 12, "free_ports": 3,
        "network_mix": ["chargepoint","tesla"],
        "tier": "premium"
    },
    {
        "id": "hub_domain_B",
        "name": "Domain • Hub B",
        "lat": 30.4039, "lng": -97.7250,
        "total_ports": 8, "free_ports": 1,
        "network_mix": ["chargepoint"],
        "tier": "standard"
    },
    {
        "id": "hub_ut_quarters",
        "name": "UT • The Quarters on Campus",
        "lat": 30.2883, "lng": -97.7423,
        "total_ports": 2, "free_ports": 1,
        "network_mix": ["ampup"],
        "tier": "reservable"
    }
]

def list_hubs() -> List[Dict]:
    return list(_SEEDED)

def get_hub(hub_id: str) -> Optional[Dict]:
    for h in _SEEDED:
        if h["id"] == hub_id: return h
    return None
