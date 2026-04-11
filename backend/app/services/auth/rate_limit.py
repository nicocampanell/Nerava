"""
Rate limiting service for OTP authentication
Redis-backed with in-memory fallback
"""
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import Redis
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None


class RateLimitEntry:
    """Rate limit entry for a phone or IP"""
    
    def __init__(self):
        self.attempts: list[float] = []  # Timestamps of attempts
        self.last_success: Optional[float] = None  # Timestamp of last success
        self.locked_until: Optional[float] = None  # Timestamp when lockout expires


class RateLimitService:
    """
    Rate limiting service for OTP authentication.
    
    Limits:
    - start: max 3 / 10 min per phone, max 3 / 10 min per IP
    - verify: max 6 attempts / 10 min per phone
    - Cooldown: 30s after successful verify before resend allowed
    - Lockout: 15 min after too many verify failures (per phone)
    """
    
    # Rate limits (production values)
    START_LIMIT_PHONE = 5    # 5 OTP requests per phone per minute
    START_LIMIT_IP = 20      # 20 OTP requests per IP per minute
    START_WINDOW_SECONDS = 60  # 1 minute

    VERIFY_LIMIT_PHONE = 10  # 10 verify attempts per phone per minute
    VERIFY_WINDOW_SECONDS = 60  # 1 minute

    COOLDOWN_SECONDS = 30   # 30 seconds cooldown after success
    LOCKOUT_SECONDS = 300   # 5 minute lockout after exceeding limits
    
    def __init__(self, redis_client: Optional['redis.Redis'] = None):
        self._redis = redis_client
        # Fallback in-memory stores: phone -> RateLimitEntry, IP -> RateLimitEntry
        self._phone_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._ip_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._cleanup_interval = 3600  # Cleanup old entries every hour
        self._last_cleanup = time.time()
    
    def _check_limit_redis(self, key: str, max_requests: int, window_seconds: int) -> Optional[bool]:
        """Check rate limit using Redis sorted set (read-only). Returns None if Redis unavailable."""
        if not self._redis or not REDIS_AVAILABLE:
            return None
        
        try:
            now = time.time()
            pipe = self._redis.pipeline()
            # Remove old entries (cleanup)
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            # Count requests in window (read-only, no zadd)
            pipe.zcard(key)
            # Set expiry
            pipe.expire(key, window_seconds)
            _, count, _ = pipe.execute()
            return count < max_requests
        except Exception as e:
            logger.warning(f"Redis rate limit check failed, using fallback: {e}")
            return None
    
    def _get_lockout_redis(self, key: str) -> Optional[float]:
        """Get lockout expiry from Redis. Returns None if Redis unavailable."""
        if not self._redis or not REDIS_AVAILABLE:
            return None
        
        try:
            value = self._redis.get(key)
            if value:
                return float(value)
            return None
        except Exception:
            return None
    
    def _set_lockout_redis(self, key: str, expiry_seconds: int):
        """Set lockout expiry in Redis."""
        if not self._redis or not REDIS_AVAILABLE:
            return
        
        try:
            self._redis.setex(key, expiry_seconds, str(time.time() + expiry_seconds))
        except Exception as e:
            logger.warning(f"Redis lockout set failed: {e}")
    
    def _get_last_success_redis(self, key: str) -> Optional[float]:
        """Get last success timestamp from Redis."""
        if not self._redis or not REDIS_AVAILABLE:
            return None
        
        try:
            value = self._redis.get(key)
            if value:
                return float(value)
            return None
        except Exception:
            return None
    
    def _set_last_success_redis(self, key: str, timestamp: float, ttl: int):
        """Set last success timestamp in Redis."""
        if not self._redis or not REDIS_AVAILABLE:
            return
        
        try:
            self._redis.setex(key, ttl, str(timestamp))
        except Exception as e:
            logger.warning(f"Redis last success set failed: {e}")
    
    def _record_attempt_redis(self, key: str, timestamp: float, window_seconds: int):
        """Record an attempt in Redis."""
        if not self._redis or not REDIS_AVAILABLE:
            return
        
        try:
            pipe = self._redis.pipeline()
            pipe.zadd(key, {str(timestamp): timestamp})
            pipe.zremrangebyscore(key, 0, timestamp - window_seconds)
            pipe.expire(key, window_seconds)
            pipe.execute()
        except Exception as e:
            logger.warning(f"Redis attempt record failed: {e}")
    
    def _cleanup_old_entries(self):
        """Remove old entries that are outside the rate limit windows"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        cutoff_start = now - self.START_WINDOW_SECONDS
        cutoff_verify = now - self.VERIFY_WINDOW_SECONDS
        cutoff_lockout = now - self.LOCKOUT_SECONDS
        
        # Cleanup phone limits
        phones_to_remove = []
        for phone, entry in self._phone_limits.items():
            # Remove old attempts
            entry.attempts = [ts for ts in entry.attempts if ts > cutoff_verify]
            
            # Remove entry if no recent activity and not locked
            if (
                not entry.attempts
                and (not entry.last_success or entry.last_success < cutoff_start)
                and (not entry.locked_until or entry.locked_until < cutoff_lockout)
            ):
                phones_to_remove.append(phone)
        
        for phone in phones_to_remove:
            del self._phone_limits[phone]
        
        # Cleanup IP limits
        ips_to_remove = []
        for ip, entry in self._ip_limits.items():
            entry.attempts = [ts for ts in entry.attempts if ts > cutoff_start]
            
            if not entry.attempts and (not entry.last_success or entry.last_success < cutoff_start):
                ips_to_remove.append(ip)
        
        for ip in ips_to_remove:
            del self._ip_limits[ip]
        
        self._last_cleanup = now
    
    def check_rate_limit_start(self, phone: str, ip: str) -> Tuple[bool, Optional[str]]:
        """
        Check if OTP start request is allowed.
        
        Args:
            phone: Normalized phone number
            ip: Client IP address
            
        Returns:
            Tuple of (allowed, error_message)
        """
        self._cleanup_old_entries()
        now = time.time()
        
        # Check phone limit (Redis first, fallback to memory)
        phone_key = f"rate_limit:start:phone:{phone}"
        phone_result = self._check_limit_redis(phone_key, self.START_LIMIT_PHONE, self.START_WINDOW_SECONDS)
        
        if phone_result is None:
            # Fallback to in-memory
            phone_entry = self._phone_limits[phone]
            window_start = now - self.START_WINDOW_SECONDS
            recent_phone_attempts = [ts for ts in phone_entry.attempts if ts > window_start]
            phone_result = len(recent_phone_attempts) < self.START_LIMIT_PHONE
        
        if not phone_result:
            return False, "Too many OTP requests. Please wait before requesting a new code."
        
        # Check lockout (Redis first, fallback to memory)
        lockout_key = f"rate_limit:lockout:phone:{phone}"
        locked_until = self._get_lockout_redis(lockout_key)
        
        if locked_until is None:
            # Fallback to in-memory
            phone_entry = self._phone_limits[phone]
            if phone_entry.locked_until and phone_entry.locked_until > now:
                locked_until = phone_entry.locked_until
        
        if locked_until and locked_until > now:
            remaining = int(locked_until - now)
            return False, f"Phone number is temporarily locked. Please try again in {remaining} seconds."
        
        # Check cooldown (Redis first, fallback to memory)
        cooldown_key = f"rate_limit:cooldown:phone:{phone}"
        last_success = self._get_last_success_redis(cooldown_key)
        
        if last_success is None:
            # Fallback to in-memory
            phone_entry = self._phone_limits[phone]
            last_success = phone_entry.last_success
        
        if last_success:
            cooldown_until = last_success + self.COOLDOWN_SECONDS
            if cooldown_until > now:
                remaining = int(cooldown_until - now)
                return False, f"Please wait {remaining} seconds before requesting a new code."
        
        # Check IP limit (Redis first, fallback to memory)
        ip_key = f"rate_limit:start:ip:{ip}"
        ip_result = self._check_limit_redis(ip_key, self.START_LIMIT_IP, self.START_WINDOW_SECONDS)
        
        if ip_result is None:
            # Fallback to in-memory
            ip_entry = self._ip_limits[ip]
            window_start = now - self.START_WINDOW_SECONDS
            recent_ip_attempts = [ts for ts in ip_entry.attempts if ts > window_start]
            ip_result = len(recent_ip_attempts) < self.START_LIMIT_IP
        
        if not ip_result:
            return False, "Too many OTP requests from this IP. Please wait before requesting a new code."
        
        return True, None
    
    def record_start_attempt(self, phone: str, ip: str):
        """Record an OTP start attempt"""
        now = time.time()
        # Record in Redis if available
        phone_key = f"rate_limit:start:phone:{phone}"
        ip_key = f"rate_limit:start:ip:{ip}"
        self._record_attempt_redis(phone_key, now, self.START_WINDOW_SECONDS)
        self._record_attempt_redis(ip_key, now, self.START_WINDOW_SECONDS)
        # Also record in memory (fallback)
        self._phone_limits[phone].attempts.append(now)
        self._ip_limits[ip].attempts.append(now)
    
    def check_rate_limit_verify(self, phone: str) -> Tuple[bool, Optional[str]]:
        """
        Check if OTP verify attempt is allowed.
        
        Args:
            phone: Normalized phone number
            
        Returns:
            Tuple of (allowed, error_message)
        """
        self._cleanup_old_entries()
        now = time.time()
        
        # Check lockout (Redis first, fallback to memory)
        lockout_key = f"rate_limit:lockout:phone:{phone}"
        locked_until = self._get_lockout_redis(lockout_key)
        
        if locked_until is None:
            # Fallback to in-memory
            phone_entry = self._phone_limits[phone]
            if phone_entry.locked_until and phone_entry.locked_until > now:
                locked_until = phone_entry.locked_until
        
        if locked_until and locked_until > now:
            remaining = int(locked_until - now)
            return False, f"Phone number is temporarily locked. Please try again in {remaining} seconds."
        
        # Check verify limit (Redis first, fallback to memory)
        verify_key = f"rate_limit:verify:phone:{phone}"
        verify_result = self._check_limit_redis(verify_key, self.VERIFY_LIMIT_PHONE, self.VERIFY_WINDOW_SECONDS)
        
        if verify_result is None:
            # Fallback to in-memory
            phone_entry = self._phone_limits[phone]
            window_start = now - self.VERIFY_WINDOW_SECONDS
            recent_attempts = [ts for ts in phone_entry.attempts if ts > window_start]
            verify_result = len(recent_attempts) < self.VERIFY_LIMIT_PHONE
        
        if not verify_result:
            # Lock out
            lockout_until = now + self.LOCKOUT_SECONDS
            self._set_lockout_redis(lockout_key, self.LOCKOUT_SECONDS)
            # Also set in memory
            phone_entry = self._phone_limits[phone]
            phone_entry.locked_until = lockout_until
            return False, "Too many verification attempts. Phone number is temporarily locked."
        
        return True, None
    
    def record_verify_attempt(self, phone: str, success: bool):
        """
        Record an OTP verify attempt.
        
        Args:
            phone: Normalized phone number
            success: Whether verification was successful
        """
        now = time.time()
        phone_entry = self._phone_limits[phone]
        
        if success:
            # Record success and reset attempts (Redis and memory)
            cooldown_key = f"rate_limit:cooldown:phone:{phone}"
            self._set_last_success_redis(cooldown_key, now, self.COOLDOWN_SECONDS)
            phone_entry.last_success = now
            phone_entry.attempts = []  # Clear attempts on success
            phone_entry.locked_until = None  # Clear lockout
            # Clear Redis lockout
            lockout_key = f"rate_limit:lockout:phone:{phone}"
            if self._redis and REDIS_AVAILABLE:
                try:
                    self._redis.delete(lockout_key)
                except Exception:
                    pass
        else:
            # Record failed attempt (Redis and memory)
            verify_key = f"rate_limit:verify:phone:{phone}"
            self._record_attempt_redis(verify_key, now, self.VERIFY_WINDOW_SECONDS)
            phone_entry.attempts.append(now)
            
            # Check if we should lock out (after max attempts)
            window_start = now - self.VERIFY_WINDOW_SECONDS
            recent_attempts = [ts for ts in phone_entry.attempts if ts > window_start]
            
            if len(recent_attempts) >= self.VERIFY_LIMIT_PHONE:
                lockout_until = now + self.LOCKOUT_SECONDS
                phone_entry.locked_until = lockout_until
                lockout_key = f"rate_limit:lockout:phone:{phone}"
                self._set_lockout_redis(lockout_key, self.LOCKOUT_SECONDS)
    
    def is_locked_out(self, phone: str) -> bool:
        """Check if phone number is currently locked out"""
        self._cleanup_old_entries()
        now = time.time()
        phone_entry = self._phone_limits[phone]
        return phone_entry.locked_until is not None and phone_entry.locked_until > now


# Global singleton instance
_rate_limit_service: Optional[RateLimitService] = None


def get_rate_limit_service(redis_client: Optional['redis.Redis'] = None) -> RateLimitService:
    """Get or create rate limit service singleton"""
    global _rate_limit_service
    if _rate_limit_service is None:
        if redis_client is None:
            # Try to create Redis client from settings
            try:
                from app.config import settings
                if REDIS_AVAILABLE and settings.redis_url:
                    redis_url = settings.redis_url
                    # Only use Redis if URL is not localhost (production)
                    if redis_url and not redis_url.startswith("redis://localhost"):
                        redis_client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
                        redis_client.ping()  # Test connection
                        logger.info(f"Redis rate limiting enabled: {redis_url}")
                    else:
                        redis_client = None
            except Exception as e:
                logger.warning(f"Failed to initialize Redis for rate limiting, using in-memory fallback: {e}")
                redis_client = None
        _rate_limit_service = RateLimitService(redis_client=redis_client)
    return _rate_limit_service







