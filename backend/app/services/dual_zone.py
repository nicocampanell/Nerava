from datetime import datetime
from math import asin, cos, radians, sin, sqrt

from sqlalchemy.orm import Session

from app.models_extra import DualZoneSession


def haversine_m(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in meters using Haversine formula"""
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2*asin(sqrt(a))
    return R*c

def start_session(db: Session, user_id: str, charger_id: str, merchant_id: str,
                  charger_radius_m: int = 40, merchant_radius_m: int = 100, dwell_threshold_s: int = 300):
    """Start a new dual-zone verification session"""
    s = DualZoneSession(
        user_id=user_id, charger_id=charger_id, merchant_id=merchant_id,
        charger_radius_m=charger_radius_m, merchant_radius_m=merchant_radius_m,
        dwell_threshold_s=dwell_threshold_s, status="pending"
    )
    db.add(s); db.commit(); db.refresh(s)
    return s

def update_positions_and_verify(db: Session, sess_id: int, now_pos: dict, charger_pos: dict, merchant_pos: dict):
    """Update session with current positions and verify if conditions are met"""
    s: DualZoneSession = db.query(DualZoneSession).get(sess_id)
    if not s or s.status != "pending":
        return s

    now = datetime.utcnow()

    # record first time within charger radius
    d1 = haversine_m(now_pos["lat"], now_pos["lng"], charger_pos["lat"], charger_pos["lng"])
    if d1 <= (s.charger_radius_m or 40) and not s.charger_entered_at:
        s.charger_entered_at = now

    # record merchant dwell if within R2 after charger enter
    d2 = haversine_m(now_pos["lat"], now_pos["lng"], merchant_pos["lat"], merchant_pos["lng"])
    if s.charger_entered_at and d2 <= (s.merchant_radius_m or 100):
        if not s.merchant_entered_at:
            s.merchant_entered_at = now
        else:
            s.dwell_seconds = int((now - s.merchant_entered_at).total_seconds())
            if s.dwell_seconds >= (s.dwell_threshold_s or 300):
                s.verified_at = now
                s.status = "verified"

    db.commit(); db.refresh(s)
    return s
