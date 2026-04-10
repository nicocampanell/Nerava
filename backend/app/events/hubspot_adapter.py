"""
HubSpot Event Adapter

Converts domain events to HubSpot event format.
"""
from typing import Any, Dict, Optional

from app.events.domain import DomainEvent
from app.utils.log import get_logger

logger = get_logger(__name__)


def adapt_user_signup_event(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert user signup event to HubSpot format.
    
    Args:
        user_data: User data from signup event
    
    Returns:
        HubSpot-formatted payload
    """
    return {
        "eventName": "nerava_user_signup",
        "properties": {
            "user_id": user_data.get("user_id"),
            "email": user_data.get("email"),
            "signup_date": user_data.get("created_at"),
            "role_flags": user_data.get("role_flags", ""),
        }
    }


def adapt_redemption_event(redemption_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert redemption event to HubSpot format.
    
    Args:
        redemption_data: Redemption data from event
    
    Returns:
        HubSpot-formatted payload
    """
    return {
        "eventName": "nerava_redemption",
        "properties": {
            "user_id": redemption_data.get("user_id"),
            "merchant_id": redemption_data.get("merchant_id"),
            "amount_cents": redemption_data.get("amount_cents"),
            "redemption_id": redemption_data.get("redemption_id"),
            "redeemed_at": redemption_data.get("redeemed_at"),
        }
    }


def adapt_wallet_pass_install_event(install_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert wallet pass install event to HubSpot format.
    
    Args:
        install_data: Wallet pass install data
    
    Returns:
        HubSpot-formatted payload
    """
    return {
        "eventName": "nerava_wallet_pass_install",
        "properties": {
            "user_id": install_data.get("user_id"),
            "pass_type": install_data.get("pass_type", "apple"),  # apple or google
            "installed_at": install_data.get("installed_at"),
        }
    }


def to_hubspot_external_id(user_id: int) -> str:
    """
    Convert user ID to HubSpot external ID format.
    
    Args:
        user_id: User ID (integer)
    
    Returns:
        HubSpot external ID string
    """
    return f"nerava_user_{user_id}"


def adapt_event_to_hubspot(event: DomainEvent, email: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Convert a domain event to HubSpot payload format.
    
    Args:
        event: Domain event instance
        email: Optional email address (if not in event)
    
    Returns:
        HubSpot payload dict or None if event not supported
    """
    from datetime import datetime
    
    event_type = event.event_type
    user_id = getattr(event, "user_id", None)
    
    # Get email from event if not provided
    if not email:
        email = getattr(event, "email", None)
    
    # Generate external_id from user_id
    external_id = None
    if user_id:
        try:
            external_id = to_hubspot_external_id(int(user_id))
        except (ValueError, TypeError):
            pass
    
    # Must have either email or external_id
    if not email and not external_id:
        logger.warning(f"Cannot adapt event {event_type}: no email or user_id")
        return None
    
    # Build base payload
    payload = {
        "event_name": f"nerava_{event_type}",
        "email": email,
        "external_id": external_id,
        "contact_properties": {},
        "event_properties": {},
    }
    
    # Adapt based on event type
    if event_type == "driver_signed_up":
        payload["contact_properties"] = {
            "role": "driver",
            "signup_date": getattr(event, "created_at", datetime.utcnow()).isoformat() + "Z",
            "auth_provider": getattr(event, "auth_provider", ""),
            "lifecycle_stage": "new_driver",
        }
        payload["event_properties"] = {
            "user_id": str(user_id) if user_id else "",
            "auth_provider": getattr(event, "auth_provider", ""),
        }
    
    elif event_type == "wallet_pass_installed":
        pass_type = getattr(event, "pass_type", "apple")
        payload["contact_properties"] = {
            "wallet_pass_installed": True,
            "wallet_pass_type": pass_type,
            "wallet_pass_installed_at": getattr(event, "installed_at", datetime.utcnow()).isoformat() + "Z",
        }
        payload["event_properties"] = {
            "user_id": str(user_id) if user_id else "",
            "pass_type": pass_type,
        }
    
    elif event_type == "nova_earned":
        amount_cents = getattr(event, "amount_cents", 0)
        payload["contact_properties"] = {
            "total_nova_earned_cents": amount_cents,  # Note: This should be cumulative in real implementation
            "last_nova_earned_at": getattr(event, "earned_at", datetime.utcnow()).isoformat() + "Z",
        }
        payload["event_properties"] = {
            "user_id": str(user_id) if user_id else "",
            "amount_cents": amount_cents,
            "session_id": getattr(event, "session_id", ""),
            "new_balance_cents": getattr(event, "new_balance_cents", 0),
        }
    
    elif event_type == "nova_redeemed":
        amount_cents = getattr(event, "amount_cents", 0)
        merchant_id = getattr(event, "merchant_id", "")
        payload["contact_properties"] = {
            "total_nova_redeemed_cents": amount_cents,  # Note: This should be cumulative
            "last_redemption_at": getattr(event, "redeemed_at", datetime.utcnow()).isoformat() + "Z",
        }
        payload["event_properties"] = {
            "user_id": str(user_id) if user_id else "",
            "amount_cents": amount_cents,
            "merchant_id": merchant_id,
            "redemption_id": getattr(event, "redemption_id", ""),
            "new_balance_cents": getattr(event, "new_balance_cents", 0),
        }
    
    elif event_type == "first_redemption_completed":
        amount_cents = getattr(event, "amount_cents", 0)
        merchant_id = getattr(event, "merchant_id", "")
        payload["contact_properties"] = {
            "lifecycle_stage": "active_driver",
            "first_redemption_completed_at": getattr(event, "completed_at", datetime.utcnow()).isoformat() + "Z",
        }
        payload["event_properties"] = {
            "user_id": str(user_id) if user_id else "",
            "amount_cents": amount_cents,
            "merchant_id": merchant_id,
            "redemption_id": getattr(event, "redemption_id", ""),
        }
    
    else:
        logger.warning(f"Event type {event_type} not supported by HubSpot adapter")
        return None
    
    return payload
