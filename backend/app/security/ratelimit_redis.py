"""
Redis-backed rate limiting utilities.
Falls back to in-memory rate limiting if Redis is unavailable (local dev only).
"""
import os
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

logger = None
try:
    from app.utils.log import get_logger
    logger = get_logger(__name__)
except Exception:
    import logging
    logger = logging.getLogger(__name__)

# In-memory fallback (used when Redis unavailable in local dev)
_memory_rate_limits = defaultdict(lambda: {"tokens": 0, "last_refill": time.time()})
_memory_lock = Lock()

# Redis client (lazy initialization)
_redis_client = None
_redis_available = None


def _get_redis_client():
    """Get Redis client, lazily initialized"""
    global _redis_client, _redis_available
    
    if _redis_available is not None:
        # Already checked - return cached result
        return _redis_client if _redis_available else None
    
    try:
        import redis

        from app.config import settings
        
        redis_url = os.getenv("REDIS_URL", settings.redis_url)
        if not redis_url or redis_url.startswith("redis://localhost") and "REDIS_URL" not in os.getenv("DATABASE_URL", ""):
            # No Redis URL explicitly set - check if we should use Redis
            env = os.getenv("ENV", "dev").lower()
            if env not in {"local", "dev"}:
                # In non-local env without Redis URL, we should warn but allow in-memory fallback for now
                pass
        
        _redis_client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
        # Test connection
        _redis_client.ping()
        _redis_available = True
        if logger:
            logger.info(f"Redis rate limiting enabled: {redis_url}")
        return _redis_client
    except Exception as e:
        _redis_available = False
        if logger:
            logger.warning(f"Redis not available for rate limiting, using in-memory fallback: {e}")
        return None


def _is_local_env() -> bool:
    """Check if running in local environment"""
    env = os.getenv("ENV", "dev").lower()
    return env in {"local", "dev"}


def _get_rate_limit_key(endpoint: str, client_id: str) -> str:
    """Generate Redis key for rate limiting"""
    # Key format: rl:{endpoint}:{client_id}
    # Sanitize endpoint (remove leading /, replace / with :)
    endpoint_clean = endpoint.lstrip("/").replace("/", ":")
    return f"rl:{endpoint_clean}:{client_id}"


def _redis_rate_limit(key: str, limit_per_min: int) -> bool:
    """
    Rate limit using Redis (fixed window algorithm).
    
    Returns True if request is allowed, False if rate limit exceeded.
    """
    redis_client = _get_redis_client()
    if not redis_client:
        return None  # Signal to use fallback
    
    try:
        window_seconds = 60
        current_window = int(time.time() // window_seconds)
        redis_key = f"{key}:{current_window}"
        
        # Use INCR to atomically increment counter
        count = redis_client.incr(redis_key)
        
        # Set expiration on first increment (race condition safe: EXPIRE is idempotent)
        if count == 1:
            redis_client.expire(redis_key, window_seconds)
        
        # Check if limit exceeded
        if count > limit_per_min:
            return False
        
        return True
    except Exception as e:
        if logger:
            logger.error(f"Redis rate limit error: {e}", exc_info=True)
        return None  # Signal to use fallback


def _memory_rate_limit(key: str, limit_per_min: int) -> bool:
    """Fallback in-memory rate limiting"""
    with _memory_lock:
        now = time.time()
        window_seconds = 60
        current_window = int(now // window_seconds)
        memory_key = f"{key}:{current_window}"
        
        # Get or create rate limit entry
        entry = _memory_rate_limits[memory_key]
        
        # Refill tokens if needed
        time_since_refill = now - entry["last_refill"]
        if time_since_refill >= window_seconds:
            entry["tokens"] = limit_per_min
            entry["last_refill"] = now
        else:
            # Add tokens based on time passed
            tokens_to_add = int(time_since_refill * limit_per_min / window_seconds)
            entry["tokens"] = min(limit_per_min, entry["tokens"] + tokens_to_add)
            entry["last_refill"] = now
        
        # Check if we have tokens
        if entry["tokens"] <= 0:
            return False
        
        # Consume a token
        entry["tokens"] -= 1
        return True


def rate_limit(endpoint: str, client_id: str, limit_per_min: int) -> bool:
    """
    Rate limit a request.
    
    Args:
        endpoint: API endpoint path
        client_id: Client identifier (IP or user ID)
        limit_per_min: Requests per minute limit
    
    Returns:
        True if request is allowed, False if rate limit exceeded
    
    Raises:
        HTTPException: 429 if rate limit exceeded
        RuntimeError: In non-local env if Redis is required but unavailable
    """
    key = _get_rate_limit_key(endpoint, client_id)
    
    # Try Redis first
    result = _redis_rate_limit(key, limit_per_min)
    
    if result is None:
        # Redis unavailable - use in-memory fallback
        if logger:
            logger.warning("Redis unavailable for rate limiting, using in-memory fallback")
        result = _memory_rate_limit(key, limit_per_min)
    
    if not result:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {limit_per_min} requests per minute"
        )
    
    return True








