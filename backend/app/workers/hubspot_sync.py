"""
HubSpot sync worker

Reads events from the outbox and sends them to HubSpot (in log-only mode by default).
"""
import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import SessionLocal
from app.events.domain import EVENT_TYPES
from app.events.hubspot_adapter import adapt_event_to_hubspot, to_hubspot_external_id
from app.models import User
from app.services.hubspot import get_hubspot_client
from sqlalchemy import text

logger = logging.getLogger(__name__)


@contextmanager
def get_db_session():
    """Context manager for database sessions"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class HubSpotSyncWorker:
    """Worker that processes outbox events and sends them to HubSpot"""
    
    def __init__(self, poll_interval: int = 10):
        self.poll_interval = poll_interval
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.hubspot_client = get_hubspot_client()
        
        # Rate limiting: allow 8-10 requests/second max
        self.rate_limit_requests_per_second = 8
        self.rate_limit_window_seconds = 1.0
        self._request_times: List[float] = []
        self._last_request_time = 0.0
    
    async def start(self):
        """Start the HubSpot sync worker"""
        if self.running:
            logger.warning("HubSpot sync worker is already running")
            return
        
        if not self.hubspot_client.enabled:
            logger.info("HubSpot sync worker not started (HUBSPOT_ENABLED=false)")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("HubSpot sync worker started")
    
    async def stop(self):
        """Stop the HubSpot sync worker"""
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("HubSpot sync worker stopped")
    
    async def _run(self):
        """Main worker loop"""
        while self.running:
            try:
                await self._process_outbox_events()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in HubSpot sync worker: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)
    
    async def _process_outbox_events(self):
        """Process pending outbox events for HubSpot"""
        try:
            # Get unprocessed events for HubSpot-relevant event types
            events = await self._get_relevant_unprocessed_events()
            
            for event in events:
                try:
                    # Process the event
                    await self._process_event(event)
                    
                    # Mark as processed (only if no exception was raised)
                    await self._mark_event_processed(event["id"])
                    
                    logger.info(f"Processed HubSpot event: {event['id']} ({event['event_type']})")
                    
                except Exception as e:
                    # Error handling is done in _process_event (retry/fail logic)
                    # Just log here for visibility
                    logger.debug(f"HubSpot event {event['id']} will be retried or marked failed: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing HubSpot outbox events: {e}", exc_info=True)
    
    async def _get_relevant_unprocessed_events(self) -> List[Dict[str, Any]]:
        """
        Get unprocessed events that are relevant for HubSpot.
        
        Only fetches events of types that HubSpot cares about.
        Excludes events that have exceeded max retry attempts.
        """
        relevant_types = [
            "driver_signed_up",
            "wallet_pass_installed",
            "nova_earned",
            "nova_redeemed",
            "first_redemption_completed"
        ]
        
        try:
            with get_db_session() as db:
                # Build query with IN clause for relevant event types
                placeholders = ",".join([f"'{t}'" for t in relevant_types])
                result = db.execute(text(f"""
                    SELECT id, event_type, payload_json, created_at, attempt_count, last_error
                    FROM outbox_events
                    WHERE processed_at IS NULL
                    AND event_type IN ({placeholders})
                    AND (attempt_count IS NULL OR attempt_count < 3)
                    ORDER BY created_at ASC
                    LIMIT 50
                """))
                
                events = []
                for row in result:
                    events.append({
                        "id": row.id,
                        "event_type": row.event_type,
                        "payload_json": row.payload_json,
                        "created_at": row.created_at,
                        "attempt_count": getattr(row, "attempt_count", 0) or 0,
                        "last_error": getattr(row, "last_error", None),
                    })
                
                return events
                
        except Exception as e:
            logger.error(f"Error getting HubSpot relevant events: {e}", exc_info=True)
            return []
    
    async def _wait_for_rate_limit(self):
        """Wait if rate limit would be exceeded"""
        current_time = time.time()
        
        # Remove old request times outside the window
        cutoff_time = current_time - self.rate_limit_window_seconds
        self._request_times = [t for t in self._request_times if t > cutoff_time]
        
        # Check if we're at the limit
        if len(self._request_times) >= self.rate_limit_requests_per_second:
            # Wait until we can make another request
            sleep_time = self._request_times[0] + self.rate_limit_window_seconds - current_time + 0.1
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                current_time = time.time()
                # Clean up again after sleep
                cutoff_time = current_time - self.rate_limit_window_seconds
                self._request_times = [t for t in self._request_times if t > cutoff_time]
        
        # Record this request
        self._request_times.append(current_time)
        self._last_request_time = current_time
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process a single event and send to HubSpot with retry logic"""
        event_id = event["id"]
        attempt_count = event.get("attempt_count", 0)
        
        try:
            # Apply rate limiting
            await self._wait_for_rate_limit()
            
            # Deserialize the event
            event_data = json.loads(event["payload_json"])
            event_type = event["event_type"]
            
            if event_type not in EVENT_TYPES:
                logger.warning(f"Unknown event type for HubSpot: {event_type}")
                await self._mark_event_failed(event_id, attempt_count, f"Unknown event type: {event_type}")
                return
            
            event_class = EVENT_TYPES[event_type]
            domain_event = event_class(**event_data)
            
            # Get user email if user_id is available
            email = None
            user_id = getattr(domain_event, "user_id", None)
            if user_id:
                try:
                    with get_db_session() as db:
                        # Try to get user email
                        user = db.query(User).filter(User.id == int(user_id)).first()
                        if user:
                            email = user.email
                except Exception as e:
                    logger.debug(f"Could not fetch email for user {user_id}: {e}")
            
            # Adapt event to HubSpot format
            hubspot_payload = adapt_event_to_hubspot(domain_event, email)
            
            if not hubspot_payload:
                logger.debug(f"Event type {event_type} not supported by HubSpot adapter")
                await self._mark_event_failed(event_id, attempt_count, "Event not supported by adapter")
                return
            
            # Get external ID
            external_id = None
            if user_id:
                try:
                    external_id = to_hubspot_external_id(int(user_id))
                except (ValueError, TypeError):
                    pass
            
            # Must have either email or external_id
            if not email and not external_id:
                logger.warning(f"No email or external_id available for HubSpot event {event_type}")
                await self._mark_event_failed(event_id, attempt_count, "No email or external_id available")
                return
            
            # Upsert contact if contact_properties are provided
            if "contact_properties" in hubspot_payload:
                contact_id = self.hubspot_client.upsert_contact(
                    email=email,
                    properties=hubspot_payload["contact_properties"],
                    external_id=external_id
                )
                if not contact_id:
                    raise Exception("Failed to upsert contact")
            
            # Send event
            success = self.hubspot_client.send_event(
                event_name=hubspot_payload["event_name"],
                properties=hubspot_payload["event_properties"],
                email=email,
                external_id=external_id
            )
            
            if not success:
                raise Exception("Failed to send event")
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error processing HubSpot event {event_id}: {error_msg}", exc_info=True)
            
            # Increment attempt count and store error
            new_attempt_count = attempt_count + 1
            
            if new_attempt_count >= 3:
                # Max retries exceeded, mark as failed
                await self._mark_event_failed(event_id, new_attempt_count, error_msg)
            else:
                # Store error but keep event for retry
                await self._mark_event_retry(event_id, new_attempt_count, error_msg)
            raise
    
    async def _mark_event_retry(self, event_id: int, attempt_count: int, error_msg: str):
        """Mark event for retry with updated attempt count and error"""
        try:
            with get_db_session() as db:
                db.execute(text("""
                    UPDATE outbox_events
                    SET attempt_count = :attempt_count,
                        last_error = :last_error
                    WHERE id = :event_id
                """), {
                    "attempt_count": attempt_count,
                    "last_error": error_msg[:1000],  # Limit error message length
                    "event_id": event_id
                })
        except Exception as e:
            logger.error(f"Error marking HubSpot event for retry: {e}", exc_info=True)
    
    async def _mark_event_failed(self, event_id: int, attempt_count: int, error_msg: str):
        """Mark event as failed (max retries exceeded)"""
        try:
            with get_db_session() as db:
                db.execute(text("""
                    UPDATE outbox_events
                    SET processed_at = :processed_at,
                        attempt_count = :attempt_count,
                        last_error = :last_error
                    WHERE id = :event_id
                """), {
                    "processed_at": datetime.utcnow(),
                    "attempt_count": attempt_count,
                    "last_error": f"FAILED after {attempt_count} attempts: {error_msg[:900]}",
                    "event_id": event_id
                })
                logger.warning(f"HubSpot event {event_id} marked as failed after {attempt_count} attempts")
        except Exception as e:
            logger.error(f"Error marking HubSpot event as failed: {e}", exc_info=True)
    
    async def _mark_event_processed(self, event_id: int):
        """Mark an event as processed"""
        try:
            with get_db_session() as db:
                db.execute(text("""
                    UPDATE outbox_events
                    SET processed_at = :processed_at,
                        last_error = NULL
                    WHERE id = :event_id
                """), {
                    "processed_at": datetime.utcnow(),
                    "event_id": event_id
                })
                
        except Exception as e:
            logger.error(f"Error marking HubSpot event as processed: {e}", exc_info=True)
            raise
    
    async def process_once(self, db=None):
        """
        Process one batch of events (useful for testing).
        
        Args:
            db: Optional database session (creates one if not provided)
        """
        if db:
            # Use provided session
            events = await self._get_relevant_unprocessed_events()
            for event in events:
                try:
                    await self._process_event(event)
                    await self._mark_event_processed(event["id"])
                except Exception:
                    pass  # Error handling done in _process_event
        else:
            # Use normal async processing
            await self._process_outbox_events()


# Global worker instance
hubspot_sync_worker = HubSpotSyncWorker()

