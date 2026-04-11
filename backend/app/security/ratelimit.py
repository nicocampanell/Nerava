"""
Rate limiting utilities.
"""
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException

# In-memory rate limiting (TODO: replace with Redis in production)
_rate_limits = defaultdict(lambda: {"tokens": 0, "last_refill": time.time()})
_rate_limit_lock = Lock()

def rate_limit(key: str, limit_per_min: int):
    """Dependency to enforce rate limiting."""
    def rate_limiter():
        with _rate_limit_lock:
            now = time.time()
            rate_key = f"{key}:{now // 60}"  # Key per minute
            
            # Get or create rate limit entry
            entry = _rate_limits[rate_key]
            
            # Refill tokens if needed
            time_since_refill = now - entry["last_refill"]
            if time_since_refill >= 60:  # New minute
                entry["tokens"] = limit_per_min
                entry["last_refill"] = now
            else:
                # Add tokens based on time passed
                tokens_to_add = int(time_since_refill * limit_per_min / 60)
                entry["tokens"] = min(limit_per_min, entry["tokens"] + tokens_to_add)
                entry["last_refill"] = now
            
            # Check if we have tokens
            if entry["tokens"] <= 0:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {limit_per_min} requests per minute"
                )
            
            # Consume a token
            entry["tokens"] -= 1
            
            return True
    
    return rate_limiter
