"""Notification service (stub with DB logging)."""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.utils.dbjson import as_db_json
from app.utils.log import get_logger

logger = get_logger("notify")


def send_batched(db: Session, user_id: int, kind: str, payload_dict: dict) -> dict:
    """
    Send a notification (logged to DB, no external push yet).
    
    Args:
        db: Database session
        user_id: Target user
        kind: Notification kind (new_event_in_city, friend_joined, etc.)
        payload_dict: Notification payload
        
    Returns:
        {sent: bool, reason: optional str}
    """
    # Rate limit check
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    count_result = db.execute(text("""
        SELECT COUNT(*) FROM notification_logs
        WHERE user_id = :user_id AND sent_at >= :today_start
    """), {"user_id": user_id, "today_start": today_start})
    
    count = count_result.scalar()
    
    if count >= settings.max_push_per_day_per_user:
        logger.info("notification_rate_limited", extra={
            "user_id": user_id,
            "kind": kind,
            "count": count
        })
        return {"sent": False, "reason": "rate_limit"}
    
    # Debounce check (identical kind/payload within 2 hours)
    two_hours_ago = datetime.utcnow() - timedelta(hours=2)
    payload_json_str = as_db_json(payload_dict)
    
    debounce_result = db.execute(text("""
        SELECT COUNT(*) FROM notification_logs
        WHERE user_id = :user_id
          AND kind = :kind
          AND payload_json = :payload_json
          AND sent_at >= :two_hours_ago
    """), {
        "user_id": user_id,
        "kind": kind,
        "payload_json": payload_json_str,
        "two_hours_ago": two_hours_ago
    })
    
    if debounce_result.scalar() > 0:
        logger.info("notification_debounced", extra={
            "user_id": user_id,
            "kind": kind
        })
        return {"sent": False, "reason": "debounced"}
    
    # Insert notification log
    db.execute(text("""
        INSERT INTO notification_logs (
            user_id, kind, payload_json, sent_at
        ) VALUES (
            :user_id, :kind, :payload_json, CURRENT_TIMESTAMP
        )
    """), {
        "user_id": user_id,
        "kind": kind,
        "payload_json": payload_json_str
    })
    
    db.commit()
    
    logger.info("notification_sent", extra={
        "user_id": user_id,
        "kind": kind
    })
    
    return {"sent": True}


# Trigger helpers (stubs for now)
def notify_new_event_in_city(db: Session, city: str, event_row: dict):
    """Notify users in city about new event."""
    # TODO: Query users in city, call send_batched for each
    logger.info("notify_new_event_in_city_called", extra={"city": city, "event_id": event_row.get("id")})


def notify_friend_joined(db: Session, friend_handle: str, event_row: dict):
    """Notify friends when someone joins an event."""
    # TODO: Query followers, call send_batched
    logger.info("notify_friend_joined_called", extra={"friend_handle": friend_handle, "event_id": event_row.get("id")})


def notify_pool_update(db: Session, city: str, delta_cents: int, milestone: Optional[str] = None):
    """Notify users about pool updates."""
    # TODO: Query active users in city, call send_batched
    logger.info("notify_pool_update_called", extra={"city": city, "delta_cents": delta_cents, "milestone": milestone})

