"""
Outbox pattern implementation for reliable event publishing
"""
import asyncio
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import SessionLocal
from app.events.bus import event_bus
from app.events.domain import EVENT_TYPES
from sqlalchemy import text

logger = logging.getLogger(__name__)


@contextmanager
def get_db_session():
    """
    Context manager for database sessions (P1-2: fix session leaks).
    Ensures sessions are properly closed even on exceptions.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class OutboxRelay:
    """Relay service for processing outbox events"""
    
    def __init__(self, poll_interval: int = 5):
        self.poll_interval = poll_interval
        self.running = False
        self.task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the outbox relay worker"""
        if self.running:
            logger.warning("Outbox relay is already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Outbox relay started")
    
    async def stop(self):
        """Stop the outbox relay worker"""
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Outbox relay stopped")
    
    async def _run(self):
        """Main worker loop"""
        while self.running:
            try:
                await self._process_outbox_events()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in outbox relay: {e}")
                await asyncio.sleep(self.poll_interval)
    
    async def _process_outbox_events(self):
        """Process pending outbox events"""
        try:
            # Get unprocessed events
            events = await self._get_unprocessed_events()
            
            for event in events:
                try:
                    # Publish the event
                    await self._publish_event(event)
                    
                    # Mark as processed
                    await self._mark_event_processed(event["id"])
                    
                    logger.info(f"Processed outbox event: {event['id']}")
                    
                except Exception as e:
                    logger.error(f"Error processing outbox event {event['id']}: {e}")
                    # Don't mark as processed if there was an error
                    
        except Exception as e:
            logger.error(f"Error processing outbox events: {e}")
    
    async def _get_unprocessed_events(self) -> List[Dict[str, Any]]:
        """Get unprocessed events from the outbox (P1-2: fixed session leak)"""
        try:
            with get_db_session() as db:
                result = db.execute(text("""
                    SELECT id, event_type, payload_json, created_at
                    FROM outbox_events
                    WHERE processed_at IS NULL
                    ORDER BY created_at ASC
                    LIMIT 100
                """))
                
                events = []
                for row in result:
                    events.append({
                        "id": row.id,
                        "event_type": row.event_type,
                        "payload_json": row.payload_json,
                        "created_at": row.created_at
                    })
                
                return events
            
        except Exception as e:
            logger.error(f"Error getting unprocessed events: {e}")
            return []
    
    async def _publish_event(self, event: Dict[str, Any]):
        """Publish an event to the event bus"""
        try:
            # Deserialize the event
            event_data = json.loads(event["payload_json"])
            event_type = event["event_type"]
            
            if event_type in EVENT_TYPES:
                event_class = EVENT_TYPES[event_type]
                domain_event = event_class(**event_data)
                
                # Publish to event bus
                await event_bus.publish(domain_event)
                
                logger.info(f"Published event: {event_type}")
            else:
                logger.warning(f"Unknown event type: {event_type}")
                
        except Exception as e:
            logger.error(f"Error publishing event: {e}")
            raise
    
    async def _mark_event_processed(self, event_id: int):
        """Mark an event as processed (P1-2: fixed session leak)"""
        try:
            with get_db_session() as db:
                db.execute(text("""
                    UPDATE outbox_events
                    SET processed_at = :processed_at
                    WHERE id = :event_id
                """), {
                    "processed_at": datetime.utcnow(),
                    "event_id": event_id
                })
            
        except Exception as e:
            logger.error(f"Error marking event as processed: {e}")
            raise
    
    async def get_outbox_stats(self) -> Dict[str, Any]:
        """Get statistics about the outbox (P1-2: fixed session leak)"""
        try:
            with get_db_session() as db:
                # Get total events
                total_result = db.execute(text("SELECT COUNT(*) as count FROM outbox_events"))
                total_events = total_result.scalar()
                
                # Get unprocessed events
                unprocessed_result = db.execute(text("""
                    SELECT COUNT(*) as count FROM outbox_events WHERE processed_at IS NULL
                """))
                unprocessed_events = unprocessed_result.scalar()
                
                # Get events by type
                type_result = db.execute(text("""
                    SELECT event_type, COUNT(*) as count
                    FROM outbox_events
                    GROUP BY event_type
                """))
                events_by_type = {row.event_type: row.count for row in type_result}
                
                return {
                    "total_events": total_events,
                    "unprocessed_events": unprocessed_events,
                    "processed_events": total_events - unprocessed_events,
                    "events_by_type": events_by_type
                }
            
        except Exception as e:
            logger.error(f"Error getting outbox stats: {e}")
            return {}

# Global outbox relay instance
outbox_relay = OutboxRelay()
