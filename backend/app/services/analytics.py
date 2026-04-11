"""
PostHog analytics client for backend

Sends server-truth events to PostHog for product analytics.
Never crashes requests if PostHog is down - swallows errors and logs them.
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Try to import posthog, but handle gracefully if not installed
try:
    import posthog
    POSTHOG_AVAILABLE = True
except ImportError:
    POSTHOG_AVAILABLE = False
    logger.warning("posthog package not installed. Analytics events will not be sent to PostHog.")


class AnalyticsClient:
    """PostHog analytics client wrapper"""
    
    def __init__(self):
        self.enabled = os.getenv("ANALYTICS_ENABLED", "true").lower() == "true"
        # Support both POSTHOG_KEY and POSTHOG_API_KEY for compatibility
        self.posthog_key = os.getenv("POSTHOG_KEY") or os.getenv("POSTHOG_API_KEY", "")
        self.posthog_host = os.getenv("POSTHOG_HOST", "https://app.posthog.com")
        self.env = os.getenv("ENV", "dev")
        self.posthog_client = None
        
        if not self.enabled:
            logger.info("Analytics disabled via ANALYTICS_ENABLED=false")
            return
            
        if not self.posthog_key:
            logger.warning("POSTHOG_KEY or POSTHOG_API_KEY not set. Analytics events will not be sent.")
            self.enabled = False
            return
            
        if not POSTHOG_AVAILABLE:
            self.enabled = False
            return
            
        try:
            # Initialize PostHog client
            self.posthog_client = posthog.Posthog(
                project_api_key=self.posthog_key,
                host=self.posthog_host,
            )
            logger.info(f"PostHog analytics initialized (host={self.posthog_host}, env={self.env})")
        except Exception as e:
            logger.error(f"Failed to initialize PostHog: {e}")
            self.enabled = False
    
    def capture(
        self,
        event: str,
        distinct_id: str,
        properties: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        merchant_id: Optional[str] = None,
        charger_id: Optional[str] = None,
        session_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        accuracy_m: Optional[float] = None,
    ) -> None:
        """
        Capture an event to PostHog
        
        Args:
            event: Event name (e.g., 'server.driver.otp.verify.success')
            distinct_id: User/distinct identifier
            properties: Additional event properties
            request_id: Request ID from middleware
            user_id: User ID (driver_id, merchant_user_id, admin_user_id)
            merchant_id: Merchant ID when relevant
            charger_id: Charger ID when relevant
            session_id: Session ID when relevant
            ip: Client IP address
            user_agent: User agent string
            lat: Latitude (geo coordinate)
            lng: Longitude (geo coordinate)
            accuracy_m: Location accuracy in meters
        """
        if not self.enabled:
            return
            
        if not POSTHOG_AVAILABLE:
            return
            
        try:
            # Build enriched properties
            enriched_properties = {
                "app": "backend",
                "env": self.env,
                "source": "api",
                "ts": datetime.utcnow().isoformat() + "Z",
            }
            
            # Add correlation IDs
            if request_id:
                enriched_properties["request_id"] = request_id
            if user_id:
                enriched_properties["user_id"] = user_id
            if merchant_id:
                enriched_properties["merchant_id"] = merchant_id
            if charger_id:
                enriched_properties["charger_id"] = charger_id
            if session_id:
                enriched_properties["session_id"] = session_id
            
            # Add request metadata
            if ip:
                enriched_properties["ip"] = ip
            if user_agent:
                enriched_properties["user_agent"] = user_agent
            
            # Add geo coordinates (if provided)
            if lat is not None and lng is not None:
                enriched_properties["lat"] = lat
                enriched_properties["lng"] = lng
                if accuracy_m is not None:
                    enriched_properties["accuracy_m"] = accuracy_m
            
            # Merge custom properties (can override geo if explicitly set)
            if properties:
                enriched_properties.update(properties)
            
            # Send to PostHog (non-blocking)
            if self.posthog_client:
                self.posthog_client.capture(
                    distinct_id=distinct_id,
                    event=event,
                    properties=enriched_properties,
                )
            
            logger.debug(f"Analytics event captured: {event} (distinct_id={distinct_id})")
            
        except Exception as e:
            # Never crash requests due to analytics failures
            logger.error(f"Failed to capture analytics event {event}: {e}", exc_info=True)
    
    def identify(
        self,
        distinct_id: str,
        traits: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Identify a user in PostHog
        
        Args:
            distinct_id: User identifier
            traits: User traits/properties
        """
        if not self.enabled:
            return
            
        if not POSTHOG_AVAILABLE:
            return
            
        try:
            if self.posthog_client:
                self.posthog_client.identify(
                    distinct_id=distinct_id,
                    properties=traits or {},
                )
            
            logger.debug(f"Analytics identify: {distinct_id}")
            
        except Exception as e:
            logger.error(f"Failed to identify user {distinct_id}: {e}", exc_info=True)


# Singleton instance
_analytics_client: Optional[AnalyticsClient] = None


def get_analytics_client() -> AnalyticsClient:
    """Get or create analytics client singleton"""
    global _analytics_client
    if _analytics_client is None:
        _analytics_client = AnalyticsClient()
    return _analytics_client

