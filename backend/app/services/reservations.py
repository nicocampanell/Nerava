# app/services/reservations.py
from datetime import datetime, timedelta, timezone
from typing import Dict


# naive preview builder; you can swap in network-aware rules later
def reserve_preview_for(hub: dict) -> Dict:
    now = datetime.now(timezone.utc)
    free = int(hub.get("free_ports") or 0)

    # assume "real" if hub tier tagged reservable
    rtype = "real" if hub.get("tier") == "reservable" else "soft"

    if free > 0:
        start = now + timedelta(minutes=5)
    else:
        # soft queue—first ETA window 20–40 mins if busy
        start = now + timedelta(minutes=30)

    return {
        "type": rtype,
        "can_hold": True,
        "suggested_start_iso": start.isoformat().replace("+00:00", "Z"),
        "window_min": 30
    }
