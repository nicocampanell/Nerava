"""
Outbox event storage utility

Helper functions for storing domain events in the outbox.
"""
import json
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import DomainEvent

logger = logging.getLogger(__name__)


def store_outbox_event(db: Session, event: DomainEvent) -> None:
    """
    Store a domain event in the outbox for reliable delivery.
    
    This is a fail-open function: errors are logged but do not raise exceptions,
    so the main application flow is never blocked.
    
    Args:
        db: Database session
        event: Domain event to store
    """
    try:
        db.execute(text("""
            INSERT INTO outbox_events (event_type, payload_json, created_at)
            VALUES (:event_type, :payload_json, :created_at)
        """), {
            "event_type": event.event_type,
            "payload_json": json.dumps(event.__dict__, default=str),
            "created_at": event.timestamp
        })
        db.commit()
    except Exception as e:
        # Log error but don't fail the request (fail-open pattern)
        logger.warning(f"Failed to store outbox event {event.event_type}: {e}", exc_info=True)
        db.rollback()








