"""
Exception handlers extracted from main_simple.py.

Register these on a FastAPI app instance via `register_exception_handlers(app)`.
"""
import logging
import traceback

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.env import is_local_env
from app.utils.pwa_responses import shape_error

logger = logging.getLogger("nerava")


async def pilot_error_handler(request: Request, exc: HTTPException):
    """Normalize errors for pilot/PWA endpoints."""
    # Only apply to pilot endpoints
    if request.url.path.startswith("/v1/pilot/"):
        status_code_map = {
            400: "BadRequest",
            401: "Unauthorized",
            403: "Unauthorized",
            404: "NotFound",
            500: "Internal"
        }
        error_type = status_code_map.get(exc.status_code, "Internal")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=shape_error(error_type, detail)
        )
    # For non-pilot endpoints, return proper JSON response
    # CORS headers are handled by CORSMiddleware — do NOT set them manually
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Normalize validation errors for pilot/PWA endpoints."""
    if request.url.path.startswith("/v1/pilot/"):
        return JSONResponse(
            status_code=400,
            content=shape_error("BadRequest", "Invalid request data")
        )
    raise exc


async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors"""
    # Skip exception handling for static file paths - let FastAPI/Starlette handle them
    # IMPORTANT: StaticFiles should handle its own errors (404 for missing files, etc.)
    path = request.url.path
    if path.startswith("/app/") or path.startswith("/static/"):
        # For static files, allow HTTPException (both FastAPI and Starlette) to pass through
        # StaticFiles raises HTTPException for missing files (404), which should be returned properly
        if isinstance(exc, (StarletteHTTPException, HTTPException)):
            # This is a normal HTTP exception from StaticFiles - let it through
            logger.debug(f"StaticFiles HTTPException for {path}: {exc.status_code}")
            raise exc

        # For other exceptions on static paths, re-raise immediately without processing
        # Let Starlette's default handler deal with it - don't log or wrap
        raise exc

    # Handle HTTPException with CORS headers (critical for browser requests)
    # Check both FastAPI and Starlette HTTPException
    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        # Return HTTPException as JSON — CORS headers handled by CORSMiddleware
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail if hasattr(exc, 'detail') else str(exc)},
        )

    # Log unhandled exceptions (full traceback in logs)
    error_detail = str(exc)
    error_traceback = traceback.format_exc()
    logger.error(f"Unhandled exception: {error_detail}\n{error_traceback}", exc_info=True)

    # For other exceptions, return a 500 with proper CORS headers
    # In production, don't leak internal error details to clients
    if is_local_env():
        # In local/dev, return detailed error for debugging
        error_message = str(exc) if exc else "Internal server error"
        error_response = {"detail": f"Internal server error: {error_message}"}
    else:
        # In production, return generic error message (details are in logs)
        error_response = {"detail": "Internal server error"}

    # CORS headers are handled by CORSMiddleware — do NOT set them manually
    return JSONResponse(
        status_code=500,
        content=error_response,
    )


def register_exception_handlers(app):
    """Register all exception handlers on the given FastAPI app."""
    app.add_exception_handler(HTTPException, pilot_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, global_exception_handler)
