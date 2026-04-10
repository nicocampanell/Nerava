import hashlib
import json
from typing import Any, Optional

from app.services.cache import cache


class IdempotencyService:
    """Service for handling idempotent operations"""
    
    def __init__(self, ttl_seconds: int = 120):
        self.ttl_seconds = ttl_seconds
    
    def _generate_key(self, operation: str, payload: dict) -> str:
        """Generate idempotency key from operation and payload"""
        payload_str = json.dumps(payload, sort_keys=True)
        hash_input = f"{operation}:{payload_str}"
        return f"idempotency:{hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest()}"
    
    async def check_and_store(self, operation: str, payload: dict) -> Optional[Any]:
        """Check if operation is already processed, store if not"""
        key = self._generate_key(operation, payload)
        
        # Check if already exists
        existing = await cache.get(key)
        if existing:
            return existing
        
        # Store the operation as pending
        await cache.setex(key, self.ttl_seconds, {"status": "pending"})
        return None
    
    async def store_result(self, operation: str, payload: dict, result: Any) -> None:
        """Store the result of an operation"""
        key = self._generate_key(operation, payload)
        await cache.setex(key, self.ttl_seconds, {
            "status": "completed",
            "result": result
        })
    
    async def get_result(self, operation: str, payload: dict) -> Optional[Any]:
        """Get the result of a previously processed operation"""
        key = self._generate_key(operation, payload)
        cached = await cache.get(key)
        
        if cached and cached.get("status") == "completed":
            return cached.get("result")
        
        return None

# Global service instance
idempotency_service = IdempotencyService()
