import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

# Load environment variables from .env file
load_dotenv()

from .config import settings  # noqa: E402
from .db import get_engine  # noqa: E402


def _log_startup_diagnostics():
    """Log startup diagnostics after logger is configured."""
    logger.info("=" * 60)
    logger.info("Nerava Backend - Python interpreter started")
    logger.info("Python version: %s", sys.version)
    logger.info("ENV=%s", os.getenv("ENV", "not set"))
    logger.info("PORT=%s", os.getenv("PORT", "not set"))
    logger.info("DATABASE_URL set: %s", bool(os.getenv("DATABASE_URL")))
    try:
        from urllib.parse import urlparse

        parsed = urlparse(settings.database_url)
        db_safe = f"{parsed.scheme}://{parsed.hostname or 'unknown'}/**"
    except Exception:
        db_safe = "(unparseable)"
    logger.info("Config loaded. database_url: %s", db_safe)
    logger.info("=" * 60)


# Configure logging for production visibility
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Use a consistent logger name for all app logs
logger = logging.getLogger("nerava")
logger.info("Starting Nerava Backend v9")
_log_startup_diagnostics()

# Initialize Sentry error tracking (only in non-local environments when SENTRY_DSN is set)
sentry_dsn = os.getenv("SENTRY_DSN")
from .core.env import get_env_name, is_local_env

is_local = is_local_env()
env = get_env_name()  # Define env before Sentry initialization (used at line 85)

import re as _re

_PII_PATTERNS = [
    (_re.compile(r"\+?1?\d{10,15}"), "[PHONE]"),  # phone numbers
    (_re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[JWT]"),  # JWT tokens
    (_re.compile(r"sk_live_[A-Za-z0-9]{20,}"), "[STRIPE_KEY]"),  # Stripe keys
]


def _scrub_pii_from_sentry_event(event, hint):
    """Scrub PII (phone numbers, tokens) from Sentry events."""

    def _scrub(obj):
        if isinstance(obj, str):
            for pattern, replacement in _PII_PATTERNS:
                obj = pattern.sub(replacement, obj)
            return obj
        if isinstance(obj, dict):
            return {k: _scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_scrub(i) for i in obj]
        return obj

    return _scrub(event)


if sentry_dsn and not is_local:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            # Set traces_sample_rate to 1.0 to capture 100% of transactions for performance monitoring
            # Adjust this value in production
            traces_sample_rate=0.1,
            # Set profiles_sample_rate to 1.0 to profile 100% of sampled transactions
            # Adjust this value in production
            profiles_sample_rate=0.1,
            # Environment name
            environment=env,
            # Don't send PII (scrub sensitive data)
            send_default_pii=False,
            # Additional options to scrub PII
            before_send=_scrub_pii_from_sentry_event,
        )
        logger.info(f"Sentry error tracking initialized for environment: {env}")
        print(f"[STARTUP] Sentry error tracking enabled for {env}", flush=True)
    except ImportError:
        logger.warning("sentry-sdk not installed, skipping Sentry initialization")
        print(
            "[STARTUP] WARNING: sentry-sdk not installed, skipping Sentry initialization",
            flush=True,
        )
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}", exc_info=True)
        print(f"[STARTUP] ERROR: Failed to initialize Sentry: {e}", flush=True)
elif sentry_dsn and is_local:
    logger.info("Sentry DSN configured but not initializing in local environment")
elif not sentry_dsn and not is_local:
    logger.info("Sentry DSN not configured, skipping error tracking initialization")

# Import validation functions from startup_validation module
from .core.startup_validation import (
    check_schema_payload_hash,
    ensure_merchant_schema,
    ensure_verified_visits_table,
    validate_cors_origins,
    validate_database_url,
    validate_demo_mode,
    validate_dev_flags,
    validate_jwt_secret,
    validate_merchant_auth_mock,
    validate_public_urls,
    validate_redis_url,
    validate_stripe_config,
    validate_token_encryption_key,
)

# Run validation before migrations
# CRITICAL: Make validations non-fatal to allow /healthz to serve even if validation fails
# P0-4: Default STRICT_STARTUP_VALIDATION to true in prod, false in local
# Note: is_local is already defined earlier (before Sentry initialization)
#
# IMPORTANT FOR APP RUNNER DEPLOYMENTS:
# If containers fail to start with no logs, validation is likely failing with strict mode enabled.
# Validation failures cause sys.exit(1) BEFORE uvicorn starts, so no HTTP server = no logs.
#
# To debug:
# 1. Set STRICT_STARTUP_VALIDATION=false temporarily to allow startup
# 2. Check /tmp/startup_validation_error.log in container (if it exists)
# 3. Check CloudWatch logs for "[STARTUP ERROR]" messages
# 4. Verify all required env vars are set (REDIS_URL, TOKEN_ENCRYPTION_KEY, JWT_SECRET, etc.)
#
skip_validation = os.getenv("SKIP_STARTUP_VALIDATION", "false").lower() == "true"
strict_validation_default = "true" if not is_local else "false"
strict_validation = (
    os.getenv("STRICT_STARTUP_VALIDATION", strict_validation_default).lower() == "true"
)

# Initialize tracking variables for startup validation
_startup_validation_failed = False
_startup_validation_errors = []

# Always run schema fix regardless of validation setting
# This ensures database columns exist even when skipping validation
try:
    print("[STARTUP] Ensuring database schema is up to date...", flush=True)
    ensure_merchant_schema()
    print("[STARTUP] Merchant schema check passed", flush=True)
    ensure_verified_visits_table()
    print("[STARTUP] Verified visits table check passed", flush=True)
except Exception as e:
    print(f"[STARTUP] Schema fix failed (non-critical): {e}", flush=True)

if skip_validation:
    logger.warning("Skipping strict startup validation (SKIP_STARTUP_VALIDATION=true)")
    print("[STARTUP] SKIP_STARTUP_VALIDATION=true - skipping all validation checks", flush=True)
    strict_validation = False
    print("[STARTUP] All validation checks skipped (SKIP_STARTUP_VALIDATION=true)", flush=True)
else:
    try:
        print("[STARTUP] Running validation checks...", flush=True)
        validate_jwt_secret()
        print("[STARTUP] JWT secret validation passed", flush=True)
        validate_database_url()
        print("[STARTUP] Database URL validation passed", flush=True)
        validate_redis_url()
        print("[STARTUP] Redis URL validation passed", flush=True)
        validate_dev_flags()
        print("[STARTUP] Dev flags validation passed", flush=True)
        validate_token_encryption_key()
        print("[STARTUP] TOKEN_ENCRYPTION_KEY validation passed", flush=True)
        validate_cors_origins()
        print("[STARTUP] CORS origins validation passed", flush=True)
        validate_public_urls()
        print("[STARTUP] Public URLs validation passed", flush=True)
        validate_demo_mode()
        print("[STARTUP] Demo mode validation passed", flush=True)
        validate_merchant_auth_mock()
        print("[STARTUP] Merchant auth mock validation passed", flush=True)
        validate_stripe_config()
        print("[STARTUP] Stripe config validation passed", flush=True)
        from .core.config import validate_config

        validate_config()
        print("[STARTUP] Config validation passed", flush=True)
        logger.info("All startup validations passed")
    except ValueError as e:
        error_msg = f"Startup validation failed: {e}"
        print(f"[STARTUP ERROR] {error_msg}", flush=True)
        # Log safe env var values for debugging (no secrets)
        print(f"[STARTUP ERROR] ENV={os.getenv('ENV', 'not set')}", flush=True)
        print(f"[STARTUP ERROR] REGION={settings.region}", flush=True)
        db_url = os.getenv("DATABASE_URL", "not set")
        if db_url != "not set":
            # Only log scheme, not full URL (which may contain credentials)
            scheme = db_url.split("://")[0] if "://" in db_url else "unknown"
            print(f"[STARTUP ERROR] DATABASE_URL scheme: {scheme}", flush=True)
        else:
            print("[STARTUP ERROR] DATABASE_URL: not set", flush=True)
        redis_url = os.getenv("REDIS_URL", "not set")
        if redis_url != "not set" and "://" in redis_url:
            # Extract host from Redis URL (e.g., redis://host:port/db)
            try:
                from urllib.parse import urlparse

                parsed = urlparse(redis_url)
                redis_host = (
                    f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 6379}"
                    if parsed.hostname
                    else "unknown"
                )
                print(f"[STARTUP ERROR] REDIS_URL host: {redis_host}", flush=True)
            except Exception:
                print("[STARTUP ERROR] REDIS_URL: set (cannot parse)", flush=True)
        else:
            print(f"[STARTUP ERROR] REDIS_URL: {redis_url}", flush=True)
        logger.error(error_msg, exc_info=True)
        _startup_validation_failed = True
        _startup_validation_errors.append(error_msg)
        if strict_validation:
            # Write error to file for debugging (App Runner might not capture stdout if exit is too fast)
            try:
                with open("/tmp/startup_validation_error.log", "w") as f:
                    f.write("STARTUP VALIDATION FAILED\n")
                    f.write(f"Error: {error_msg}\n")
                    f.write(f"ENV={os.getenv('ENV', 'not set')}\n")
                    f.write("STRICT_STARTUP_VALIDATION=true\n")
                    f.write(f"All validation errors: {_startup_validation_errors}\n")
                    import traceback

                    f.write(f"\nTraceback:\n{traceback.format_exc()}\n")
            except Exception as log_err:
                print(f"[STARTUP] Failed to write error log: {log_err}", flush=True)
            print(
                "[STARTUP] STRICT_STARTUP_VALIDATION enabled - exiting due to validation failure",
                flush=True,
            )
            print(
                "[STARTUP] Error details written to /tmp/startup_validation_error.log", flush=True
            )
            # Small delay to ensure logs are flushed
            import time

            time.sleep(1)
            sys.exit(1)
        else:
            print(
                "[STARTUP] WARNING: Validation failed but continuing startup (STRICT_STARTUP_VALIDATION=false)",
                flush=True,
            )
            print(
                "[STARTUP] /readyz endpoint will return 503 until validation issues are resolved",
                flush=True,
            )
    except Exception as e:
        error_msg = f"Unexpected error during startup validation: {e}"
        print(f"[STARTUP ERROR] {error_msg}", flush=True)
        # Log safe env var values for debugging (no secrets)
        print(f"[STARTUP ERROR] ENV={os.getenv('ENV', 'not set')}", flush=True)
        print(f"[STARTUP ERROR] REGION={settings.region}", flush=True)
        db_url = os.getenv("DATABASE_URL", "not set")
        if db_url != "not set":
            scheme = db_url.split("://")[0] if "://" in db_url else "unknown"
            print(f"[STARTUP ERROR] DATABASE_URL scheme: {scheme}", flush=True)
        else:
            print("[STARTUP ERROR] DATABASE_URL: not set", flush=True)
        logger.error(error_msg, exc_info=True)
        _startup_validation_failed = True
        _startup_validation_errors.append(error_msg)
        if strict_validation:
            # Write error to file for debugging (App Runner might not capture stdout if exit is too fast)
            try:
                with open("/tmp/startup_validation_error.log", "w") as f:
                    f.write("STARTUP VALIDATION FAILED\n")
                    f.write(f"Error: {error_msg}\n")
                    f.write(f"ENV={os.getenv('ENV', 'not set')}\n")
                    f.write("STRICT_STARTUP_VALIDATION=true\n")
                    f.write(f"All validation errors: {_startup_validation_errors}\n")
                    import traceback

                    f.write(f"\nTraceback:\n{traceback.format_exc()}\n")
            except Exception as log_err:
                print(f"[STARTUP] Failed to write error log: {log_err}", flush=True)
            print(
                "[STARTUP] STRICT_STARTUP_VALIDATION enabled - exiting due to validation failure",
                flush=True,
            )
            print(
                "[STARTUP] Error details written to /tmp/startup_validation_error.log", flush=True
            )
            # Small delay to ensure logs are flushed
            import time

            time.sleep(1)
            sys.exit(1)
        else:
            print(
                "[STARTUP] WARNING: Validation failed but continuing startup (STRICT_STARTUP_VALIDATION=false)",
                flush=True,
            )
            print(
                "[STARTUP] /readyz endpoint will return 503 until validation issues are resolved",
                flush=True,
            )

# Check schema in local dev (non-blocking)
check_schema_payload_hash()

# CRITICAL: Migrations removed from startup (P1 stability fix)
# Migrations must be run manually before deployment:
#   alembic upgrade head
#
# This prevents:
# - Race conditions during startup
# - Migrations running on every instance (multi-instance deployments)
# - Startup failures due to migration issues
#
# Deployment checklist:
# 1. Run migrations: alembic upgrade head
# 2. Verify migration status: alembic current
# 3. Start application

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from .middleware.audit import AuditMiddleware
from .middleware.demo_banner import DemoBannerMiddleware
from .middleware.logging import LoggingMiddleware
from .middleware.metrics import MetricsMiddleware
from .middleware.ratelimit import RateLimitMiddleware
from .middleware.region import CanaryRoutingMiddleware, ReadWriteRoutingMiddleware, RegionMiddleware
from .middleware.request_id import RequestIDMiddleware
from .middleware.request_size import RequestSizeLimitMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware

# Domain routers (imported AFTER migrations to avoid model registration conflicts)
from .routers import (
    activity,
    admin,
    affiliate_api,
    analytics,
    bootstrap,
    challenges,
    chargers,
    checkout,
    debug_pool,
    debug_verify,
    demo_charging,
    demo_qr,
    demo_square,
    dev_tools,
    discover_api,
    energyhub,
    ev_smartcar,
    events_api,
    exclusive,
    flags,
    gpt,
    grid,
    health,
    hubs,
    incentives,
    insights_api,
    intent,
    intents,
    ledger,
    merchant_analytics,
    merchant_api,
    merchant_balance,
    merchant_claim,
    merchant_onboarding,
    merchant_reports,
    merchant_rewards,
    merchant_ui,
    merchants,
    merchants_local,
    meta,
    ml,
    native_events,
    offers_api,
    ops,
    payouts,
    perks,
    places,
    pool_api,
    profile,
    purchase_webhooks,
    recommend,
    reservations,
    sessions,
    sessions_verify,
    social,
    square,
    stripe_api,
    users,
    users_register,
    vehicle_onboarding,
    virtual_cards,
    wallet,
    wallet_pass,
    webhooks,
    while_you_charge,
)
from .routers import (
    config as config_router,
)
from .routers import (
    merchants as merchants_router,
)

# Auth + JWT preferences
from .routers.auth import router as auth_router
from .routers.user_prefs import router as prefs_router
from .services.nova_accrual import nova_accrual_service

app = FastAPI(title="Nerava Backend v9", version="0.9.0")

logger.info("=" * 60)
logger.info("[STARTUP] FastAPI app object created")
logger.info("[STARTUP] App title: Nerava Backend v9")
logger.info("[STARTUP] App version: 0.9.0")
logger.info("=" * 60)
logger.info("[STARTUP] App title: Nerava Backend v9")
logger.info("[STARTUP] App version: 0.9.0")
logger.info("=" * 60)


# CRITICAL: Define /healthz and /readyz IMMEDIATELY after app creation
# This ensures they are registered before any routers that might conflict
@app.get("/healthz")
async def root_healthz():
    """Root-level health check for App Runner deployment (liveness probe).

    App Runner expects the health check at the root path /healthz.
    This endpoint provides a simple health response without database checks
    to ensure fast startup and reliable health checks.

    This is a LIVENESS check - it only verifies the HTTP server is running.
    For dependency checks, use /readyz (readiness probe).

    This endpoint is designed to NEVER fail - it always returns 200.
    """
    # Ultra-simple response - no imports, no dependencies, no exceptions
    # This must return 200 as soon as the HTTP server can respond
    return {"ok": True, "service": "nerava-backend", "version": "0.9.0", "status": "healthy"}


@app.get("/health")
async def root_health():
    """Health check endpoint for Docker Compose (alias for /healthz).

    Returns the same response as /healthz for consistency with Docker health checks.
    """
    return {"ok": True, "service": "nerava-backend", "version": "0.9.0", "status": "healthy"}


@app.get("/.well-known/appspecific/com.tesla.3p.public-key.pem")
async def tesla_public_key():
    """Serve Tesla EC public key for domain verification."""
    from fastapi.responses import PlainTextResponse

    pem = os.environ.get("TESLA_EC_PUBLIC_KEY_PEM", "")
    if not pem:
        raise HTTPException(status_code=404, detail="Public key not configured")
    # Handle escaped newlines from env var
    pem = pem.replace("\\n", "\n")
    return PlainTextResponse(
        content=pem,
        media_type="application/x-pem-file",
    )


@app.get("/test-wallet-pass")
async def test_wallet_pass():
    """Serve the pre-built signed .pkpass for testing on iPhone.

    This endpoint serves the existing signed wallet pass from wallet-pass/dist/.
    Used for testing that the pass installs correctly on iOS devices.
    """
    import os

    from fastapi.responses import Response

    # Path to the pre-built pkpass
    pkpass_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "wallet-pass",
        "dist",
        "nerava.pkpass",
    )

    if not os.path.exists(pkpass_path):
        raise HTTPException(status_code=404, detail="Pre-built wallet pass not found")

    with open(pkpass_path, "rb") as f:
        content = f.read()

    return Response(
        content=content,
        media_type="application/vnd.apple.pkpass",
        headers={"Content-Disposition": 'attachment; filename="nerava-wallet.pkpass"'},
    )


@app.get("/readyz")
async def root_readyz():
    """Readiness check - verifies database and Redis connectivity with timeouts.

    Returns 200 if all dependencies are reachable, 503 otherwise.
    App Runner can use this to determine if the service is ready to accept traffic.
    Uses short timeouts (2s DB, 1s Redis) to prevent hanging.

    Also checks startup validation status - if validation failed during startup,
    returns 503 with validation errors.
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    checks = {
        "startup_validation": {"status": "ok", "error": None},
        "database": {"status": "unknown", "error": None},
        "redis": {"status": "unknown", "error": None},
    }

    # Check startup validation status first
    if _startup_validation_failed:
        checks["startup_validation"]["status"] = "error"
        checks["startup_validation"]["error"] = "; ".join(_startup_validation_errors)
        logger.warning(
            f"[READYZ] Startup validation failed: {checks['startup_validation']['error']}"
        )

    # Check database with 2s timeout
    async def check_database():
        """Check database connectivity with timeout"""
        try:
            engine = get_engine()

            def _ping_db():
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1")).fetchone()

            # Run in thread pool since SQLAlchemy is synchronous
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _ping_db),
                timeout=2.0,
            )
            checks["database"]["status"] = "ok"
        except asyncio.TimeoutError:
            error_msg = "Database check timed out after 2s"
            checks["database"]["status"] = "error"
            checks["database"]["error"] = error_msg
            logger.error(f"[READYZ] {error_msg}")
        except Exception as e:
            error_msg = str(e)
            checks["database"]["status"] = "error"
            checks["database"]["error"] = error_msg
            logger.error(f"[READYZ] Database check failed: {error_msg}")

    # Check Redis with 1s timeout (if configured)
    async def check_redis():
        """Check Redis connectivity with timeout"""
        try:
            redis_url = settings.redis_url
            if not redis_url or redis_url == "redis://localhost:6379/0":
                checks["redis"]["status"] = "skipped"  # Not configured
                return

            import redis

            # Run in thread pool since redis client is synchronous
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, lambda: redis.from_url(redis_url, socket_connect_timeout=1).ping()
                ),
                timeout=1.0,
            )
            checks["redis"]["status"] = "ok"
        except asyncio.TimeoutError:
            error_msg = "Redis check timed out after 1s"
            checks["redis"]["status"] = "error"
            checks["redis"]["error"] = error_msg
            logger.error(f"[READYZ] {error_msg}")
        except Exception as e:
            error_msg = str(e)
            checks["redis"]["status"] = "error"
            checks["redis"]["error"] = error_msg
            logger.error(f"[READYZ] Redis check failed: {error_msg}")

    # Run checks concurrently
    await asyncio.gather(check_database(), check_redis(), return_exceptions=True)

    # Determine overall status
    # All checks must pass: startup validation, database, and redis (if configured)
    all_ok = (
        checks["startup_validation"]["status"] == "ok"
        and checks["database"]["status"] == "ok"
        and checks["redis"]["status"] in ("ok", "skipped")
    )

    status_code = 200 if all_ok else 503
    return JSONResponse(status_code=status_code, content={"ready": all_ok, "checks": checks})


# Request/Response logging middleware
# CRITICAL: This middleware MUST execute for Railway logs to show requests/errors
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests and responses for debugging in Railway"""
    # Skip detailed logging for static files to reduce noise
    is_static = request.url.path.startswith("/app/") or request.url.path.startswith("/static/")

    if not is_static:
        print(f">>>> REQUEST {request.method} {request.url.path} <<<<", flush=True)
        logger.info(">>>> REQUEST %s %s <<<<", request.method, request.url.path)

    try:
        response = await call_next(request)

        if not is_static:
            print(
                f">>>> RESPONSE {request.method} {request.url.path} -> {response.status_code} <<<<",
                flush=True,
            )
            logger.info(
                ">>>> RESPONSE %s %s -> %s <<<<",
                request.method,
                request.url.path,
                response.status_code,
            )
        return response
    except HTTPException:
        # HTTPException is expected - re-raise immediately without logging as unhandled
        raise
    except Exception as e:
        # For static files, re-raise immediately without logging to avoid interfering
        # StaticFiles will handle its own exceptions (404s, etc.) properly
        if is_static:
            raise

        # Log full stack trace in Railway logs for non-static requests
        print(
            f">>>> UNHANDLED ERROR during {request.method} {request.url.path}: {e} <<<<", flush=True
        )
        logger.exception(">>>> UNHANDLED ERROR during %s %s <<<<", request.method, request.url.path)
        raise


# CRITICAL DEBUG: Confirm middleware decorator was applied
print(">>>> Nerava Logging Middleware Decorator Applied <<<<", flush=True)
logger.info(">>>> Nerava Logging Middleware Decorator Applied <<<<")


# Redirect root to /app
@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse

    try:
        return RedirectResponse(url="/app/")
    except Exception as e:
        logger.exception("Error in root redirect: %s", str(e))
        raise


# Serve OpenAPI spec for ChatGPT Actions
@app.get("/openapi-actions.yaml")
async def get_openapi_spec(request: Request):
    """Return OpenAPI spec for ChatGPT Actions"""
    from pathlib import Path

    from fastapi.responses import Response

    # Get the current server URL from the request
    current_url = str(request.url).replace("/openapi-actions.yaml", "")

    # Try to read the generated spec file (stored next to this module)
    spec_file = Path(__file__).parent / "openapi-actions.yaml"
    if spec_file.exists():
        content = spec_file.read_text()
        # Replace any old tunnel URLs with the current one
        content = content.replace(
            "https://the-lightweight-mention-extensions.trycloudflare.com", current_url
        )
        content = content.replace("http://localhost:8001", current_url)
        return Response(content=content, media_type="text/yaml")

    # Fallback: generate a basic spec
    fallback_spec = f"""openapi: 3.0.0
info:
  title: Nerava API
  version: 1.0.0
  description: Nerava EV charging rewards platform
servers:
  - url: {current_url}
    description: Nerava API
paths:
  /v1/gpt/find_merchants:
    get:
      summary: Find nearby merchants
      operationId: find_merchants
      responses:
        '200':
          description: List of merchants
  /v1/gpt/find_charger:
    get:
      summary: Find nearby EV chargers
      operationId: find_charger
      responses:
        '200':
          description: List of chargers
  /v1/gpt/create_session_link:
    post:
      summary: Create a verify link
      operationId: create_session_link
      responses:
        '200':
          description: Verify link created
  /v1/gpt/me:
    get:
      summary: Get user profile and wallet
      operationId: get_me
      responses:
        '200':
          description: User profile
"""
    return Response(content=fallback_spec, media_type="text/yaml")


# Migrations already run at the top of this file (before router imports)
# This prevents model registration conflicts when routers import models_extra

# Create tables on startup as fallback (SQLite dev only - migrations should handle this)
# Base.metadata.create_all(bind=engine)

# Add middleware (BEFORE static mounts to ensure they process requests first)
# RequestIDMiddleware should be early to ensure request_id is available to all other middleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(LoggingMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
app.add_middleware(RegionMiddleware)
app.add_middleware(ReadWriteRoutingMiddleware)
app.add_middleware(CanaryRoutingMiddleware, canary_percentage=0.0)  # Disabled by default
app.add_middleware(DemoBannerMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Production security middleware
if settings.is_prod:
    from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    # TrustedHostMiddleware: Prevent host header injection attacks
    allowed_hosts_str = settings.ALLOWED_HOSTS or os.getenv("ALLOWED_HOSTS", "")
    if allowed_hosts_str:
        allowed_hosts_list = [host.strip() for host in allowed_hosts_str.split(",") if host.strip()]
        if allowed_hosts_list:
            app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts_list)
            logger.info(f"TrustedHostMiddleware enabled with hosts: {allowed_hosts_list}")
        else:
            logger.warning("ALLOWED_HOSTS is set but empty, skipping TrustedHostMiddleware")
    else:
        logger.warning("ALLOWED_HOSTS not set in production, skipping TrustedHostMiddleware")

    # HTTPSRedirectMiddleware: Enforce HTTPS in production
    # Note: Skip if behind ALB/load balancer that terminates TLS (set SKIP_HTTPS_REDIRECT=true)
    skip_https_redirect = os.getenv("SKIP_HTTPS_REDIRECT", "false").lower() == "true"
    if not skip_https_redirect:
        app.add_middleware(HTTPSRedirectMiddleware)
        logger.info("HTTPSRedirectMiddleware enabled in production")
    else:
        logger.info("HTTPSRedirectMiddleware skipped (SKIP_HTTPS_REDIRECT=true, likely behind ALB)")

# CORS validation and middleware setup (extracted to cors_config module)
from .cors_config import configure_cors, validate_cors

cors_validation_failed, _startup_validation_failed, _startup_validation_errors = validate_cors(
    settings, is_local, env, _startup_validation_failed, _startup_validation_errors
)
configure_cors(app, settings, is_local, cors_validation_failed)

# Mount static files - MORE SPECIFIC PATHS FIRST (order matters!)
# Mount demo charger photos (backend/static/demo_chargers) - MUST be before /static
DEMO_CHARGERS_DIR = Path(__file__).parent.parent / "static" / "demo_chargers"
if DEMO_CHARGERS_DIR.exists() and DEMO_CHARGERS_DIR.is_dir():
    app.mount(
        "/static/demo_chargers",
        StaticFiles(directory=str(DEMO_CHARGERS_DIR), html=False),
        name="demo_chargers",
    )
    logger.info("Mounted /static/demo_chargers from directory: %s", str(DEMO_CHARGERS_DIR))

# Mount merchant photos directory - MUST be before /static
# Photos are in backend/static/merchant_photos_asadas_grill/ (copied from repo root for Docker)
MERCHANT_PHOTOS_DIR = Path(__file__).parent.parent / "static" / "merchant_photos_asadas_grill"
if MERCHANT_PHOTOS_DIR.exists() and MERCHANT_PHOTOS_DIR.is_dir():
    app.mount(
        "/static/merchant_photos_asadas_grill",
        StaticFiles(directory=str(MERCHANT_PHOTOS_DIR), html=False),
        name="merchant_photos",
    )
    logger.info(
        "Mounted /static/merchant_photos_asadas_grill from directory: %s", str(MERCHANT_PHOTOS_DIR)
    )

# Mount /static for verify assets LAST (catches remaining /static/* requests)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists() and STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
    logger.info("Mounted /static from directory: %s", str(STATIC_DIR))

# Operations routes
app.include_router(ops.router)
app.include_router(flags.router)
app.include_router(analytics.router)

# PostHog Events API (for manual event triggering via Swagger)
from .routers import posthog_events

app.include_router(posthog_events.router)

# Debug endpoints (only in non-production)
if os.getenv("ENV", "dev") != "prod":
    from .routers import analytics_debug

    app.include_router(analytics_debug.router)

# Health first
app.include_router(health.router, prefix="/v1", tags=["health"])

# Public config endpoint
app.include_router(config_router.router)

# NOTE: /healthz and /readyz are defined at the top of the file (right after app creation)
# to ensure they take precedence over any router-defined endpoints

# Meta routes (health, version, debug)
app.include_router(meta.router)

# GPT routes
app.include_router(gpt.router)

# Sessions/Verify routes (public)
app.include_router(sessions.router)

# Auth + JWT prefs
app.include_router(auth_router)
app.include_router(prefs_router)

# Legacy + domain routes
app.include_router(users.router)
app.include_router(merchants_router.router)
# Register demo_qr BEFORE checkout to ensure /qr/eggman-demo-checkout matches before /qr/{token}
app.include_router(demo_qr.router)
app.include_router(checkout.router)
app.include_router(bootstrap.router)  # /v1/bootstrap/*
app.include_router(demo_square.router)
app.include_router(hubs.router, prefix="/v1/hubs", tags=["hubs"])
app.include_router(places.router)
app.include_router(recommend.router, prefix="/v1", tags=["recommend"])
app.include_router(reservations.router, prefix="/v1/reservations", tags=["reservations"])
app.include_router(intent.router)
app.include_router(exclusive.router)  # /v1/exclusive/*
app.include_router(native_events.router)  # /v1/native/*
app.include_router(vehicle_onboarding.router)
app.include_router(perks.router)
app.include_router(merchant_onboarding.router)
app.include_router(merchant_claim.router)
app.include_router(merchant_rewards.router)  # /v1/merchants/{place_id}/request-join, /v1/rewards/*

# Loyalty punch cards
from .routers import loyalty

app.include_router(loyalty.router)  # /v1/loyalty/*

# Merchant billing and ad impressions
from .routers import ad_impressions, merchant_billing

app.include_router(merchant_billing.router)  # /v1/merchant/billing/*
app.include_router(ad_impressions.router)  # /v1/ads/*
app.include_router(merchants.router)
app.include_router(wallet.router)
app.include_router(wallet_pass.router)
app.include_router(virtual_cards.router)  # /v1/virtual_cards/*
app.include_router(demo_charging.router)
app.include_router(chargers.router, prefix="/v1/chargers", tags=["chargers"])
app.include_router(webhooks.router)
app.include_router(users_register.router)
app.include_router(merchants_local.router, prefix="/v1/local", tags=["local_merchants"])
app.include_router(incentives.router)
app.include_router(energyhub.router)
app.include_router(social.router)
app.include_router(activity.router)
app.include_router(intents.router)
app.include_router(profile.router)
app.include_router(admin.router)
app.include_router(ml.router)
app.include_router(ledger.router)
app.include_router(merchant_analytics.router)
app.include_router(challenges.router)
app.include_router(grid.router)
app.include_router(payouts.router)
app.include_router(stripe_api.router)
app.include_router(purchase_webhooks.router)
app.include_router(dev_tools.router)
app.include_router(merchant_api.router)
app.include_router(merchant_ui.router)
app.include_router(square.router)
app.include_router(events_api.router)
app.include_router(pool_api.router)
app.include_router(offers_api.router)
app.include_router(sessions_verify.router)
if os.getenv("ENV", "dev") != "prod":
    app.include_router(debug_verify.router)
    app.include_router(debug_pool.router)

# vNext routers
app.include_router(discover_api.router)
app.include_router(affiliate_api.router)
app.include_router(insights_api.router)
app.include_router(while_you_charge.router)

# Phase 0 EV Arrival (phone-first PIN verification)
from .routers import arrival_v2

app.include_router(arrival_v2.router)  # /v1/arrival/*

app.include_router(merchant_reports.router)
app.include_router(merchant_balance.router)

# 14 previously missing routers (hardening fix)
from .routers import (
    account,
    charge_context,
    client_telemetry,
    clo,
    consent,
    driver_wallet,
    ev_context,
    merchant_arrivals,
    merchant_funnel,
    notifications,
    twilio_sms_webhook,
    virtual_key,
)
from .routers import (
    arrival as arrival_router,
)
from .routers import (
    checkin as checkin_router,
)

app.include_router(checkin_router.router)
app.include_router(driver_wallet.router)
app.include_router(charge_context.router)
app.include_router(ev_context.router)
app.include_router(virtual_key.router)
app.include_router(clo.router)
app.include_router(notifications.router)
app.include_router(account.router)
from .routers import referrals as referrals_router

app.include_router(referrals_router.router)  # /v1/referrals/*
from .routers import leaderboard as leaderboard_router

app.include_router(leaderboard_router.router)  # /v1/leaderboard
from .routers import public_stats as public_stats_router

app.include_router(public_stats_router.router)  # /v1/stats/public
from .routers.plaid import router as plaid_router
from .routers.plaid import wallet_router as plaid_wallet_router

app.include_router(plaid_router)  # /v1/wallet/plaid/*
app.include_router(plaid_wallet_router)  # /v1/wallet/funding-sources
app.include_router(consent.router)
app.include_router(merchant_funnel.router)
app.include_router(merchant_arrivals.router)

# Toast POS Integration
from .routers import toast_pos

app.include_router(toast_pos.router)  # /v1/merchant/pos/*
app.include_router(twilio_sms_webhook.router)
app.include_router(client_telemetry.router)
app.include_router(arrival_router.router)

# Console auth (email OTP for sponsor portal)
from .routers import console_auth

app.include_router(console_auth.router)  # /v1/console/auth/*

# Campaign / Incentive Layer routers
from .routers import campaign_sessions
from .routers import campaigns as campaigns_router

app.include_router(campaign_sessions.router)  # /v1/charging-sessions/*
app.include_router(campaigns_router.router)  # /v1/campaigns/*

# Partner Incentive API
from .routers import admin_partners, partner_api

app.include_router(partner_api.router)  # /v1/partners/*
app.include_router(admin_partners.router)  # /v1/admin/partners/*

# Admin Analytics
from .routers import admin_analytics

app.include_router(admin_analytics.router)  # /v1/admin/analytics/*

# Admin Charger Management
from .routers import admin_chargers

app.include_router(admin_chargers.router)  # /v1/admin/chargers/*

# Admin Tesla Fleet API tooling
from .routers import admin_tesla

app.include_router(admin_tesla.router)  # /v1/admin/tesla/*

# Consolidated Stripe Webhooks
from .routers import stripe_webhooks

app.include_router(stripe_webhooks.router)  # /v1/stripe/webhooks

# Tesla Fleet Telemetry routers
from .routers import tesla_telemetry, tesla_telemetry_config

app.include_router(tesla_telemetry.router)  # /v1/webhooks/tesla/telemetry
app.include_router(tesla_telemetry_config.router)  # /v1/tesla/configure-telemetry

# Canonical v1 API routers (promoted from Domain Charge Party MVP)
# These are the production endpoints that the PWA uses
from .routers import admin_domain, auth_domain, drivers_domain, merchants_domain, nova_domain

# Stripe router (optional - only load if stripe package is available)
try:
    from .routers import stripe_domain

    STRIPE_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as e:
    logger.warning(f"Stripe router not available (stripe package not installed): {e}")
    STRIPE_AVAILABLE = False
    stripe_domain = None

# These are now the canonical /v1/* endpoints (no /domain/ prefix)
app.include_router(auth_domain.router)  # /v1/auth/*
app.include_router(drivers_domain.router)  # /v1/drivers/* (includes /merchants/nearby)
app.include_router(merchants_domain.router)  # /v1/merchants/*
if STRIPE_AVAILABLE:
    app.include_router(stripe_domain.router)  # /v1/stripe/*
app.include_router(admin_domain.router)  # /v1/admin/*
app.include_router(nova_domain.router)  # /v1/nova/*

# EV/Smartcar integration
app.include_router(ev_smartcar.router)  # /v1/ev/* and /oauth/smartcar/callback

# Tesla Fleet API OAuth integration
from .routers import tesla_auth

app.include_router(tesla_auth.router, prefix="/v1")  # /v1/auth/tesla/*

# Debug router for logging verification
from fastapi import APIRouter as DebugRouter

debug_router = DebugRouter()


@debug_router.get("/v1/debug/log-test")
async def debug_log_test():
    """Test endpoint to verify logging is working in Railway"""
    logger.info("DEBUG LOG TEST endpoint hit")
    # Intentionally raise an error to generate a traceback in logs
    from fastapi import HTTPException

    raise HTTPException(status_code=500, detail="Intentional test error for logging")


app.include_router(debug_router)

# ─── Temporary ops endpoint for merchant seeding (remove after use) ───
import uuid as _uuid

_OPS_KEY = os.environ.get("OPS_API_KEY", "")


@app.get("/v1/ops/seed-stats")
async def ops_seed_stats(key: str = ""):
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    from .db import SessionLocal
    from .models.while_you_charge import Charger, ChargerMerchant, Merchant

    db = SessionLocal()
    try:
        return {
            "chargers": db.query(Charger).count(),
            "merchants": db.query(Merchant).count(),
            "charger_merchant_links": db.query(ChargerMerchant).count(),
        }
    finally:
        db.close()


@app.post("/v1/ops/seed-merchants")
async def ops_seed_merchants(key: str = "", max_cells: int = 0):
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    import threading
    from datetime import datetime, timezone

    from .routers.admin_domain import _run_seed_merchants_job, _seed_jobs

    # Check for already running
    for jid, job in _seed_jobs.items():
        if job["type"] == "merchants" and job["status"] == "running":
            return {"job_id": jid, "status": "already_running"}
    job_id = f"merchant_seed_ops_{_uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": "merchants",
        "status": "starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_by": "ops_endpoint",
        "progress": {},
        "result": None,
        "error": None,
    }
    thread = threading.Thread(
        target=_run_seed_merchants_job,
        args=(job_id, max_cells if max_cells > 0 else None),
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "status": "started"}


@app.post("/v1/ops/run-migrations")
async def ops_run_migrations(key: str = ""):
    """Run Alembic migrations to head. Protected by OPS_API_KEY."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        from .run_migrations import run_migrations as _run_mig

        _run_mig()
        return {"ok": True, "status": "migrations_complete"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/v1/ops/seed-merchants-city")
async def ops_seed_merchants_city(city: str = "", key: str = ""):
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    import threading
    from datetime import datetime, timezone

    # Validate city
    from scripts.seed_merchants_city import CITY_BBOXES

    from .routers.admin_domain import _seed_jobs

    if city not in CITY_BBOXES:
        raise HTTPException(
            status_code=400, detail=f"Unknown city: {city}. Available: {list(CITY_BBOXES.keys())}"
        )

    # Check for already running city seed
    for jid, job in _seed_jobs.items():
        if job["type"] == f"merchants_city_{city}" and job["status"] == "running":
            return {"job_id": jid, "status": "already_running"}

    job_id = f"city_seed_{city}_{_uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": f"merchants_city_{city}",
        "status": "starting",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_by": "ops_endpoint",
        "progress": {},
        "result": None,
        "error": None,
    }

    def _run_city_seed(jid, city_name):
        import asyncio

        from scripts.seed_merchants_city import seed_city

        from app.db import SessionLocal

        _seed_jobs[jid]["status"] = "running"
        db = SessionLocal()
        try:
            result = asyncio.run(seed_city(db, city_name))
            _seed_jobs[jid]["status"] = "completed"
            _seed_jobs[jid]["result"] = result
            _seed_jobs[jid]["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            _seed_jobs[jid]["status"] = "failed"
            _seed_jobs[jid]["error"] = str(e)
        finally:
            db.close()

    thread = threading.Thread(target=_run_city_seed, args=(job_id, city), daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "started", "city": city}


@app.get("/v1/ops/seed-status")
async def ops_seed_status(key: str = ""):
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .routers.admin_domain import _seed_jobs

    return {"jobs": _seed_jobs}


@app.get("/v1/ops/seed-debug")
async def ops_seed_debug(key: str = "", charger_id: str = ""):
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from sqlalchemy import text

    from .db import SessionLocal
    from .models.while_you_charge import Charger, ChargerMerchant, Merchant

    db = SessionLocal()
    try:
        # Sample charger IDs from junction table
        junction_charger_ids = [
            r[0] for r in db.query(ChargerMerchant.charger_id).distinct().limit(20).all()
        ]
        # Sample charger IDs from chargers table
        charger_ids = [r[0] for r in db.query(Charger.id).limit(20).all()]
        # Check a specific charger
        specific_links = []
        if charger_id:
            links = (
                db.query(ChargerMerchant)
                .filter(ChargerMerchant.charger_id == charger_id)
                .limit(5)
                .all()
            )
            for l in links:
                m = db.query(Merchant).filter(Merchant.id == l.merchant_id).first()
                specific_links.append(
                    {
                        "merchant_id": l.merchant_id,
                        "name": m.name if m else "NOT FOUND",
                        "distance_m": l.distance_m,
                    }
                )
        # Count chargers that have at least 1 merchant
        chargers_with_merchants = db.execute(
            text("SELECT COUNT(DISTINCT charger_id) FROM charger_merchants")
        ).scalar()
        # Check ID format distribution
        nrel_count = (
            db.query(ChargerMerchant).filter(ChargerMerchant.charger_id.like("nrel_%")).count()
        )
        return {
            "junction_charger_id_samples": junction_charger_ids,
            "charger_id_samples": charger_ids,
            "chargers_with_merchants": chargers_with_merchants,
            "nrel_junction_count": nrel_count,
            "specific_charger_links": specific_links,
        }
    finally:
        db.close()


@app.post("/v1/ops/configure-telemetry")
async def ops_configure_telemetry(user_id: int = 0, key: str = "", proxy_url: str = ""):
    """Temporary ops endpoint to trigger telemetry config for debugging."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .db import SessionLocal
    from .models.tesla_connection import TeslaConnection
    from .services.tesla_oauth import get_tesla_oauth_service, get_valid_access_token

    db = SessionLocal()
    try:
        conn = (
            db.query(TeslaConnection)
            .filter(
                TeslaConnection.user_id == user_id,
                TeslaConnection.is_active == True,
            )
            .first()
        )
        if not conn:
            return {"error": f"No active Tesla connection for user {user_id}"}
        oauth = get_tesla_oauth_service()
        token = await get_valid_access_token(db, conn, oauth)
        if not token:
            return {"error": "Could not get valid access token"}
        if proxy_url:
            # Route through Vehicle Command HTTP Proxy
            result = await oauth.subscribe_vehicle_telemetry(
                token,
                conn.vin,
                proxy_base_url=proxy_url,
            )
        else:
            result = await oauth.subscribe_vehicle_telemetry(token, conn.vin)
        conn.telemetry_enabled = True
        from datetime import datetime

        conn.telemetry_configured_at = datetime.utcnow()
        db.commit()
        return {"status": "configured", "vin": conn.vin, "result": result}
    except Exception as e:
        resp_body = None
        if hasattr(e, "response") and e.response is not None:
            resp_body = e.response.text
        return {"error": str(e), "response_body": resp_body}
    finally:
        db.close()


@app.get("/v1/ops/tesla-token")
async def ops_tesla_token(user_id: int = 0, key: str = ""):
    """Get a valid Tesla access token for a user (for proxy-based telemetry config)."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .db import SessionLocal
    from .models.tesla_connection import TeslaConnection
    from .services.tesla_oauth import get_tesla_oauth_service, get_valid_access_token

    db = SessionLocal()
    try:
        conn = (
            db.query(TeslaConnection)
            .filter(
                TeslaConnection.user_id == user_id,
                TeslaConnection.is_active == True,
            )
            .first()
        )
        if not conn:
            return {"error": f"No active Tesla connection for user {user_id}"}
        oauth = get_tesla_oauth_service()
        token = await get_valid_access_token(db, conn, oauth)
        if not token:
            return {"error": "Could not get valid access token"}
        return {"access_token": token, "vin": conn.vin}
    finally:
        db.close()


@app.get("/v1/ops/user-lookup")
async def ops_user_lookup(email: str = "", key: str = ""):
    """Ops: Look up user by email and return admin status."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .db import SessionLocal
    from .models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"found": False, "email": email}
        return {
            "found": True,
            "id": user.id,
            "email": user.email,
            "phone_number": getattr(user, "phone_number", None),
            "admin_role": user.admin_role,
            "role_flags": user.role_flags,
            "is_active": user.is_active,
        }
    finally:
        db.close()


@app.post("/v1/ops/set-admin")
async def ops_set_admin(email: str = "", admin_role: str = "super_admin", key: str = ""):
    """Ops: Set admin_role for a user by email."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .db import SessionLocal
    from .models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User {email} not found")
        user.admin_role = admin_role
        db.commit()
        return {"ok": True, "user_id": user.id, "email": email, "admin_role": admin_role}
    finally:
        db.close()


@app.post("/v1/ops/set-password")
async def ops_set_password(email: str = "", password: str = "", key: str = ""):
    """Ops: Set password for a user by email."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    from .core.security import hash_password
    from .db import SessionLocal
    from .models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User {email} not found")
        user.password_hash = hash_password(password)
        db.commit()
        return {"ok": True, "user_id": user.id, "email": email}
    finally:
        db.close()


@app.post("/v1/ops/test-push")
async def ops_test_push(
    email: str = "",
    title: str = "Test Push",
    body: str = "This is a test notification from Nerava",
    key: str = "",
):
    """Ops: Send a test push notification to a user by email."""
    if not _OPS_KEY or key != _OPS_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from .db import SessionLocal
    from .models.device_token import DeviceToken
    from .models.user import User
    from .services.push_service import _get_apns_client, send_push_notification

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User {email} not found")
        tokens = (
            db.query(DeviceToken)
            .filter(
                DeviceToken.user_id == user.id,
                DeviceToken.is_active.is_(True),
            )
            .all()
        )
        client = _get_apns_client()
        sent = send_push_notification(
            db,
            user.id,
            title=title,
            body=body,
            data={"type": "test_push"},
        )
        return {
            "ok": True,
            "user_id": user.id,
            "email": email,
            "device_tokens": len(tokens),
            "token_previews": [t.token[:12] + "..." for t in tokens],
            "apns_client_initialized": client is not None,
            "notifications_sent": sent,
        }
    finally:
        db.close()


# ─── End temporary ops endpoint ───


# Exception handlers (extracted to exception_handlers module)
from .exception_handlers import register_exception_handlers

register_exception_handlers(app)


# Start Nova accrual service on startup (demo mode only)
@app.on_event("startup")
async def start_nova_accrual():
    """Start Nova accrual service for demo mode - non-blocking startup"""
    logger.info("[STARTUP] Startup event entered")

    # One-time exclusive title update (safe to remove after deploy)
    _db = None
    try:
        from sqlalchemy import func as _func

        from .db import SessionLocal
        from .models.domain import DomainMerchant as _DM
        from .models.while_you_charge import ChargerMerchant as _CM
        from .models.while_you_charge import Merchant as _WYCMerchant

        _db = SessionLocal()
        _new_title = "Free Garlic Knots"
        # Update ALL ChargerMerchant links for any WYC merchant with "heights" and "pizzeria" in name
        _mids = [
            m.id
            for m in _db.query(_WYCMerchant)
            .filter(
                _func.lower(_WYCMerchant.name).contains("heights"),
                _func.lower(_WYCMerchant.name).contains("pizzeria"),
            )
            .all()
        ]
        if _mids:
            _n = (
                _db.query(_CM)
                .filter(_CM.merchant_id.in_(_mids))
                .update({_CM.exclusive_title: _new_title}, synchronize_session=False)
            )
            # Also update WYC merchant perk_labels
            _db.query(_WYCMerchant).filter(_WYCMerchant.id.in_(_mids)).update(
                {_WYCMerchant.perk_label: _new_title}, synchronize_session=False
            )
            logger.info("Updated %d CM links + %d WYC merchants -> %s", _n, len(_mids), _new_title)
        # Also update DomainMerchant perk_label
        _dm_n = (
            _db.query(_DM)
            .filter(
                _func.lower(_DM.name).contains("heights"),
                _func.lower(_DM.name).contains("pizzeria"),
            )
            .update({_DM.perk_label: _new_title}, synchronize_session=False)
        )
        if _dm_n:
            logger.info("Updated %d DomainMerchants -> %s", _dm_n, _new_title)
        _db.commit()
    except Exception as _e:
        if _db is not None:
            _db.rollback()
        logger.warning("Exclusive fix skipped: %s", _e)
    finally:
        if _db is not None:
            _db.close()

    # Check startup mode (light mode skips optional workers for faster startup)
    startup_mode = os.getenv("APP_STARTUP_MODE", "light").lower()
    is_light_mode = startup_mode == "light"

    # Availability collector runs in ALL modes (lightweight: 10 API calls every 5 min)
    try:
        from .workers.availability_collector import run_collector

        asyncio.create_task(run_collector())
        print("[STARTUP] Charger availability collector started", flush=True)
        logger.info("[STARTUP] Charger availability collector started")
    except Exception as e:
        print(f"[STARTUP WARNING] Availability collector failed to start: {e}", flush=True)
        logger.warning(f"Availability collector failed to start: {e}")

    if is_light_mode:
        print("[STARTUP] Light mode: skipping optional background workers", flush=True)
        logger.info("[STARTUP] Light mode: skipping optional background workers")
        print("[STARTUP] Startup event completed (non-blocking, light mode)", flush=True)
        logger.info("[STARTUP] Startup event completed (non-blocking, light mode)")
        return

    # Full mode: schedule background services as non-blocking tasks
    print("[STARTUP] Full mode: starting background services (non-blocking)...", flush=True)
    logger.info("[STARTUP] Full mode: starting background services (non-blocking)")

    async def start_background_services():
        """Background task to start optional services - failures are logged but don't crash startup"""
        try:
            print("[STARTUP] Starting Nova accrual service...", flush=True)
            await nova_accrual_service.start()
            print(
                "[STARTUP] Nova accrual service started (or skipped if not in demo mode)",
                flush=True,
            )
            logger.info("Nova accrual service started")
        except Exception as e:
            error_msg = f"Failed to start Nova accrual service: {e}"
            print(f"[STARTUP WARNING] {error_msg}", flush=True)
            logger.warning(error_msg, exc_info=True)

        # Start HubSpot sync worker
        try:
            from .workers.hubspot_sync import hubspot_sync_worker

            print("[STARTUP] Starting HubSpot sync worker...", flush=True)
            await hubspot_sync_worker.start()
            print("[STARTUP] HubSpot sync worker started (or skipped if not enabled)", flush=True)
            logger.info("HubSpot sync worker started")
        except Exception as e:
            error_msg = f"Failed to start HubSpot sync worker: {e}"
            print(f"[STARTUP WARNING] {error_msg}", flush=True)
            logger.warning(error_msg, exc_info=True)

        print("[STARTUP] Background services initialization complete", flush=True)
        logger.info("Background services initialization complete")

    # Schedule as background task - don't await (non-blocking)
    asyncio.create_task(start_background_services())

    print("[STARTUP] Startup event completed (non-blocking, services scheduled)", flush=True)
    logger.info("[STARTUP] Startup event completed (non-blocking, services scheduled)")


@app.on_event("shutdown")
async def stop_nova_accrual():
    """Stop Nova accrual service on shutdown"""
    await nova_accrual_service.stop()

    # Stop HubSpot sync worker
    try:
        from .workers.hubspot_sync import hubspot_sync_worker

        await hubspot_sync_worker.stop()
    except Exception as e:
        logger.warning(f"Failed to stop HubSpot sync worker: {e}")
