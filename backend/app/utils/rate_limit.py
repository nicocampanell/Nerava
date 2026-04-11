"""
Rate limiting utilities for phone-first checkin flow.

Provides per-phone and per-IP rate limiting with Redis support
and in-memory fallback.
"""
import hashlib
import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Rate limit constants
PHONE_LIMIT_PER_DAY = 3
IP_LIMIT_PER_HOUR = 10
VERIFICATION_LIMIT_PER_SESSION = 10


class InMemoryRateLimiter:
    """
    Simple in-memory rate limiter with TTL.

    Thread-safe. Used as fallback when Redis is not available.
    """

    def __init__(self):
        self._store: dict = defaultdict(list)
        self._lock = Lock()

    def _cleanup(self, key: str, window_seconds: int):
        """Remove expired entries."""
        cutoff = time.time() - window_seconds
        self._store[key] = [ts for ts in self._store[key] if ts > cutoff]

    def check_and_increment(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        """
        Check if key is within rate limit and increment counter.

        Returns: (allowed, remaining_count)
        """
        with self._lock:
            self._cleanup(key, window_seconds)
            current_count = len(self._store[key])

            if current_count >= limit:
                return False, 0

            self._store[key].append(time.time())
            return True, limit - current_count - 1

    def get_count(self, key: str, window_seconds: int) -> int:
        """Get current count for key within window."""
        with self._lock:
            self._cleanup(key, window_seconds)
            return len(self._store[key])


class CheckinRateLimiter:
    """
    Rate limiter for phone-first checkin flow.

    Uses Redis if available, falls back to in-memory.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._memory = InMemoryRateLimiter()
        self._use_redis = redis_client is not None

    def _hash_phone(self, phone: str) -> str:
        """Hash phone number for rate limiting key."""
        return hashlib.sha256(phone.encode()).hexdigest()[:16]

    def _get_phone_key(self, phone: str) -> str:
        """Get rate limit key for phone."""
        phone_hash = self._hash_phone(phone)
        return f"checkin:rate:phone:{phone_hash}"

    def _get_ip_key(self, ip: str) -> str:
        """Get rate limit key for IP."""
        return f"checkin:rate:ip:{ip}"

    def check_phone_limit(self, phone: str) -> Tuple[bool, int]:
        """
        Check if phone is within daily rate limit.

        Args:
            phone: E.164 formatted phone number

        Returns:
            (allowed, remaining_count)
        """
        key = self._get_phone_key(phone)
        window_seconds = 86400  # 24 hours

        if self._use_redis:
            try:
                return self._check_redis_limit(key, PHONE_LIMIT_PER_DAY, window_seconds)
            except Exception as e:
                logger.warning(f"Redis rate limit check failed, using memory: {e}")

        return self._memory.check_and_increment(key, PHONE_LIMIT_PER_DAY, window_seconds)

    def check_ip_limit(self, ip: str) -> Tuple[bool, int]:
        """
        Check if IP is within hourly rate limit.

        Args:
            ip: Client IP address

        Returns:
            (allowed, remaining_count)
        """
        key = self._get_ip_key(ip)
        window_seconds = 3600  # 1 hour

        if self._use_redis:
            try:
                return self._check_redis_limit(key, IP_LIMIT_PER_HOUR, window_seconds)
            except Exception as e:
                logger.warning(f"Redis rate limit check failed, using memory: {e}")

        return self._memory.check_and_increment(key, IP_LIMIT_PER_HOUR, window_seconds)

    def _check_redis_limit(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        """Check rate limit using Redis sorted set with sliding window."""
        now = time.time()
        cutoff = now - window_seconds

        pipe = self._redis.pipeline()

        # Remove old entries
        pipe.zremrangebyscore(key, 0, cutoff)

        # Count current entries
        pipe.zcard(key)

        # Add new entry
        pipe.zadd(key, {str(now): now})

        # Set expiry
        pipe.expire(key, window_seconds + 1)

        results = pipe.execute()
        current_count = results[1]

        if current_count >= limit:
            # Remove the entry we just added since we're over limit
            self._redis.zrem(key, str(now))
            return False, 0

        return True, limit - current_count - 1

    def get_phone_count(self, phone: str) -> int:
        """Get current session count for phone today."""
        key = self._get_phone_key(phone)

        if self._use_redis:
            try:
                now = time.time()
                cutoff = now - 86400
                return self._redis.zcount(key, cutoff, now)
            except Exception:
                pass

        return self._memory.get_count(key, 86400)


# Singleton instance
_rate_limiter: Optional[CheckinRateLimiter] = None


def get_rate_limiter() -> CheckinRateLimiter:
    """Get singleton rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        # Try to get Redis client
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            _rate_limiter = CheckinRateLimiter(redis_client)
            logger.info("Rate limiter initialized with Redis")
        except Exception:
            _rate_limiter = CheckinRateLimiter()
            logger.info("Rate limiter initialized with in-memory store")
    return _rate_limiter
