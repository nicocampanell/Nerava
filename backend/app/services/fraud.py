"""
Anti-fraud services: device tracking, risk scoring, abuse event logging
"""
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.geo import haversine_m
from app.utils.log import get_logger

logger = get_logger(__name__)


def hash_device(ip: str, ua: str, accept_lang: Optional[str] = None, platform: Optional[str] = None) -> str:
    """
    Generate a device fingerprint hash from IP, UA, and optional signals.
    """
    components = [ip or "", ua or "", accept_lang or "", platform or ""]
    combined = "|".join(components)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def record_verify_attempt(
    db: Session,
    *,
    user_id: int,
    session_id: Optional[str],
    ip: Optional[str],
    ua: Optional[str],
    accuracy_m: Optional[float],
    outcome: str
):
    """
    Record a verify attempt for audit and risk scoring.
    """
    try:
        db.execute(text("""
            INSERT INTO verify_attempts (
                user_id, session_id, ip, ua, accuracy_m, outcome, created_at
            ) VALUES (
                :user_id, :session_id, :ip, :ua, :accuracy_m, :outcome, :created_at
            )
        """), {
            "user_id": user_id,
            "session_id": session_id,
            "ip": ip,
            "ua": ua,
            "accuracy_m": accuracy_m,
            "outcome": outcome,
            "created_at": datetime.utcnow()
        })
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to record verify attempt: {e}")


def touch_device(
    db: Session,
    *,
    user_id: int,
    device_hash: str,
    ua: Optional[str],
    ip: Optional[str]
):
    """
    Record or update device fingerprint for a user.
    """
    try:
        # Check if exists
        existing = db.execute(text("""
            SELECT id FROM device_fingerprints
            WHERE user_id = :user_id AND device_hash = :device_hash
            LIMIT 1
        """), {
            "user_id": user_id,
            "device_hash": device_hash
        }).first()
        
        now = datetime.utcnow()
        
        if existing:
            # Update
            db.execute(text("""
                UPDATE device_fingerprints
                SET last_seen = :now, ua = :ua, last_ip = :ip
                WHERE id = :id
            """), {
                "id": existing[0],
                "now": now,
                "ua": ua,
                "ip": ip
            })
        else:
            # Insert
            db.execute(text("""
                INSERT INTO device_fingerprints (
                    user_id, device_hash, first_seen, last_seen, ua, last_ip
                ) VALUES (
                    :user_id, :device_hash, :now, :now, :ua, :ip
                )
            """), {
                "user_id": user_id,
                "device_hash": device_hash,
                "now": now,
                "ua": ua,
                "ip": ip
            })
        
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to touch device: {e}")


def compute_risk_score(db: Session, *, user_id: int, now: datetime) -> Dict[str, Any]:
    """
    Compute risk score for a user based on various heuristics.
    Returns: {"score": int, "reasons": List[str]}
    """
    score = 0
    reasons = []
    
    # 1. Verify attempts per hour
    hour_ago = now - timedelta(hours=1)
    verify_count = db.execute(text("""
        SELECT COUNT(*) FROM verify_attempts
        WHERE user_id = :user_id AND created_at >= :hour_ago
    """), {"user_id": user_id, "hour_ago": hour_ago}).scalar()
    verify_count = int(verify_count) if verify_count else 0
    
    if verify_count > settings.max_verify_per_hour:
        score += 30
        reasons.append(f"verify_attempts_per_hour: {verify_count} > {settings.max_verify_per_hour}")
    
    # 2. Sessions started per hour
    session_count = db.execute(text("""
        SELECT COUNT(*) FROM sessions
        WHERE user_id = :user_id AND started_at >= :hour_ago
    """), {"user_id": str(user_id), "hour_ago": hour_ago}).scalar()
    session_count = int(session_count) if session_count else 0
    
    if session_count > settings.max_sessions_per_hour:
        score += 30
        reasons.append(f"sessions_per_hour: {session_count} > {settings.max_sessions_per_hour}")
    
    # 3. Distinct IPs per day
    day_ago = now - timedelta(days=1)
    distinct_ips = db.execute(text("""
        SELECT COUNT(DISTINCT ip) FROM verify_attempts
        WHERE user_id = :user_id AND created_at >= :day_ago AND ip IS NOT NULL
    """), {"user_id": user_id, "day_ago": day_ago}).scalar()
    distinct_ips = int(distinct_ips) if distinct_ips else 0
    
    if distinct_ips > settings.max_different_ips_per_day:
        score += 30
        reasons.append(f"distinct_ips_per_day: {distinct_ips} > {settings.max_different_ips_per_day}")
    
    # 4. Accuracy violations (passed as parameter in caller)
    # This is checked per-attempt, not aggregated here
    
    # 5. Geo jump check (requires previous session location)
    recent_session = db.execute(text("""
        SELECT lat, lng, started_at FROM sessions
        WHERE user_id = :user_id
        AND status = 'verified'
        AND lat IS NOT NULL AND lng IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 2
    """), {"user_id": str(user_id)}).fetchall()
    
    if len(recent_session) >= 2:
        current = recent_session[0]
        previous = recent_session[1]
        
        current_lat = float(current[0])
        current_lng = float(current[1])
        current_ts = current[2]
        
        prev_lat = float(previous[0])
        prev_lng = float(previous[1])
        prev_ts = previous[2]
        
        # Check if within 15 minutes
        if isinstance(current_ts, str):
            try:
                current_dt = datetime.fromisoformat(current_ts.replace('Z', '+00:00')[:19])
            except:
                current_dt = now
        else:
            current_dt = current_ts
        
        if isinstance(prev_ts, str):
            try:
                prev_dt = datetime.fromisoformat(prev_ts.replace('Z', '+00:00')[:19])
            except:
                prev_dt = now
        else:
            prev_dt = prev_ts
        
        time_diff = abs((current_dt - prev_dt).total_seconds())
        
        if time_diff <= 900:  # 15 minutes
            # Calculate distance in km
            distance_m = haversine_m(
                current_lat, current_lng,
                prev_lat, prev_lng
            )
            distance_km = distance_m / 1000.0
            
            if distance_km > settings.max_geo_jump_km:
                score += 40
                reasons.append(f"geo_jump: {distance_km:.1f}km within 15min > {settings.max_geo_jump_km}km")
    
    return {
        "score": score,
        "reasons": reasons
    }


def emit_abuse_event(
    db: Session,
    *,
    user_id: int,
    event_type: str,
    severity: int,
    details: Dict[str, Any]
):
    """
    Emit an abuse event for logging and alerting.
    """
    try:
        db.execute(text("""
            INSERT INTO abuse_events (
                user_id, type, severity, details_json, created_at
            ) VALUES (
                :user_id, :type, :severity, :details_json, :created_at
            )
        """), {
            "user_id": user_id,
            "type": event_type,
            "severity": severity,
            "details_json": json.dumps(details),
            "created_at": datetime.utcnow()
        })
        db.commit()
        
        logger.info(
            f"Abuse event: user_id={user_id}, type={event_type}, severity={severity}",
            extra={
                "at": "fraud",
                "step": "abuse_event",
                "uid": user_id,
                "type": event_type,
                "severity": severity,
                "extra": details
            }
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to emit abuse event: {e}")

