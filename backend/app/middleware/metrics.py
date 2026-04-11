"""
FastAPI middleware for metrics collection.
Tracks p95 latency for all endpoints, with special focus on critical endpoints.
"""
import time

from app.obs.obs import get_trace_id, record_request
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Critical endpoints for p95 latency tracking
CRITICAL_ENDPOINTS = {
    "/v1/auth/otp/verify",
    "/v1/exclusive/activate",
    "/v1/exclusive/complete",
    "/v1/intent/capture",
    "/v1/merchants/nearby",
    "/v1/verify-visit",
}

class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect request metrics and set trace IDs."""
    
    async def dispatch(self, request: Request, call_next):
        # Set trace ID in request state
        trace_id = get_trace_id(request)
        request.state.trace_id = trace_id
        
        # Start timing
        start_time = time.time()
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        
        # Extract route from request
        route = f"{request.method} {request.url.path}"
        
        # Record metrics (tracks p95 latency via histogram buckets in obs.py)
        record_request(route, duration_ms)
        
        # Log critical endpoint latency if applicable
        if request.url.path in CRITICAL_ENDPOINTS:
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(
                f"[Metrics] Critical endpoint {route}: {duration_ms:.2f}ms",
                extra={
                    "endpoint": route,
                    "duration_ms": duration_ms,
                    "trace_id": trace_id,
                }
            )
        
        # Add trace ID to response headers
        response.headers["X-Trace-Id"] = trace_id
        
        return response