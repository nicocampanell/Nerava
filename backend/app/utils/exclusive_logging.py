"""
Logging utility for exclusive session events
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


def log_event(event_name: str, payload: Dict[str, Any]) -> None:
    """
    Log an exclusive session event with structured format.
    
    Args:
        event_name: Event name (e.g., "exclusive_activated", "exclusive_completed")
        payload: Event payload dictionary
    """
    log_data = {
        "at": "exclusive",
        "event": event_name,
        "ts": datetime.utcnow().isoformat(),
        **payload
    }
    
    # Format as JSON-like string for readability
    log_msg = json.dumps(log_data, separators=(',', ':'))
    logger.info(log_msg)

