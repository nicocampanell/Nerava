"""
Redis Streams adapter for event bus
"""
import logging
from typing import Any, Dict, List

import redis.asyncio as redis
from app.config import settings

logger = logging.getLogger(__name__)

class RedisStreamsAdapter:
    """Redis Streams adapter for event publishing and consumption"""
    
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)
        self.consumer_group = "nerava_events"
        self.consumer_name = f"nerava_consumer_{id(self)}"
        self.streams = {}
    
    async def publish_event(self, stream_name: str, event_data: Dict[str, Any]) -> str:
        """Publish an event to a Redis stream"""
        try:
            message_id = await self.redis.xadd(stream_name, event_data)
            logger.info(f"Published event to {stream_name}: {message_id}")
            return message_id
        except Exception as e:
            logger.error(f"Error publishing event to {stream_name}: {e}")
            raise
    
    async def create_consumer_group(self, stream_name: str):
        """Create a consumer group for a stream"""
        try:
            await self.redis.xgroup_create(stream_name, self.consumer_group, id="0", mkstream=True)
            logger.info(f"Created consumer group {self.consumer_group} for {stream_name}")
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group {self.consumer_group} already exists for {stream_name}")
            else:
                logger.error(f"Error creating consumer group: {e}")
                raise
    
    async def consume_events(self, stream_names: List[str], handler: callable, count: int = 1, block: int = 1000):
        """Consume events from multiple streams"""
        try:
            # Ensure consumer groups exist
            for stream_name in stream_names:
                await self.create_consumer_group(stream_name)
            
            # Read from streams
            streams_dict = dict.fromkeys(stream_names, ">")
            messages = await self.redis.xreadgroup(
                self.consumer_group,
                self.consumer_name,
                streams_dict,
                count=count,
                block=block
            )
            
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    try:
                        # Process the event
                        await handler(stream, msg_id, fields)
                        
                        # Acknowledge the message
                        await self.redis.xack(stream, self.consumer_group, msg_id)
                        logger.debug(f"Acknowledged message {msg_id} from {stream}")
                        
                    except Exception as e:
                        logger.error(f"Error processing message {msg_id} from {stream}: {e}")
                        # Don't acknowledge failed messages
                        
        except Exception as e:
            logger.error(f"Error consuming events: {e}")
            raise
    
    async def get_pending_messages(self, stream_name: str) -> List[Dict[str, Any]]:
        """Get pending messages for a consumer group"""
        try:
            pending = await self.redis.xpending_range(
                stream_name,
                self.consumer_group,
                min="-",
                max="+",
                count=100
            )
            return pending
        except Exception as e:
            logger.error(f"Error getting pending messages for {stream_name}: {e}")
            return []
    
    async def claim_pending_messages(self, stream_name: str, min_idle_time: int = 60000) -> List[Dict[str, Any]]:
        """Claim and process pending messages"""
        try:
            pending = await self.get_pending_messages(stream_name)
            if not pending:
                return []
            
            # Claim messages that have been idle for too long
            message_ids = [msg["message_id"] for msg in pending]
            claimed = await self.redis.xclaim(
                stream_name,
                self.consumer_group,
                self.consumer_name,
                min_idle_time,
                message_ids
            )
            
            return claimed
        except Exception as e:
            logger.error(f"Error claiming pending messages for {stream_name}: {e}")
            return []
    
    async def get_stream_info(self, stream_name: str) -> Dict[str, Any]:
        """Get information about a stream"""
        try:
            info = await self.redis.xinfo_stream(stream_name)
            return info
        except Exception as e:
            logger.error(f"Error getting stream info for {stream_name}: {e}")
            return {}
    
    async def trim_stream(self, stream_name: str, max_length: int = 10000):
        """Trim a stream to keep only the latest messages"""
        try:
            await self.redis.xtrim(stream_name, maxlen=max_length)
            logger.info(f"Trimmed stream {stream_name} to {max_length} messages")
        except Exception as e:
            logger.error(f"Error trimming stream {stream_name}: {e}")
    
    async def close(self):
        """Close the Redis connection"""
        await self.redis.close()

# Global Redis Streams adapter
redis_streams = RedisStreamsAdapter(settings.redis_url)
