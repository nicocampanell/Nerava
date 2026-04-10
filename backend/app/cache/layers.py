"""
Multi-layer caching system with L1 (in-memory) and L2 (Redis) caches
"""
import asyncio
import hashlib
import json
import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional

import redis
from app.config import settings

logger = logging.getLogger(__name__)

class L1Cache:
    """In-memory L1 cache with TTL support"""
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.access_times: Dict[str, float] = {}
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from L1 cache"""
        if key not in self.cache:
            return None
        
        entry = self.cache[key]
        if time.time() > entry["expires_at"]:
            # Expired, remove it
            del self.cache[key]
            if key in self.access_times:
                del self.access_times[key]
            return None
        
        # Update access time for LRU
        self.access_times[key] = time.time()
        return entry["value"]
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in L1 cache"""
        try:
            # Check if we need to evict
            if len(self.cache) >= self.max_size and key not in self.cache:
                self._evict_lru()
            
            ttl = ttl or self.default_ttl
            self.cache[key] = {
                "value": value,
                "expires_at": time.time() + ttl
            }
            self.access_times[key] = time.time()
            return True
        except Exception as e:
            logger.error(f"Error setting L1 cache: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Delete key from L1 cache"""
        try:
            if key in self.cache:
                del self.cache[key]
            if key in self.access_times:
                del self.access_times[key]
            return True
        except Exception as e:
            logger.error(f"Error deleting from L1 cache: {e}")
            return False
    
    def _evict_lru(self):
        """Evict least recently used entry"""
        if not self.access_times:
            return
        
        lru_key = min(self.access_times.keys(), key=lambda k: self.access_times[k])
        del self.cache[lru_key]
        del self.access_times[lru_key]
    
    def clear(self):
        """Clear all entries"""
        self.cache.clear()
        self.access_times.clear()
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hit_rate": getattr(self, "_hit_rate", 0.0)
        }

class L2Cache:
    """Redis-based L2 cache"""
    
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)
        self.hit_count = 0
        self.miss_count = 0
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from L2 cache"""
        try:
            value = self.redis.get(key)
            if value:
                self.hit_count += 1
                return json.loads(value)
            else:
                self.miss_count += 1
                return None
        except Exception as e:
            logger.error(f"Error getting from L2 cache: {e}")
            self.miss_count += 1
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set value in L2 cache"""
        try:
            serialized = json.dumps(value, default=str)
            return self.redis.setex(key, ttl, serialized)
        except Exception as e:
            logger.error(f"Error setting L2 cache: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete key from L2 cache"""
        try:
            return bool(self.redis.delete(key))
        except Exception as e:
            logger.error(f"Error deleting from L2 cache: {e}")
            return False
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total = self.hit_count + self.miss_count
        hit_rate = self.hit_count / total if total > 0 else 0.0
        return {
            "hits": self.hit_count,
            "misses": self.miss_count,
            "hit_rate": hit_rate
        }

class LayeredCache:
    """Layered cache with L1 (memory) and L2 (Redis)"""
    
    def __init__(self, redis_url: str, region: str = "local"):
        self.l1 = L1Cache()
        self.l2 = L2Cache(redis_url)
        self.region = region
        self.single_flight_locks: Dict[str, asyncio.Lock] = {}
    
    def _get_cache_key(self, key: str) -> str:
        """Generate region-prefixed cache key"""
        return f"{self.region}:{key}"
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from layered cache"""
        cache_key = self._get_cache_key(key)
        
        # Try L1 first
        value = self.l1.get(cache_key)
        if value is not None:
            return value
        
        # Try L2
        value = await self.l2.get(cache_key)
        if value is not None:
            # Populate L1 with L2 value
            self.l1.set(cache_key, value)
            return value
        
        return None
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set value in both L1 and L2 caches"""
        cache_key = self._get_cache_key(key)
        
        # Set in both layers
        l1_success = self.l1.set(cache_key, value, ttl)
        l2_success = await self.l2.set(cache_key, value, ttl)
        
        return l1_success and l2_success
    
    async def delete(self, key: str) -> bool:
        """Delete key from both L1 and L2 caches"""
        cache_key = self._get_cache_key(key)
        
        l1_success = self.l1.delete(cache_key)
        l2_success = await self.l2.delete(cache_key)
        
        return l1_success and l2_success
    
    async def get_or_set(self, key: str, factory: Callable, ttl: int = 300) -> Any:
        """Get value from cache or set it using factory function"""
        cache_key = self._get_cache_key(key)
        
        # Try to get from cache first
        value = await self.get(key)
        if value is not None:
            return value
        
        # Use single-flight lock to prevent thundering herd
        if cache_key not in self.single_flight_locks:
            self.single_flight_locks[cache_key] = asyncio.Lock()
        
        async with self.single_flight_locks[cache_key]:
            # Check again after acquiring lock
            value = await self.get(key)
            if value is not None:
                return value
            
            # Generate value using factory
            try:
                if asyncio.iscoroutinefunction(factory):
                    value = await factory()
                else:
                    value = factory()
                
                # Store in cache
                await self.set(key, value, ttl)
                return value
                
            except Exception as e:
                logger.error(f"Error in cache factory for {key}: {e}")
                raise
            finally:
                # Clean up lock
                if cache_key in self.single_flight_locks:
                    del self.single_flight_locks[cache_key]
    
    def stats(self) -> Dict[str, Any]:
        """Get combined cache statistics"""
        l1_stats = self.l1.stats()
        l2_stats = self.l2.stats()
        
        return {
            "l1": l1_stats,
            "l2": l2_stats,
            "region": self.region
        }
    
    def clear(self):
        """Clear L1 cache"""
        self.l1.clear()

# Global layered cache instance
layered_cache = LayeredCache(settings.redis_url, settings.region)

def cache_key(*args, **kwargs) -> str:
    """Generate cache key from arguments"""
    key_data = {"args": args, "kwargs": kwargs}
    key_str = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.md5(key_str.encode()).hexdigest()

def cached(ttl: int = 300, key_func: Optional[Callable] = None):
    """Decorator for caching function results"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = f"{func.__name__}:{hashlib.md5(str(args).encode()).hexdigest()}"
            
            # Try to get from cache
            result = await layered_cache.get(cache_key)
            if result is not None:
                return result
            
            # Execute function
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # Store in cache
            await layered_cache.set(cache_key, result, ttl)
            return result
        
        return wrapper
    return decorator
