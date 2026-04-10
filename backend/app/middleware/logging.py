import json
import logging
import time
import uuid

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Paths to skip logging (health checks generate noise and CloudWatch cost)
_SKIP_LOG_PATHS = {"/healthz", "/readyz", "/health", "/livez", "/"}

# High-volume paths to sample (log 1-in-N successful requests)
_SAMPLED_PATHS = {"/v1/charging-sessions/poll": 10}

_request_counter = 0


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        global _request_counter
        # Use existing request_id from RequestIDMiddleware if present, otherwise generate one
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        request.state.request_id = request_id

        # Start timing
        start_time = time.time()

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            path = request.url.path

            # Skip logging health check endpoints entirely
            if path in _SKIP_LOG_PATHS:
                response.headers["X-Request-ID"] = request_id
                return response

            # Sample high-volume endpoints (only log errors + 1-in-N successes)
            sample_rate = _SAMPLED_PATHS.get(path)
            if sample_rate and response.status_code < 400:
                _request_counter += 1
                if _request_counter % sample_rate != 0:
                    response.headers["X-Request-ID"] = request_id
                    return response

            # Log request
            log_data = {
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "user_id": getattr(request.state, "user_id", None),  # From AuthMiddleware
                "user_agent": request.headers.get("user-agent", ""),
                "remote_addr": request.client.host if request.client else None
            }

            logger.info(json.dumps(log_data))
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
        except HTTPException as exc:
            # HTTPException is expected - log as warning, let FastAPI handle the response
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(
                "HTTPException on %s %s: %s (status=%s) after %sms",
                request.method,
                request.url.path,
                exc.detail,
                exc.status_code,
                round(duration_ms, 2)
            )
            # Re-raise so FastAPI can return proper JSON response with status code
            raise
        except Exception as e:
            # Log unhandled exceptions with full traceback
            duration_ms = (time.time() - start_time) * 1000
            logger.exception(
                "Unhandled error on %s %s after %sms: %s",
                request.method,
                request.url.path,
                round(duration_ms, 2),
                str(e)
            )
            # Re-raise so FastAPI's exception handlers can respond
            raise
