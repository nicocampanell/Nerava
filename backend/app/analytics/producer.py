"""
Analytics event producer for business metrics
"""
import logging
from typing import Any, Dict

from app.config import settings
from app.events.bus import event_bus
from app.events.domain import (
    ChargeStartedEvent,
    ChargeStoppedEvent,
    DomainEvent,
    WalletCreditedEvent,
)

logger = logging.getLogger(__name__)

class AnalyticsProducer:
    """Producer for analytics events"""
    
    def __init__(self):
        self.event_bus = event_bus
        self.region = settings.region
    
    async def publish_charging_event(self, event: DomainEvent):
        """Publish a charging-related analytics event"""
        try:
            analytics_event = self._create_analytics_event(event)
            await self.event_bus.publish(analytics_event)
            logger.info(f"Published analytics event: {analytics_event['event_type']}")
        except Exception as e:
            logger.error(f"Error publishing analytics event: {e}")
    
    def _create_analytics_event(self, event: DomainEvent) -> Dict[str, Any]:
        """Create analytics event from domain event"""
        base_event = {
            "event_id": event.event_id,
            "event_type": f"analytics_{event.event_type}",
            "timestamp": event.timestamp.isoformat(),
            "region": self.region,
            "aggregate_id": event.aggregate_id,
            "version": event.version
        }
        
        if isinstance(event, ChargeStartedEvent):
            return {
                **base_event,
                "session_id": event.session_id,
                "user_id": event.user_id,
                "hub_id": event.hub_id,
                "window_id": event.window_id,
                "started_at": event.started_at.isoformat(),
                "properties": {
                    "session_type": "charging",
                    "has_active_window": event.window_id is not None
                }
            }
        
        elif isinstance(event, ChargeStoppedEvent):
            return {
                **base_event,
                "session_id": event.session_id,
                "user_id": event.user_id,
                "hub_id": event.hub_id,
                "window_id": event.window_id,
                "stopped_at": event.stopped_at.isoformat(),
                "kwh_consumed": event.kwh_consumed,
                "grid_reward_usd": event.grid_reward_usd,
                "merchant_reward_usd": event.merchant_reward_usd,
                "total_reward_usd": event.total_reward_usd,
                "properties": {
                    "session_type": "charging",
                    "has_active_window": event.window_id is not None,
                    "reward_tier": self._get_reward_tier(event.total_reward_usd)
                }
            }
        
        elif isinstance(event, WalletCreditedEvent):
            return {
                **base_event,
                "user_id": event.user_id,
                "amount_cents": event.amount_cents,
                "session_id": event.session_id,
                "new_balance_cents": event.new_balance_cents,
                "credited_at": event.credited_at.isoformat(),
                "properties": {
                    "credit_type": "charging_reward",
                    "amount_tier": self._get_amount_tier(event.amount_cents)
                }
            }
        
        else:
            # Generic analytics event
            return {
                **base_event,
                "properties": {}
            }
    
    def _get_reward_tier(self, reward_usd: float) -> str:
        """Get reward tier based on amount"""
        if reward_usd >= 5.0:
            return "high"
        elif reward_usd >= 2.0:
            return "medium"
        else:
            return "low"
    
    def _get_amount_tier(self, amount_cents: int) -> str:
        """Get amount tier based on cents"""
        if amount_cents >= 500:
            return "high"
        elif amount_cents >= 200:
            return "medium"
        else:
            return "low"

# Global analytics producer
analytics_producer = AnalyticsProducer()

# Event handlers for analytics
async def handle_charge_started_analytics(event: ChargeStartedEvent):
    """Handle charge started event for analytics"""
    await analytics_producer.publish_charging_event(event)

async def handle_charge_stopped_analytics(event: ChargeStoppedEvent):
    """Handle charge stopped event for analytics"""
    await analytics_producer.publish_charging_event(event)

async def handle_wallet_credited_analytics(event: WalletCreditedEvent):
    """Handle wallet credited event for analytics"""
    await analytics_producer.publish_charging_event(event)

# Register analytics event handlers
event_bus.subscribe("charge_started", handle_charge_started_analytics)
event_bus.subscribe("charge_stopped", handle_charge_stopped_analytics)
event_bus.subscribe("wallet_credited", handle_wallet_credited_analytics)
