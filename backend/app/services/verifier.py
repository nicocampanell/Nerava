"""Verification service for event check-ins."""

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.events import get_event_by_id
from app.services.geo import haversine_m
from app.services.pool import split_and_credit_verified_attendee
from app.utils.log import get_logger

logger = get_logger("verifier")


def start_verification(
    db: Session,
    user_id: int,
    event_id: int,
    mode: str = "geo",
    charger_id: Optional[str] = None
) -> dict:
    """
    Start a verification process.
    
    Args:
        db: Database session
        user_id: User being verified
        event_id: Event ID
        mode: Verification mode ('geo')
        charger_id: Optional charger ID
        
    Returns:
        Verification row dict
    """
    # Create verification record
    result = db.execute(text("""
        INSERT INTO verifications (
            user_id, event_id, mode, charger_id, started_at, status
        ) VALUES (
            :user_id, :event_id, :mode, :charger_id, CURRENT_TIMESTAMP, 'pending'
        )
    """), {
        "user_id": user_id,
        "event_id": event_id,
        "mode": mode,
        "charger_id": charger_id
    })
    
    db.commit()
    verification_id = result.lastrowid
    
    logger.info("verification_started", extra={
        "verification_id": verification_id,
        "user_id": user_id,
        "event_id": event_id,
        "mode": mode
    })
    
    # Fetch and return
    result = db.execute(text("""
        SELECT * FROM verifications WHERE id = :verification_id
    """), {"verification_id": verification_id})
    
    return dict(result.first()._mapping)


def complete_verification(
    db: Session,
    verification_id: int,
    lat: float,
    lng: float
) -> dict:
    """
    Complete a verification and award rewards if passed.
    
    Args:
        db: Database session
        verification_id: Verification ID
        lat: User's latitude
        lng: User's longitude
        
    Returns:
        Result dict with status, reward_cents, etc.
    """
    # Load verification
    result = db.execute(text("""
        SELECT * FROM verifications WHERE id = :verification_id
    """), {"verification_id": verification_id})
    
    verification = result.first()
    if not verification:
        raise ValueError(f"Verification {verification_id} not found")
    
    verification = dict(verification._mapping)
    event_id = verification["event_id"]
    
    # Load event
    event = get_event_by_id(db, event_id)
    if not event:
        raise ValueError(f"Event {event_id} not found")
    
    # Check time window
    now = datetime.utcnow()
    
    # Parse event timestamps if they're strings
    starts_at = event["starts_at"]
    ends_at = event["ends_at"]
    
    if isinstance(starts_at, str):
        # Remove timezone info for comparison
        starts_at = starts_at.split('+')[0].split('Z')[0].strip()
        if "T" in starts_at:
            starts_at = datetime.fromisoformat(starts_at)
        else:
            starts_at = datetime.strptime(starts_at, "%Y-%m-%d %H:%M:%S")
    
    if isinstance(ends_at, str):
        ends_at = ends_at.split('+')[0].split('Z')[0].strip()
        if "T" in ends_at:
            ends_at = datetime.fromisoformat(ends_at)
        else:
            ends_at = datetime.strptime(ends_at, "%Y-%m-%d %H:%M:%S")
    
    # Demo mode: allow verification at any time
    if settings.demo_mode:
        logger.info("Demo mode enabled: bypassing time window check")
        time_check_passed = True
    else:
        window_start = starts_at - timedelta(minutes=settings.verify_time_window_lead_min)
        window_end = ends_at + timedelta(minutes=settings.verify_time_window_tail_min)
        time_check_passed = window_start <= now <= window_end
        logger.info("Time window check: window_start=%s, window_end=%s, now=%s, passed=%s",
                    window_start, window_end, now, time_check_passed)
    
    # Check distance
    event_lat = event.get("lat")
    event_lng = event.get("lng")
    
    distance_check_passed = False
    if event_lat is not None and event_lng is not None:
        distance = haversine_m(lat, lng, event_lat, event_lng)
        distance_check_passed = distance <= settings.verify_geo_radius_m
    
    passed = time_check_passed and distance_check_passed
    
    # Update verification
    status = "passed" if passed else "failed"
    meta_json = None
    if not passed:
        reasons = []
        if not time_check_passed:
            reasons.append("outside_time_window")
        if not distance_check_passed:
            reasons.append("too_far_from_event")
        meta_json = json.dumps({"reason": ", ".join(reasons)})
    
    db.execute(text("""
        UPDATE verifications
        SET status = :status,
            completed_at = CURRENT_TIMESTAMP,
            lat = :lat, lng = :lng,
            meta_json = :meta_json
        WHERE id = :verification_id
    """), {
        "status": status,
        "lat": lat,
        "lng": lng,
        "meta_json": meta_json,
        "verification_id": verification_id
    })
    
    db.commit()
    
    reward_result = {"reward_cents": 0, "pool_contribution_cents": 0}
    
    if passed:
        # Update attendance
        db.execute(text("""
            UPDATE event_attendance
            SET state = 'verified',
                verified_at = CURRENT_TIMESTAMP
            WHERE event_id = :event_id AND user_id = :user_id
        """), {
            "event_id": event_id,
            "user_id": verification["user_id"]
        })
        
        db.commit()
        
        # Award reward
        reward_result = split_and_credit_verified_attendee(
            db, event_id, verification["user_id"]
        )
        
        logger.info("verification_passed", extra={
            "verification_id": verification_id,
            "user_id": verification["user_id"],
            "event_id": event_id,
            "reward_cents": reward_result.get("reward_cents", 0)
        })
    else:
        logger.info("verification_failed", extra={
            "verification_id": verification_id,
            "user_id": verification["user_id"],
            "event_id": event_id,
            "reason": meta_json
        })
    
    return {
        "status": status,
        "reward_cents": reward_result.get("reward_cents", 0),
        "pool_contribution_cents": reward_result.get("pool_contribution_cents", 0)
    }

