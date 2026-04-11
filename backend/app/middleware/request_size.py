"""
Request size limit middleware.

Rejects requests whose Content-Length exceeds the configured maximum.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

MAX_REQUEST_SIZE = 10 * 1024 * 1024  # 10 MB


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with Content-Length > MAX_REQUEST_SIZE."""

    def __init__(self, app: ASGIApp, max_size: int = MAX_REQUEST_SIZE):
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": f"Request body too large. Maximum size is {self.max_size // (1024 * 1024)}MB."
                },
            )
        return await call_next(request)
