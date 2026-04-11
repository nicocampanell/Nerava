"""
Request ID middleware for correlation tracking

Generates a unique request_id UUID per request and injects it into:
- Response headers (X-Request-ID)
- Request state (for use in route handlers and analytics)
- Logs

Also accepts inbound X-Request-ID from frontend and forwards if present.
"""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to generate and propagate request IDs"""

    async def dispatch(self, request: Request, call_next):
        # Check if frontend sent a request_id
        incoming_request_id = request.headers.get("X-Request-ID")
        
        # Generate new request_id if not present, otherwise use incoming
        request_id = incoming_request_id or str(uuid.uuid4())
        
        # Store in request state for use in route handlers
        request.state.request_id = request_id
        
        # Log request with request_id
        logger.debug(f"Request {request_id}: {request.method} {request.url.path}")
        
        # Process request
        response = await call_next(request)
        
        # Inject request_id into response header
        response.headers["X-Request-ID"] = request_id
        
        return response







