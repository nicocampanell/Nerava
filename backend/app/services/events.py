"""Event management service."""

import json
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.geo import haversine_m
from app.utils.log import get_logger

logger = get_logger("events")


def create_event(db: Session, activator_id: int, payload: dict) -> dict:
    """
    Create a new event.
    
    Args:
        db: Database session
        activator_id: User creating the event
        payload: Event data (title, description, lat, lng, starts_at, ends_at, etc.)
        
    Returns:
        Created event row as dict
    """
    # Normalize green_window times to HH:MM strings
    green_window_start = payload.get("green_window_start")
    if green_window_start and len(green_window_start) > 5:
        green_window_start = green_window_start[:5]  # Keep only HH:MM
    
    green_window_end = payload.get("green_window_end")
    if green_window_end and len(green_window_end) > 5:
        green_window_end = green_window_end[:5]
    
    # Default visibility and status
    visibility = payload.get("visibility", "public")
    status = payload.get("status", "scheduled")
    
    # Prepare revenue_split_json
    revenue_split_json = None
    if "revenue_split" in payload:
        revenue_split_json = json.dumps(payload["revenue_split"])
    
    result = db.execute(text("""
        INSERT INTO events (
            activator_id, title, description, category, city,
            lat, lng, starts_at, ends_at,
            green_window_start, green_window_end,
            price_cents, revenue_split_json, capacity,
            visibility, status
        ) VALUES (
            :activator_id, :title, :description, :category, :city,
            :lat, :lng, :starts_at, :ends_at,
            :green_window_start, :green_window_end,
            :price_cents, :revenue_split_json, :capacity,
            :visibility, :status
        )
    """), {
        "activator_id": activator_id,
        "title": payload["title"],
        "description": payload.get("description"),
        "category": payload.get("category"),
        "city": payload.get("city"),
        "lat": payload.get("lat"),
        "lng": payload.get("lng"),
        "starts_at": payload["starts_at"],
        "ends_at": payload["ends_at"],
        "green_window_start": green_window_start,
        "green_window_end": green_window_end,
        "price_cents": payload.get("price_cents", 0),
        "revenue_split_json": revenue_split_json,
        "capacity": payload.get("capacity"),
        "visibility": visibility,
        "status": status
    })
    
    db.commit()
    event_id = result.lastrowid
    
    logger.info("event_created", extra={
        "event_id": event_id,
        "activator_id": activator_id,
        "title": payload["title"]
    })
    
    # Fetch and return the created event
    return get_event_by_id(db, event_id)


def get_event_by_id(db: Session, event_id: int) -> Optional[dict]:
    """Get event by ID."""
    result = db.execute(text("""
        SELECT * FROM events WHERE id = :event_id
    """), {"event_id": event_id})
    
    row = result.first()
    if not row:
        return None
    
    event = dict(row._mapping)
    
    # Parse revenue_split_json
    if event.get("revenue_split_json"):
        event["revenue_split"] = json.loads(event["revenue_split_json"])
    else:
        event["revenue_split"] = None
    
    return event


def list_events_nearby(
    db: Session,
    lat: float,
    lng: float,
    radius_m: int = 2000,
    now_utc: Optional[datetime] = None
) -> List[dict]:
    """
    List events within radius, starting within next 24 hours.
    
    Args:
        db: Database session
        lat: Query latitude
        lng: Query longitude
        radius_m: Search radius in meters
        now_utc: Current time (defaults to UTC now)
        
    Returns:
        List of event dicts with distance_m and capacity_left
    """
    if now_utc is None:
        now_utc = datetime.utcnow()
    
    # Look for events starting within next 24 hours
    window_start = now_utc
    window_end = now_utc + timedelta(hours=24)
    
    # Fetch all events in the time window
    result = db.execute(text("""
        SELECT * FROM events
        WHERE starts_at >= :window_start
          AND starts_at <= :window_end
          AND status IN ('scheduled', 'live')
          AND visibility = 'public'
    """), {
        "window_start": window_start,
        "window_end": window_end
    })
    
    events = []
    for row in result:
        event = dict(row._mapping)
        event_lat = event.get("lat")
        event_lng = event.get("lng")
        
        if event_lat is None or event_lng is None:
            continue
        
        # Compute distance
        distance = haversine_m(lat, lng, event_lat, event_lng)
        
        if distance <= radius_m:
            # Compute capacity_left
            capacity = event.get("capacity")
            capacity_left = None
            if capacity is not None:
                attendance_count = db.execute(text("""
                    SELECT COUNT(*) FROM event_attendance
                    WHERE event_id = :event_id AND state NOT IN ('refunded')
                """), {"event_id": event["id"]}).scalar()
                capacity_left = max(0, capacity - attendance_count)
            
            event["distance_m"] = round(distance, 1)
            event["capacity_left"] = capacity_left
            
            # Parse revenue_split_json
            if event.get("revenue_split_json"):
                event["revenue_split"] = json.loads(event["revenue_split_json"])
            else:
                event["revenue_split"] = None
            
            events.append(event)
    
    # Sort by distance
    events.sort(key=lambda x: x["distance_m"])
    
    return events


def join_event(db: Session, event_id: int, user_id: int) -> dict:
    """
    Join an event (idempotent).
    
    Returns:
        Attendance row
    """
    # Check if already joined
    existing = db.execute(text("""
        SELECT * FROM event_attendance
        WHERE event_id = :event_id AND user_id = :user_id
    """), {"event_id": event_id, "user_id": user_id}).first()
    
    if existing:
        return dict(existing._mapping)
    
    # Insert new attendance
    db.execute(text("""
        INSERT INTO event_attendance (event_id, user_id, state, joined_at)
        VALUES (:event_id, :user_id, 'joined', CURRENT_TIMESTAMP)
    """), {"event_id": event_id, "user_id": user_id})
    
    db.commit()
    
    logger.info("event_joined", extra={
        "event_id": event_id,
        "user_id": user_id
    })
    
    # Return the new attendance
    result = db.execute(text("""
        SELECT * FROM event_attendance
        WHERE event_id = :event_id AND user_id = :user_id
    """), {"event_id": event_id, "user_id": user_id})
    
    return dict(result.first()._mapping)


def end_event(db: Session, event_id: int) -> dict:
    """
    End an event (set status to 'ended').
    
    Returns:
        Updated event row
    """
    db.execute(text("""
        UPDATE events
        SET status = 'ended'
        WHERE id = :event_id
    """), {"event_id": event_id})
    
    db.commit()
    
    logger.info("event_ended", extra={"event_id": event_id})
    
    return get_event_by_id(db, event_id)
