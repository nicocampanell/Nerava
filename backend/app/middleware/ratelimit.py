import time
from typing import Dict

from app.config import settings
from app.security.ratelimit_redis import _get_redis_client
from app.security.ratelimit_redis import rate_limit as redis_rate_limit
from cachetools import TTLCache
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware using token bucket algorithm with endpoint-specific limits"""

    # Endpoint-specific rate limits (P1 security fix)
    # Format: path_prefix -> requests_per_minute
    ENDPOINT_LIMITS = {
        "/v1/auth/otp/start": 3,  # Item 32: Tight OTP start limit (3/min)
        "/v1/auth/otp/verify": 5,  # Item 32: Tight OTP verify limit (5/min)
        "/v1/auth/register": 3,  # Item 32: Tight registration limit (3/min)
        "/v1/auth/magic_link/request": 3,  # P1-4: Very strict for magic link generation (3/min)
        "/v1/auth/google": 5,  # Social auth: tight limit
        "/v1/auth/apple": 5,  # Social auth: tight limit
        "/v1/auth/tesla/login": 5,  # Social auth: tight limit
        "/v1/auth/": 10,  # Stricter for auth endpoints
        "/v1/otp/": 5,  # Very strict for OTP
        "/v1/nova/": 30,  # Moderate for Nova operations
        "/v1/redeem/": 20,  # Moderate for redemption
        "/v1/stripe/": 30,  # Moderate for Stripe
        "/v1/smartcar/": 20,  # Moderate for Smartcar
        "/v1/square/": 20,  # Moderate for Square
        "/v1/intent/capture": 120,  # Item 33: Previously exempted, now capped at 120/min
        "/v1/drivers/location/check": 120,  # Item 33: Previously exempted, now capped at 120/min
        "/v1/drivers/merchants/open": 120,  # Item 33: Previously exempted, now capped at 120/min
    }

    def __init__(self, app, requests_per_minute: int = None):
        super().__init__(app)
        self.default_requests_per_minute = requests_per_minute or settings.rate_limit_per_minute
        # Bounded TTLCache: max 50,000 buckets, auto-expire after 120s (2 min)
        # This prevents unbounded memory growth from many unique client+path combos
        self.buckets: TTLCache = TTLCache(maxsize=50000, ttl=120)
    
    def _get_client_id(self, request: Request) -> str:
        """Get client identifier for rate limiting"""
        # Use IP address as primary identifier
        client_ip = request.client.host if request.client else "unknown"
        
        # If user is authenticated, use user ID
        user_id = getattr(request.state, 'user_id', None)
        if user_id:
            return f"user:{user_id}"
        
        return f"ip:{client_ip}"
    
    def _get_limit_for_path(self, path: str) -> int:
        """Get rate limit for a specific path (exact match takes precedence)"""
        # Check for exact matches first (more specific)
        if path in self.ENDPOINT_LIMITS:
            return self.ENDPOINT_LIMITS[path]
        # Then check prefixes
        for prefix, limit in self.ENDPOINT_LIMITS.items():
            if path.startswith(prefix):
                return limit
        return self.default_requests_per_minute
    
    def _get_bucket(self, client_id: str, path: str) -> Dict:
        """Get or create token bucket for client and path"""
        # Use path-specific bucket key
        bucket_key = f"{client_id}:{path}"
        limit = self._get_limit_for_path(path)
        
        if bucket_key not in self.buckets:
            self.buckets[bucket_key] = {
                'tokens': limit,
                'last_refill': time.time(),
                'limit': limit
            }
        return self.buckets[bucket_key]
    
    def _refill_tokens(self, bucket: Dict) -> None:
        """Refill tokens based on time elapsed"""
        now = time.time()
        time_passed = now - bucket['last_refill']
        limit = bucket.get('limit', self.default_requests_per_minute)
        tokens_per_second = limit / 60.0
        tokens_to_add = time_passed * tokens_per_second
        
        bucket['tokens'] = min(
            limit,
            bucket['tokens'] + tokens_to_add
        )
        bucket['last_refill'] = now
    
    def _consume_token(self, bucket: Dict) -> bool:
        """Consume a token from the bucket"""
        self._refill_tokens(bucket)
        
        if bucket['tokens'] >= 1:
            bucket['tokens'] -= 1
            return True
        return False
    
    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting"""
        path = request.url.path

        # Skip rate limiting for health check endpoints only
        if path in {"/healthz", "/health", "/readyz", "/livez", "/"}:
            return await call_next(request)

        client_id = self._get_client_id(request)
        limit = self._get_limit_for_path(path)
        
        # P0-E: Try Redis-backed rate limiting first, fallback to in-memory
        redis_client = _get_redis_client()
        use_redis = redis_client is not None
        
        if use_redis:
            try:
                # Use Redis-backed rate limiting
                # redis_rate_limit returns True if allowed, raises HTTPException if exceeded
                redis_rate_limit(path, client_id, limit)
                # If we get here, rate limit passed - continue
                response = await call_next(request)
                # Add rate limit headers (approximate - Redis doesn't track remaining easily)
                response.headers["X-RateLimit-Limit"] = str(limit)
                response.headers["X-RateLimit-Remaining"] = "N/A"  # Redis doesn't easily provide this
                response.headers["X-RateLimit-Reset"] = str(int(time.time() // 60) * 60 + 60)
                return response
            except HTTPException:
                # Rate limit exceeded - re-raise
                raise
            except Exception as e:
                # Redis error - gracefully fallback to in-memory
                # Don't crash the app - rate limiting is not critical enough to fail requests
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Redis rate limiting failed, falling back to in-memory: {e}")
                use_redis = False
        
        if not use_redis:
            # Fallback to in-memory rate limiting
            bucket = self._get_bucket(client_id, path)
            if not self._consume_token(bucket):
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded for {path}. Limit: {limit} requests/minute. Please try again later."
                )
            response = await call_next(request)
            # Add rate limit headers
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(int(bucket['tokens']))
            response.headers["X-RateLimit-Reset"] = str(int(bucket['last_refill'] + 60))
            return response
