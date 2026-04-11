"""
Batch writer for analytics events to database
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.db import get_db
from sqlalchemy import text

logger = logging.getLogger(__name__)

class AnalyticsBatchWriter:
    """Batch writer for analytics events"""
    
    def __init__(self, batch_size: int = 100, flush_interval: int = 30):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.batch: List[Dict[str, Any]] = []
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.region = settings.region
    
    async def start(self):
        """Start the batch writer"""
        if self.running:
            logger.warning("Analytics batch writer is already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Analytics batch writer started")
    
    async def stop(self):
        """Stop the batch writer"""
        if not self.running:
            return
        
        self.running = False
        
        # Flush remaining events
        if self.batch:
            await self._flush_batch()
        
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Analytics batch writer stopped")
    
    async def _run(self):
        """Main batch writer loop"""
        while self.running:
            try:
                await asyncio.sleep(self.flush_interval)
                if self.batch:
                    await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in analytics batch writer: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    async def write_event(self, event: Dict[str, Any]):
        """Write an analytics event to the batch"""
        self.batch.append(event)
        
        # Flush if batch is full
        if len(self.batch) >= self.batch_size:
            await self._flush_batch()
    
    async def _flush_batch(self):
        """Flush the current batch to database"""
        if not self.batch:
            return
        
        try:
            db = next(get_db())
            
            # Prepare batch insert
            values = []
            for event in self.batch:
                values.append({
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "timestamp": event["timestamp"],
                    "region": event.get("region", self.region),
                    "aggregate_id": event["aggregate_id"],
                    "properties": json.dumps(event.get("properties", {})),
                    "created_at": datetime.utcnow()
                })
            
            # Batch insert
            db.execute(text("""
                INSERT INTO analytics_events 
                (event_id, event_type, timestamp, region, aggregate_id, properties, created_at)
                VALUES (:event_id, :event_type, :timestamp, :region, :aggregate_id, :properties, :created_at)
            """), values)
            
            db.commit()
            logger.info(f"Flushed {len(self.batch)} analytics events to database")
            
            # Clear batch
            self.batch.clear()
            
        except Exception as e:
            logger.error(f"Error flushing analytics batch: {e}")
            # In a real system, you might want to retry or send to dead letter queue
    
    async def get_analytics_stats(self) -> Dict[str, Any]:
        """Get analytics statistics"""
        try:
            db = next(get_db())
            
            # Get total events
            total_result = db.execute(text("SELECT COUNT(*) as count FROM analytics_events"))
            total_events = total_result.scalar()
            
            # Get events by type
            type_result = db.execute(text("""
                SELECT event_type, COUNT(*) as count
                FROM analytics_events
                GROUP BY event_type
            """))
            events_by_type = {row.event_type: row.count for row in type_result}
            
            # Get events by region
            region_result = db.execute(text("""
                SELECT region, COUNT(*) as count
                FROM analytics_events
                GROUP BY region
            """))
            events_by_region = {row.region: row.count for row in region_result}
            
            return {
                "total_events": total_events,
                "events_by_type": events_by_type,
                "events_by_region": events_by_region,
                "batch_size": len(self.batch),
                "running": self.running
            }
            
        except Exception as e:
            logger.error(f"Error getting analytics stats: {e}")
            return {}

# Global batch writer
analytics_batch_writer = AnalyticsBatchWriter()

# Event handler for analytics events
async def handle_analytics_event(event: Dict[str, Any]):
    """Handle analytics event for batch writing"""
    await analytics_batch_writer.write_event(event)

# Register with event bus
from app.events.bus import event_bus

event_bus.subscribe("analytics_charge_started", handle_analytics_event)
event_bus.subscribe("analytics_charge_stopped", handle_analytics_event)
event_bus.subscribe("analytics_wallet_credited", handle_analytics_event)
