"""
HubSpot CRM client for backend

Sends lifecycle events to HubSpot CRM (low-volume, lifecycle-only).
Never crashes requests if HubSpot is down - logs warnings and continues.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


class HubSpotClient:
    """HubSpot CRM client wrapper"""
    
    def __init__(self):
        self.enabled = settings.HUBSPOT_ENABLED
        self.send_live = settings.HUBSPOT_SEND_LIVE
        self.access_token = settings.HUBSPOT_PRIVATE_APP_TOKEN
        self.base_url = "https://api.hubapi.com"
        
        if not self.enabled:
            logger.info("HubSpot disabled via HUBSPOT_ENABLED=false")
            return
            
        if not self.access_token:
            logger.warning("HUBSPOT_PRIVATE_APP_TOKEN not set. HubSpot CRM updates will be skipped.")
            self.enabled = False
            return
            
        if self.send_live:
            logger.info("HubSpot CRM client initialized (LIVE mode)")
        else:
            logger.info("HubSpot CRM client initialized (DRY-RUN mode)")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for HubSpot API requests"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
    
    def upsert_contact(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        external_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create or update a contact in HubSpot
        
        Args:
            email: Contact email (used as identifier if provided)
            phone: Contact phone number
            properties: Additional contact properties
            external_id: External ID for contact lookup (used if email/phone not available)
            
        Returns:
            Contact ID if successful, None otherwise
        """
        if not self.enabled:
            return None
            
        if not email and not phone and not external_id:
            logger.warning("HubSpot upsert_contact requires at least email, phone, or external_id")
            return None
            
        try:
            # Build contact properties
            contact_properties = properties or {}
            if email:
                contact_properties["email"] = email
            if phone:
                contact_properties["phone"] = phone
            if external_id:
                contact_properties["nerava_external_id"] = external_id
            
            # Determine identifier and property
            if email:
                identifier = email
                identifier_property = "email"
            elif phone:
                identifier = phone
                identifier_property = "phone"
            elif external_id:
                identifier = external_id
                identifier_property = "nerava_external_id"
            else:
                return None
            
            # Dry-run mode: validate and log
            if not self.send_live:
                logger.info(
                    f"[DRY-RUN] HubSpot upsert_contact: {identifier} "
                    f"properties={contact_properties}"
                )
                return "dry-run-contact-id"
            
            # HubSpot API: Create or update contact
            url = f"{self.base_url}/crm/v3/objects/contacts"
            params = {
                "idProperty": identifier_property,
            }
            
            payload = {
                "properties": contact_properties,
            }
            
            response = requests.post(
                url,
                json=payload,
                headers=self._get_headers(),
                params=params,
                timeout=5,
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                contact_id = data.get("id")
                logger.info(f"HubSpot contact upserted: {identifier} (id={contact_id})")
                return contact_id
            else:
                logger.warning(
                    f"HubSpot upsert_contact failed for {identifier}: "
                    f"status={response.status_code}, response={response.text}"
                )
                return None
                
        except Exception as e:
            # Never crash requests due to HubSpot failures
            logger.error(f"HubSpot upsert_contact error for {email or phone or external_id}: {e}", exc_info=True)
            return None
    
    def update_contact_properties(
        self,
        contact_id: str,
        properties: Dict[str, Any],
    ) -> bool:
        """
        Update contact properties in HubSpot
        
        Args:
            contact_id: HubSpot contact ID
            properties: Properties to update
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        # Skip if dry-run contact ID
        if contact_id == "dry-run-contact-id":
            logger.info(f"[DRY-RUN] HubSpot update_contact_properties: {contact_id} properties={properties}")
            return True
            
        try:
            # Dry-run mode: validate and log
            if not self.send_live:
                logger.info(
                    f"[DRY-RUN] HubSpot update_contact_properties: {contact_id} "
                    f"properties={properties}"
                )
                return True
            
            url = f"{self.base_url}/crm/v3/objects/contacts/{contact_id}"
            
            payload = {
                "properties": properties,
            }
            
            response = requests.patch(
                url,
                json=payload,
                headers=self._get_headers(),
                timeout=5,
            )
            
            if response.status_code == 200:
                logger.debug(f"HubSpot contact properties updated: {contact_id}")
                return True
            else:
                logger.warning(
                    f"HubSpot update_contact_properties failed for {contact_id}: "
                    f"status={response.status_code}, response={response.text}"
                )
                return False
                
        except Exception as e:
            logger.error(f"HubSpot update_contact_properties error for {contact_id}: {e}", exc_info=True)
            return False
    
    def send_event(
        self,
        event_name: str,
        properties: Dict[str, Any],
        email: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> bool:
        """
        Send a custom event to HubSpot (via contact property updates)
        
        Args:
            event_name: Name of the event
            properties: Event properties
            email: Contact email
            external_id: Contact external ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            # Build event properties for contact update
            event_properties = {
                "last_event_name": event_name,
                "last_event_at": datetime.utcnow().isoformat() + "Z",
            }
            
            # Add event-specific counters
            event_counter_key = f"{event_name}_count"
            # Note: In a real implementation, we'd fetch current count and increment
            # For now, we'll just set the event name and timestamp
            
            # Merge additional properties
            for key, value in properties.items():
                # Sanitize key for HubSpot property name
                prop_key = key.replace(" ", "_").lower()
                event_properties[prop_key] = value
            
            # Dry-run mode: log event
            if not self.send_live:
                logger.info(
                    f"[DRY-RUN] HubSpot send_event: {event_name} "
                    f"email={email}, external_id={external_id}, properties={event_properties}"
                )
                return True
            
            # If we have email or external_id, update contact
            if email or external_id:
                contact_id = self.upsert_contact(
                    email=email,
                    external_id=external_id,
                    properties=event_properties
                )
                if contact_id:
                    return True
            
            logger.warning(f"HubSpot send_event: No email or external_id provided for event {event_name}")
            return False
            
        except Exception as e:
            # Never crash requests due to HubSpot failures
            logger.error(f"HubSpot send_event error for {event_name}: {e}", exc_info=True)
            return False
    
    def create_timeline_event(
        self,
        contact_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        """
        Create a timeline event for a contact (optional)
        
        Args:
            contact_id: HubSpot contact ID
            event_type: Event type identifier
            payload: Event payload
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
            
        # Timeline events require HubSpot timeline API setup
        # For now, we'll skip this and just update contact properties
        # This can be implemented later if needed
        logger.debug(f"HubSpot timeline event skipped (not implemented): {event_type} for {contact_id}")
        return False


# Singleton instance
_hubspot_client: Optional[HubSpotClient] = None


def get_hubspot_client() -> HubSpotClient:
    """Get or create HubSpot client singleton"""
    global _hubspot_client
    if _hubspot_client is None:
        _hubspot_client = HubSpotClient()
    return _hubspot_client


def track_event(db, event_type: str, payload: Dict[str, Any]) -> None:
    """
    Store a HubSpot event in the outbox for async processing.
    
    This is a fail-open function: errors are logged but do not raise exceptions,
    so the main application flow is never blocked.
    
    Args:
        db: Database session
        event_type: Event type (e.g., "driver_signed_up", "nova_redeemed")
        payload: Event payload dictionary
    """
    try:
        from datetime import datetime

        from app.events.domain import (
            DriverSignedUpEvent,
            FirstRedemptionCompletedEvent,
            NovaEarnedEvent,
            NovaRedeemedEvent,
            WalletPassInstalledEvent,
        )
        from app.events.outbox import store_outbox_event
        
        # Map event_type to domain event class
        event_class_map = {
            "driver_signed_up": DriverSignedUpEvent,
            "user_signup": DriverSignedUpEvent,  # Alias
            "wallet_pass_installed": WalletPassInstalledEvent,
            "nova_earned": NovaEarnedEvent,
            "nova_redeemed": NovaRedeemedEvent,
            "redemption": NovaRedeemedEvent,  # Alias
            "first_redemption_completed": FirstRedemptionCompletedEvent,
        }
        
        event_class = event_class_map.get(event_type)
        if not event_class:
            logger.warning(f"Unknown HubSpot event type: {event_type}")
            return
        
        # Convert payload to domain event
        # Handle datetime strings
        for key, value in payload.items():
            if isinstance(value, str) and ("_at" in key or "created_at" in key or "date" in key):
                try:
                    payload[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass  # Keep as string if parsing fails
        
        # Create domain event
        domain_event = event_class(**payload)
        
        # Store in outbox
        store_outbox_event(db, domain_event)
        
    except Exception as e:
        # Never crash requests due to HubSpot tracking failures
        logger.warning(f"HubSpot track_event failed for {event_type}: {e}", exc_info=True)
