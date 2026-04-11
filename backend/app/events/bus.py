"""
Event bus for publishing and subscribing to domain events
"""
import asyncio
import json
import logging
from typing import Callable, Dict, List, Optional

from app.config import settings

from .domain import EVENT_TYPES, DomainEvent

logger = logging.getLogger(__name__)

class EventBus:
    """In-memory event bus for domain events"""
    
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.event_history: List[DomainEvent] = []
        self.max_history = 1000
    
    def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to events of a specific type"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        logger.info(f"Subscribed {handler.__name__} to {event_type}")
    
    def unsubscribe(self, event_type: str, handler: Callable):
        """Unsubscribe from events of a specific type"""
        if event_type in self.subscribers:
            try:
                self.subscribers[event_type].remove(handler)
                logger.info(f"Unsubscribed {handler.__name__} from {event_type}")
            except ValueError:
                logger.warning(f"Handler {handler.__name__} not found in {event_type} subscribers")
    
    async def publish(self, event: DomainEvent):
        """Publish an event to all subscribers"""
        logger.info(f"Publishing event: {event.event_type} for {event.aggregate_id}")
        
        # Store in history
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)
        
        # Notify subscribers
        if event.event_type in self.subscribers:
            tasks = []
            for handler in self.subscribers[event.event_type]:
                try:
                    task = asyncio.create_task(self._handle_event(handler, event))
                    tasks.append(task)
                except Exception as e:
                    logger.error(f"Error creating task for {handler.__name__}: {e}")
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.debug(f"No subscribers for event type: {event.event_type}")
    
    async def _handle_event(self, handler: Callable, event: DomainEvent):
        """Handle an event with a specific handler"""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error(f"Error in event handler {handler.__name__}: {e}")
    
    def get_events(self, event_type: Optional[str] = None, aggregate_id: Optional[str] = None) -> List[DomainEvent]:
        """Get events from history with optional filtering"""
        events = self.event_history
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if aggregate_id:
            events = [e for e in events if e.aggregate_id == aggregate_id]
        
        return events
    
    def get_event_count(self, event_type: Optional[str] = None) -> int:
        """Get count of events by type"""
        if event_type:
            return len([e for e in self.event_history if e.event_type == event_type])
        return len(self.event_history)

class RedisEventBus:
    """Redis-based event bus for distributed systems"""
    
    def __init__(self, redis_url: str):
        import redis.asyncio as redis
        self.redis = redis.from_url(redis_url)
        self.subscribers: Dict[str, List[Callable]] = {}
    
    async def subscribe(self, event_type: str, handler: Callable):
        """Subscribe to events of a specific type"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        
        # Start listening to Redis stream
        asyncio.create_task(self._listen_to_stream(event_type))
    
    async def publish(self, event: DomainEvent):
        """Publish an event to Redis stream"""
        event_data = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp.isoformat(),
            "aggregate_id": event.aggregate_id,
            "version": event.version,
            "data": self._serialize_event(event)
        }
        
        await self.redis.xadd(f"events:{event.event_type}", event_data)
        logger.info(f"Published event to Redis: {event.event_type}")
    
    async def _listen_to_stream(self, event_type: str):
        """Listen to Redis stream for events"""
        stream_name = f"events:{event_type}"
        
        while True:
            try:
                messages = await self.redis.xread({stream_name: "$"}, count=1, block=1000)
                
                for stream, msgs in messages:
                    for msg_id, fields in msgs:
                        event = self._deserialize_event(fields)
                        await self._notify_subscribers(event_type, event)
                        
            except Exception as e:
                logger.error(f"Error listening to Redis stream {stream_name}: {e}")
                await asyncio.sleep(1)
    
    async def _notify_subscribers(self, event_type: str, event: DomainEvent):
        """Notify subscribers of an event"""
        if event_type in self.subscribers:
            tasks = []
            for handler in self.subscribers[event_type]:
                try:
                    task = asyncio.create_task(self._handle_event(handler, event))
                    tasks.append(task)
                except Exception as e:
                    logger.error(f"Error creating task for {handler.__name__}: {e}")
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _handle_event(self, handler: Callable, event: DomainEvent):
        """Handle an event with a specific handler"""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error(f"Error in event handler {handler.__name__}: {e}")
    
    def _serialize_event(self, event: DomainEvent) -> str:
        """Serialize event to JSON"""
        return json.dumps(event.__dict__, default=str)
    
    def _deserialize_event(self, fields: Dict[str, str]) -> DomainEvent:
        """Deserialize event from JSON"""
        event_data = json.loads(fields["data"])
        event_type = event_data["event_type"]
        
        if event_type in EVENT_TYPES:
            event_class = EVENT_TYPES[event_type]
            return event_class(**event_data)
        else:
            raise ValueError(f"Unknown event type: {event_type}")

# Global event bus instance
def get_event_bus():
    """Get the appropriate event bus based on configuration"""
    if settings.events_driver == "redis":
        return RedisEventBus(settings.redis_url)
    else:
        return EventBus()

event_bus = get_event_bus()
