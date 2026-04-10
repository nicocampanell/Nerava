"""
Wallet credit event subscriber
"""
import logging

from app.events.domain import ChargeStoppedEvent, WalletCreditedEvent
from app.services.circuit_breaker import wallet_circuit_breaker

logger = logging.getLogger(__name__)

async def handle_charge_stopped(event: ChargeStoppedEvent):
    """Handle charge stopped event by crediting the wallet"""
    try:
        logger.info(f"Processing wallet credit for session {event.session_id}")
        
        # Calculate reward in cents
        reward_cents = int(event.total_reward_usd * 100)
        
        # Credit the wallet using circuit breaker
        try:
            response = await wallet_circuit_breaker.post(
                "/v1/wallet/credit_qs",
                params={
                    "user_id": event.user_id,
                    "cents": reward_cents
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                new_balance = result.get("new_balance_cents", 0)
                
                logger.info(f"Successfully credited {reward_cents} cents to {event.user_id}. New balance: {new_balance}")
                
                # Publish wallet credited event
                from app.events.bus import event_bus
                from app.events.domain import WalletCreditedEvent
                
                wallet_event = WalletCreditedEvent(
                    user_id=event.user_id,
                    amount_cents=reward_cents,
                    session_id=event.session_id,
                    new_balance_cents=new_balance,
                    credited_at=event.timestamp
                )
                
                await event_bus.publish(wallet_event)
                
            else:
                logger.error(f"Failed to credit wallet: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Circuit breaker error crediting wallet: {e}")
            # In a real system, you might want to retry or send to a dead letter queue
            
    except Exception as e:
        logger.error(f"Error handling charge stopped event: {e}")

async def handle_wallet_credited(event: WalletCreditedEvent):
    """Handle wallet credited event for logging and analytics"""
    try:
        logger.info(f"Wallet credited: {event.user_id} received {event.amount_cents} cents")
        
        # Here you could:
        # - Send notifications
        # - Update analytics
        # - Trigger other business processes
        
    except Exception as e:
        logger.error(f"Error handling wallet credited event: {e}")

# Register event handlers
from app.events.bus import event_bus

event_bus.subscribe("charge_stopped", handle_charge_stopped)
event_bus.subscribe("wallet_credited", handle_wallet_credited)
