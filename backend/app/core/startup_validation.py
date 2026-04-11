"""
Startup validation functions.

These functions validate critical configuration before the application starts.
They are called during application startup and will raise ValueError if validation fails.
"""
import logging
import os
import re

from app.core.config import settings
from app.core.env import is_local_env

logger = logging.getLogger("nerava")


def validate_jwt_secret():
    """Validate JWT secret is not database URL in non-local environments"""
    if is_local_env():
        return
    
    if settings.jwt_secret == settings.DATABASE_URL:
        error_msg = (
            "CRITICAL SECURITY ERROR: JWT secret cannot equal database_url in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. Set JWT_SECRET environment variable to a secure random value."
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if not settings.jwt_secret or settings.jwt_secret == "dev-secret" or settings.jwt_secret == "dev-secret-change-me":
        error_msg = (
            "CRITICAL SECURITY ERROR: JWT secret must be set and not use default value in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. Set JWT_SECRET or NERAVA_SECRET_KEY environment variable."
        )
        print("[Startup] Missing required env var: JWT_SECRET (must be a secure random value, not 'dev-secret' or 'dev-secret-change-me')", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("JWT secret validation passed (not equal to database_url)")


def validate_database_url():
    """Validate database URL is not SQLite in non-local environments"""
    if is_local_env():
        return
    
    database_url = os.getenv("DATABASE_URL", settings.DATABASE_URL)
    if re.match(r'^sqlite:', database_url, re.IGNORECASE):
        # Extract scheme only for logging (security: don't print full URL)
        db_scheme = "sqlite:///..." if "sqlite" in database_url.lower() else "unknown"
        error_msg = (
            "CRITICAL: SQLite database is not supported in production. "
            f"DATABASE_URL={database_url[:50]}..., ENV={os.getenv('ENV', 'dev')}. "
            "Please use PostgreSQL (e.g., RDS, managed Postgres)."
        )
        print(f"[Startup] Refusing to start with SQLite database_url={db_scheme}. Use PostgreSQL instead.", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("Database URL validation passed (not SQLite)")


def validate_redis_url():
    """Validate Redis URL is configured in non-local environments"""
    if is_local_env():
        return
    
    redis_url = os.getenv("REDIS_URL", settings.REDIS_URL)
    # Check if Redis URL is set and not the default localhost value
    if not redis_url or redis_url == "redis://localhost:6379/0":
        error_msg = (
            "CRITICAL: REDIS_URL must be configured in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. Redis is required for rate limiting in production. "
            "Please set REDIS_URL environment variable to a valid Redis connection string."
        )
        print("[Startup] Redis URL validation failed: REDIS_URL not configured", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("Redis URL validation passed (REDIS_URL is configured)")


def validate_dev_flags():
    """Validate dev-only flags are not enabled in non-local environments"""
    if is_local_env():
        return
    
    if os.getenv("NERAVA_DEV_ALLOW_ANON_USER", "false").lower() == "true":
        error_msg = (
            "CRITICAL: NERAVA_DEV_ALLOW_ANON_USER cannot be enabled in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. This is a security risk."
        )
        print("[Startup] Dev flag violation: NERAVA_DEV_ALLOW_ANON_USER is enabled (security risk)", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    if os.getenv("NERAVA_DEV_ALLOW_ANON_DRIVER", "false").lower() == "true":
        error_msg = (
            "CRITICAL: NERAVA_DEV_ALLOW_ANON_DRIVER cannot be enabled in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. This is a security risk."
        )
        print("[Startup] Dev flag violation: NERAVA_DEV_ALLOW_ANON_DRIVER is enabled (security risk)", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Check DEMO_MODE - if it bypasses auth, fail in prod
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
    if demo_mode:
        # DEMO_MODE is allowed in local, but warn if it's enabled in prod
        # (It's already gated by is_local_env() in checkout.py, but we should still warn)
        logger.warning(
            f"DEMO_MODE is enabled in {os.getenv('ENV', 'dev')} environment. "
            "Ensure it does not bypass authentication in production code paths."
        )
    
    logger.info("Dev flags validation passed (no dev flags enabled)")


def validate_token_encryption_key():
    """Validate TOKEN_ENCRYPTION_KEY is set and valid in non-local environments"""
    if is_local_env():
        return
    
    token_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not token_key:
        error_msg = (
            "CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY environment variable is required in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}. This key is used to encrypt vehicle and Square tokens. "
            "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Validate key format (Fernet keys are 44-char base64)
    if len(token_key) != 44:
        error_msg = (
            "CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY must be a valid Fernet key (44 characters base64). "
            f"ENV={os.getenv('ENV', 'dev')}, key length={len(token_key)}. "
            "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Validate key is valid Fernet key by attempting to construct Fernet instance
    try:
        from cryptography.fernet import Fernet
        Fernet(token_key.encode('utf-8'))
        logger.info("TOKEN_ENCRYPTION_KEY validation passed (valid Fernet key)")
    except Exception as e:
        error_msg = (
            "CRITICAL SECURITY ERROR: TOKEN_ENCRYPTION_KEY is not a valid Fernet key. "
            f"ENV={os.getenv('ENV', 'dev')}, error={str(e)}. "
            "Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)


def validate_cors_origins():
    """Validate CORS origins are not wildcard (*) in non-local environments"""
    # Check environment directly to avoid caching issues in tests
    env = os.getenv("ENV", "dev").lower()
    if env == "local":
        return
    
    # Get from environment variable directly, with fallback to empty string
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
    if allowed_origins == "*" or (allowed_origins and "*" in allowed_origins):
        error_msg = (
            "CRITICAL SECURITY ERROR: CORS wildcard (*) is not allowed in non-local environment. "
            f"ENV={os.getenv('ENV', 'dev')}, ALLOWED_ORIGINS={allowed_origins[:50]}... "
            "Set ALLOWED_ORIGINS environment variable to explicit origins (comma-separated list). "
            "Example: ALLOWED_ORIGINS=https://app.nerava.network,https://www.nerava.network"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("CORS origins validation passed (no wildcard in prod)")


def validate_public_urls():
    """Validate PUBLIC_BASE_URL and FRONTEND_URL don't contain localhost in production"""
    if is_local_env():
        return
    
    env = os.getenv("ENV", "dev").lower()
    if env != "prod":
        return  # Only validate in prod
    
    # Validate PUBLIC_BASE_URL
    public_base_url = os.getenv("PUBLIC_BASE_URL", getattr(settings, 'PUBLIC_BASE_URL', ''))
    if public_base_url and ("localhost" in public_base_url.lower() or "127.0.0.1" in public_base_url):
        error_msg = (
            f"CRITICAL: PUBLIC_BASE_URL cannot point to localhost in production. "
            f"Current value: {public_base_url}. "
            "Set PUBLIC_BASE_URL to your production API domain (e.g., https://api.nerava.network)"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    # Validate FRONTEND_URL
    frontend_url = os.getenv("FRONTEND_URL", getattr(settings, 'FRONTEND_URL', ''))
    if frontend_url and ("localhost" in frontend_url.lower() or "127.0.0.1" in frontend_url):
        error_msg = (
            f"CRITICAL: FRONTEND_URL cannot point to localhost in production. "
            f"Current value: {frontend_url}. "
            "Set FRONTEND_URL to your production frontend domain (e.g., https://app.nerava.network)"
        )
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("Public URLs validation passed (no localhost in prod)")


def validate_demo_mode():
    """Validate DEMO_MODE is disabled in production"""
    if is_local_env():
        return
    
    env = os.getenv("ENV", "dev").lower()
    if env != "prod":
        return  # Only validate in prod
    
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
    if demo_mode:
        error_msg = "DEMO_MODE=true is not allowed in production"
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("Demo mode validation passed (disabled in prod)")


def validate_merchant_auth_mock():
    """Validate MERCHANT_AUTH_MOCK is disabled in production"""
    if is_local_env():
        return
    
    env = os.getenv("ENV", "dev").lower()
    if env != "prod":
        return  # Only validate in prod
    
    merchant_auth_mock = os.getenv("MERCHANT_AUTH_MOCK", "false").lower() == "true"
    if merchant_auth_mock:
        error_msg = "MERCHANT_AUTH_MOCK=true is not allowed in production"
        print(f"[Startup] {error_msg}", flush=True)
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info("Merchant auth mock validation passed (disabled in prod)")


def validate_stripe_config():
    """Warn if STRIPE_SECRET_KEY is set but ENABLE_STRIPE_PAYOUTS is not enabled."""
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    enable_payouts = os.getenv("ENABLE_STRIPE_PAYOUTS", "false").lower() == "true"

    if stripe_key and not enable_payouts:
        logger.warning(
            "STRIPE_SECRET_KEY is set but ENABLE_STRIPE_PAYOUTS != 'true'. "
            "Payouts will run in mock mode. Set ENABLE_STRIPE_PAYOUTS=true to enable real payouts."
        )


def ensure_merchant_schema():
    """Ensure merchants table has short_code and region_code columns.

    This handles cases where Alembic migrations are out of sync with the database.
    Runs in all environments to fix schema mismatches.
    """
    try:
        from app.db import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            # Check if short_code column exists
            result = db.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'merchants' AND column_name = 'short_code'
            """))
            has_short_code = result.fetchone() is not None

            if not has_short_code:
                logger.info("Adding missing short_code and region_code columns to merchants table...")

                # Add columns
                db.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS short_code VARCHAR(16)"))
                db.execute(text("ALTER TABLE merchants ADD COLUMN IF NOT EXISTS region_code VARCHAR(8) DEFAULT 'ATX'"))

                # Create index (PostgreSQL specific syntax)
                db.execute(text("""
                    CREATE INDEX IF NOT EXISTS ix_merchants_short_code ON merchants(short_code)
                """))

                db.commit()
                logger.info("Successfully added short_code and region_code columns to merchants table")
            else:
                logger.debug("Merchant schema check passed: short_code column exists")

        except Exception as e:
            db.rollback()
            logger.warning(f"Schema migration for merchants failed (non-critical): {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Could not check merchant schema (non-critical): {e}")


def ensure_verified_visits_table():
    """Ensure verified_visits table exists.

    This handles cases where Alembic migrations are out of sync with the database.
    """
    try:
        from app.db import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            # Check if table exists
            result = db.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'verified_visits'
                )
            """))
            table_exists = result.scalar()

            if not table_exists:
                logger.info("Creating verified_visits table...")

                # Note: Removed FK constraints on exclusive_session_id and charger_id
                # due to UUID/VARCHAR type mismatch issues
                db.execute(text("""
                    CREATE TABLE IF NOT EXISTS verified_visits (
                        id VARCHAR(36) PRIMARY KEY,
                        verification_code VARCHAR(32) UNIQUE NOT NULL,
                        region_code VARCHAR(8) NOT NULL,
                        merchant_code VARCHAR(16) NOT NULL,
                        visit_number INTEGER NOT NULL,
                        merchant_id VARCHAR NOT NULL,
                        driver_id INTEGER NOT NULL,
                        exclusive_session_id VARCHAR(36),
                        charger_id VARCHAR,
                        verified_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                        redeemed_at TIMESTAMP WITH TIME ZONE,
                        order_reference VARCHAR(128),
                        redemption_notes VARCHAR(512),
                        verification_lat FLOAT,
                        verification_lng FLOAT,
                        visit_date TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                    )
                """))

                # Create indexes
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_verified_visits_verification_code ON verified_visits(verification_code)"))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_verified_visits_merchant_id ON verified_visits(merchant_id)"))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_verified_visits_driver_id ON verified_visits(driver_id)"))

                db.commit()
                logger.info("Successfully created verified_visits table")
            else:
                logger.debug("Schema check passed: verified_visits table exists")

        except Exception as e:
            db.rollback()
            logger.warning(f"Schema migration for verified_visits failed (non-critical): {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Could not check verified_visits schema (non-critical): {e}")


def check_schema_payload_hash():
    """Check if payload_hash column exists in nova_transactions (local dev only)."""
    if not is_local_env():
        return  # Skip check in non-local environments
    
    try:
        from app.db import SessionLocal
        from sqlalchemy import text
        
        db = SessionLocal()
        try:
            # Try to query payload_hash column
            db.execute(text("SELECT payload_hash FROM nova_transactions LIMIT 1"))
            logger.info("Schema check passed: payload_hash column exists")
        except Exception as e:
            if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                logger.error("=" * 80)
                logger.error("DATABASE SCHEMA IS OUT OF DATE")
                logger.error("=" * 80)
                logger.error("The payload_hash column is missing from nova_transactions table.")
                logger.error("")
                logger.error("To fix, run:")
                logger.error("  cd nerava-backend-v9")
                logger.error("  alembic upgrade head")
                logger.error("")
                logger.error("=" * 80)
            else:
                # Other error (table might not exist, etc.) - just log, don't fail startup
                logger.debug(f"Schema check skipped (table may not exist yet): {e}")
        finally:
            db.close()
    except Exception as e:
        # Don't fail startup if check fails
        logger.debug(f"Schema check failed (non-critical): {e}")

