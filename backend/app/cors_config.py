"""
CORS configuration extracted from main_simple.py.

Call `configure_cors(app, settings, is_local, logger, ...)` to set up CORS middleware.
"""
import logging
import os
from typing import List, Tuple

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("nerava")

# Production subdomains and S3 website origins
PRODUCTION_ORIGINS = [
    "https://www.nerava.network",
    "https://nerava.network",
    "https://app.nerava.network",
    "https://link.nerava.network",
    "https://merchant.nerava.network",
    "https://admin.nerava.network",
    "https://console.nerava.network",
    # S3 website origins (HTTP, not HTTPS)
    "http://app.nerava.network.s3-website-us-east-1.amazonaws.com",
    "http://link.nerava.network.s3-website-us-east-1.amazonaws.com",
    "http://merchant.nerava.network.s3-website-us-east-1.amazonaws.com",
    "http://admin.nerava.network.s3-website-us-east-1.amazonaws.com",
    "http://nerava.network.s3-website-us-east-1.amazonaws.com",
]

# Default dev origins (when ALLOWED_ORIGINS is not set or is "*")
DEFAULT_DEV_ORIGINS = [
    "http://localhost:8001",   # Local dev UI
    "http://127.0.0.1:8001",  # Local dev UI (alternative)
    "http://localhost",        # Docker Compose proxy (port 80)
    "http://localhost:80",     # Docker Compose proxy (explicit port 80)
    "http://localhost:3000",
    "http://localhost:8080",
    "http://localhost:5173",   # Vite default
    "http://localhost:5174",   # Vite alternate port
    "http://localhost:5176",   # Campaign console
    "https://app.nerava.app",  # Production frontend
    "https://www.nerava.app",  # Production frontend (www)
]

CORS_ORIGIN_REGEX = (
    r"https://nerava-.*\.vercel\.app"
    r"|https://web-production-.*\.up\.railway\.app"
    r"|https://.*\.nerava\.network"
)

CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
CORS_HEADERS = ["Content-Type", "Authorization", "X-Requested-With"]


def validate_cors(
    settings,
    is_local: bool,
    env: str,
    _startup_validation_failed: bool,
    _startup_validation_errors: list,
) -> Tuple[bool, bool, list]:
    """Validate CORS configuration. Returns (cors_validation_failed, validation_failed, errors)."""
    cors_validation_failed = False
    try:
        if not is_local and settings.cors_allow_origins == "*":
            error_msg = (
                "CRITICAL SECURITY ERROR: CORS wildcard (*) is not allowed in non-local environment. "
                f"ENV={env}. Set ALLOWED_ORIGINS environment variable to explicit origins."
            )
            logger.error(error_msg)
            _startup_validation_failed = True
            _startup_validation_errors.append(error_msg)
            cors_validation_failed = True
            print(f"[STARTUP] WARNING: {error_msg}", flush=True)
            print("[STARTUP] Using safe CORS origins list as default", flush=True)
    except Exception as e:
        logger.error(f"CORS validation error: {e}", exc_info=True)
        _startup_validation_failed = True
        _startup_validation_errors.append(f"CORS validation error: {e}")
        cors_validation_failed = True
        print(f"[STARTUP] WARNING: CORS validation failed: {e}", flush=True)

    return cors_validation_failed, _startup_validation_failed, _startup_validation_errors


def build_origins(settings, is_local: bool, cors_validation_failed: bool) -> List[str]:
    """Build the final list of allowed CORS origins."""
    if cors_validation_failed:
        if is_local:
            allowed_origins = ["http://localhost:8001", "http://127.0.0.1:8001"]
        else:
            allowed_origins = []
    else:
        allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "*")
        if allowed_origins_str == "*":
            allowed_origins = list(DEFAULT_DEV_ORIGINS)

            # CRITICAL: If FRONTEND_URL is set with a path (e.g., "http://localhost:8001/app"),
            # extract just the origin (scheme://host:port) for CORS
            # CORS origins must be exactly scheme://host[:port] - NO PATH
            if hasattr(settings, 'FRONTEND_URL') and settings.FRONTEND_URL:
                from urllib.parse import urlparse
                parsed = urlparse(settings.FRONTEND_URL)
                frontend_origin = f"{parsed.scheme}://{parsed.netloc}"
                if frontend_origin not in allowed_origins:
                    allowed_origins.append(frontend_origin)
                    logger.info(
                        "Added FRONTEND_URL origin to CORS: %s (extracted from %s)",
                        frontend_origin, settings.FRONTEND_URL,
                    )
        else:
            allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")]

    final_origins = allowed_origins + PRODUCTION_ORIGINS
    return final_origins


def configure_cors(app: FastAPI, settings, is_local: bool, cors_validation_failed: bool):
    """Build the origins list, log it, and attach CORSMiddleware to the app."""
    final_origins = build_origins(settings, is_local, cors_validation_failed)

    print(f">>>> CORS allowed origins: {final_origins} <<<<", flush=True)
    logger.info(">>>> CORS allowed origins: %s <<<<", final_origins)

    # Ensure credentials are only allowed with explicit origins (not wildcard)
    cors_allow_credentials = True
    if "*" in final_origins and not is_local:
        logger.warning("CORS: Wildcard origin detected in non-local env, disabling credentials")
        cors_allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_origins=final_origins,
        allow_credentials=cors_allow_credentials,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        max_age=3600,
    )

    print(">>>> CORSMiddleware added successfully <<<<", flush=True)
    logger.info(">>>> CORSMiddleware added successfully <<<<")
