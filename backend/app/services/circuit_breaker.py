import time
from enum import Enum
from typing import Any, Callable, Optional

import httpx


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Circuit breaker pattern implementation"""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: int = 60,
        success_threshold: int = 3
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.success_threshold = success_threshold
        
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitState.CLOSED
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
            else:
                raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.timeout
    
    def _on_success(self) -> None:
        """Handle successful operation"""
        self.failure_count = 0
        
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = CircuitState.CLOSED
                self.success_count = 0
    
    def _on_failure(self) -> None:
        """Handle failed operation"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

class HTTPCircuitBreaker:
    """Circuit breaker specifically for HTTP calls"""
    
    def __init__(self, base_url: str, **circuit_breaker_kwargs):
        self.base_url = base_url
        self.circuit_breaker = CircuitBreaker(**circuit_breaker_kwargs)
    
    async def get(self, path: str, **kwargs) -> httpx.Response:
        """Make GET request with circuit breaker"""
        async def _make_request():
            async with httpx.AsyncClient() as client:
                return await client.get(f"{self.base_url}{path}", **kwargs)
        
        return await self.circuit_breaker.call(_make_request)
    
    async def post(self, path: str, **kwargs) -> httpx.Response:
        """Make POST request with circuit breaker"""
        async def _make_request():
            async with httpx.AsyncClient() as client:
                return await client.post(f"{self.base_url}{path}", **kwargs)
        
        return await self.circuit_breaker.call(_make_request)

# Global circuit breakers for different services
wallet_circuit_breaker = HTTPCircuitBreaker(
    "http://127.0.0.1:8000",
    failure_threshold=3,
    timeout=30
)
