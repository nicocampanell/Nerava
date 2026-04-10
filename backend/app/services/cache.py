import json
from typing import Any, Optional

import redis

from app.config import settings


class CacheService:
    def __init__(self):
        self.redis_client = redis.from_url(settings.redis_url)
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        try:
            value = self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception:
            return None
    
    async def setex(self, key: str, ttl_seconds: int, value: Any) -> bool:
        """Set value with TTL"""
        try:
            serialized = json.dumps(value)
            return self.redis_client.setex(key, ttl_seconds, serialized)
        except Exception:
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete key"""
        try:
            return bool(self.redis_client.delete(key))
        except Exception:
            return False
    
    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        try:
            return bool(self.redis_client.exists(key))
        except Exception:
            return False

# Global cache instance
cache = CacheService()