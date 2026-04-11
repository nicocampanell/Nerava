"""
Region-aware middleware for multi-datacenter deployments
"""
import time
import uuid

from app.config import settings
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RegionMiddleware(BaseHTTPMiddleware):
    """Middleware for handling region-specific headers and routing"""
    
    def __init__(self, app):
        super().__init__(app)
        self.region = settings.region
        self.primary_region = settings.primary_region
    
    async def dispatch(self, request: Request, call_next):
        # Start timing
        start_time = time.time()
        
        # Generate request ID if not present
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Store in request state
        request.state.request_id = request_id
        request.state.region = self.region
        request.state.primary_region = self.primary_region
        
        # Process request
        response = await call_next(request)
        
        # Add region headers
        response.headers["X-Region"] = self.region
        response.headers["X-Primary-Region"] = self.primary_region
        response.headers["X-Request-ID"] = request_id
        
        # Add timing headers
        duration_ms = int((time.time() - start_time) * 1000)
        response.headers["X-Response-Time"] = str(duration_ms)
        
        return response

class ReadWriteRoutingMiddleware(BaseHTTPMiddleware):
    """Middleware for routing read/write operations to appropriate databases"""
    
    def __init__(self, app):
        super().__init__(app)
        self.region = settings.region
        self.primary_region = settings.primary_region
        self.read_database_url = settings.read_database_url
    
    async def dispatch(self, request: Request, call_next):
        # Determine if this is a read or write operation
        is_write_operation = self._is_write_operation(request)
        
        # Set database routing in request state
        if is_write_operation or self.region == self.primary_region:
            # Write operations or primary region - use primary database
            request.state.use_primary_db = True
        else:
            # Read operations in secondary region - use read replica
            request.state.use_primary_db = False
        
        response = await call_next(request)
        return response
    
    def _is_write_operation(self, request: Request) -> bool:
        """Determine if the request is a write operation"""
        method = request.method
        path = request.url.path
        
        # Write methods
        if method in ["POST", "PUT", "PATCH", "DELETE"]:
            return True
        
        # Specific write endpoints
        write_endpoints = [
            "/v1/energyhub/events/charge-start",
            "/v1/energyhub/events/charge-stop",
            "/v1/wallet/credit",
            "/v1/wallet/debit"
        ]
        
        for endpoint in write_endpoints:
            if path.startswith(endpoint):
                return True
        
        return False

class CanaryRoutingMiddleware(BaseHTTPMiddleware):
    """Middleware for canary deployments and traffic splitting"""
    
    def __init__(self, app, canary_percentage: float = 0.0):
        super().__init__(app)
        self.canary_percentage = canary_percentage
        self.canary_header = "X-Canary-Version"
    
    async def dispatch(self, request: Request, call_next):
        # Check for canary header
        canary_version = request.headers.get(self.canary_header)
        
        if canary_version:
            # Explicit canary request
            request.state.is_canary = True
            request.state.canary_version = canary_version
        else:
            # Random canary selection based on percentage
            import random
            is_canary = random.random() < self.canary_percentage
            request.state.is_canary = is_canary
            request.state.canary_version = "canary" if is_canary else "stable"
        
        response = await call_next(request)
        
        # Add canary headers to response
        response.headers["X-Canary-Request"] = str(request.state.is_canary).lower()
        response.headers["X-Canary-Version"] = request.state.canary_version
        
        return response
