"""
Application lifespan management for startup and shutdown events
"""
import logging
import os
import re
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.analytics.batch_writer import analytics_batch_writer
from app.config import settings
from app.services.async_wallet import async_wallet
from app.services.cache import cache
from app.subscribers.wallet_credit import *  # Import to register subscribers
from app.workers.outbox_relay import outbox_relay
from app.workers.prewarm import cache_prewarmer
from app.workers.scheduled_polls import scheduled_poll_worker
from app.workers.weekly_merchant_report import weekly_merchant_report_worker

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    """Manage application lifespan events"""
    # Startup
    logger.info("Starting Nerava Backend v9...")
    print("[STARTUP] Beginning application startup sequence...", flush=True)
    
    try:
        # Compute environment variables FIRST (before any code that uses them)
        print("[STARTUP] Computing environment configuration...", flush=True)
        env = os.getenv("ENV", "dev").lower()
        is_local = env in {"local", "dev"}
        print(f"[STARTUP] ENV={env}, is_local={is_local}", flush=True)
        
        # P1-G: Prevent SQLite in production
        print("[STARTUP] Validating database configuration...", flush=True)
        database_url = os.getenv("DATABASE_URL", settings.database_url)
        if not is_local and re.match(r'^sqlite:', database_url, re.IGNORECASE):
            error_msg = (
                f"CRITICAL: SQLite database is not supported in production. "
                f"DATABASE_URL={database_url[:50]}..., ENV={env}. "
                f"Please use PostgreSQL (e.g., RDS, managed Postgres)."
            )
            print(f"[STARTUP] Refusing to start in {env} with SQLite database_url=sqlite:///... Use PostgreSQL instead.", flush=True)
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        print("[STARTUP] Database configuration validated", flush=True)
        
        # Initialize async wallet processor
        print("[STARTUP] Starting async wallet processor...", flush=True)
        await async_wallet.start_worker()
        logger.info("Async wallet processor started")
        print("[STARTUP] Async wallet processor started", flush=True)
        
        # Start outbox relay
        print("[STARTUP] Starting outbox relay...", flush=True)
        await outbox_relay.start()
        logger.info("Outbox relay started")
        print("[STARTUP] Outbox relay started", flush=True)
        
        # Start cache prewarmer
        print("[STARTUP] Starting cache prewarmer...", flush=True)
        await cache_prewarmer.start()
        logger.info("Cache prewarmer started")
        print("[STARTUP] Cache prewarmer started", flush=True)
        
        # Start analytics batch writer
        print("[STARTUP] Starting analytics batch writer...", flush=True)
        await analytics_batch_writer.start()
        logger.info("Analytics batch writer started")
        print("[STARTUP] Analytics batch writer started", flush=True)

        # Start scheduled poll worker (smart polling verification)
        print("[STARTUP] Starting scheduled poll worker...", flush=True)
        await scheduled_poll_worker.start()
        logger.info("Scheduled poll worker started")
        print("[STARTUP] Scheduled poll worker started", flush=True)

        # Start weekly merchant report worker
        print("[STARTUP] Starting weekly merchant report worker...", flush=True)
        await weekly_merchant_report_worker.start()
        logger.info("Weekly merchant report worker started")
        print("[STARTUP] Weekly merchant report worker started", flush=True)

        # One-time exclusive title fix (safe to remove after first deploy)
        try:
            from sqlalchemy import func

            from app.db import SessionLocal
            from app.models.while_you_charge import ChargerMerchant
            from app.models.while_you_charge import Merchant as WYCMerchant
            _db = SessionLocal()
            _fixes = {"heights pizzeria": "Free Garlic Knots"}
            for _pattern, _title in _fixes.items():
                _mids = [m.id for m in _db.query(WYCMerchant).filter(func.lower(WYCMerchant.name).contains(_pattern)).all()]
                if _mids:
                    _count = _db.query(ChargerMerchant).filter(ChargerMerchant.merchant_id.in_(_mids)).update({ChargerMerchant.exclusive_title: _title}, synchronize_session=False)
                    print(f"[STARTUP] Updated {_count} links for '{_pattern}' → '{_title}'", flush=True)
            _db.commit()
            _db.close()
        except Exception as e:
            print(f"[STARTUP] Exclusive fix skipped: {e}", flush=True)

        # Test cache connection
        print("[STARTUP] Verifying cache connection...", flush=True)
        try:
            await cache.get("health_check")
            logger.info("Cache connection verified")
            print("[STARTUP] Cache connection verified", flush=True)
        except Exception as e:
            if is_local:
                logger.warning(f"Cache connection failed in local/dev environment: {e}")
                print(f"[STARTUP] WARNING: Cache connection failed in {env}, continuing anyway", flush=True)
            else:
                logger.error(f"Cache connection failed in production: {e}")
                print(f"[STARTUP] ERROR: Cache connection failed in {env}, failing startup", flush=True)
                raise
        
        # Test database connection
        print("[STARTUP] Verifying database connection...", flush=True)
        from app.db import get_engine
        engine = get_engine()
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection verified")
            print("[STARTUP] Database connection verified", flush=True)
        except Exception as e:
            if is_local:
                logger.warning(f"Database connection failed in local/dev environment: {e}")
                print(f"[STARTUP] WARNING: Database connection failed in {env}, continuing anyway", flush=True)
            else:
                logger.error(f"Database connection failed in production: {e}")
                print(f"[STARTUP] ERROR: Database connection failed in {env}, failing startup", flush=True)
                raise
        
        # Validate required secrets in production (P0-1: secrets hardening)
        print("[STARTUP] Validating environment security settings...", flush=True)
        
        # P0-C: Prevent dev flags in non-local environments
        if not is_local:
            if os.getenv("NERAVA_DEV_ALLOW_ANON_USER", "false").lower() == "true":
                error_msg = (
                    "CRITICAL: NERAVA_DEV_ALLOW_ANON_USER cannot be enabled in non-local environment. "
                    f"ENV={env}. This is a security risk."
                )
                print(f"[Startup] Dev flag violation in {env}: NERAVA_DEV_ALLOW_ANON_USER is enabled (security risk)", flush=True)
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            if os.getenv("NERAVA_DEV_ALLOW_ANON_DRIVER", "false").lower() == "true":
                error_msg = (
                    "CRITICAL: NERAVA_DEV_ALLOW_ANON_DRIVER cannot be enabled in non-local environment. "
                    f"ENV={env}. This is a security risk."
                )
                print(f"[Startup] Dev flag violation in {env}: NERAVA_DEV_ALLOW_ANON_DRIVER is enabled (security risk)", flush=True)
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        
        if not is_local and env == "prod":
            # Production: validate required secrets are present
            missing_secrets = []
            
            # Required secrets for production
            if not os.getenv("JWT_SECRET") or os.getenv("JWT_SECRET") == "dev-secret":
                missing_secrets.append("JWT_SECRET (must be a secure random value)")
            
            if not os.getenv("TOKEN_ENCRYPTION_KEY"):
                missing_secrets.append("TOKEN_ENCRYPTION_KEY (required for secure token storage)")
            
            if not os.getenv("STRIPE_WEBHOOK_SECRET"):
                missing_secrets.append("STRIPE_WEBHOOK_SECRET (required for webhook verification)")
            
            if missing_secrets:
                # Extract just the env var names for the print statement (security: don't print full descriptions)
                missing_names = [s.split(" ")[0] for s in missing_secrets]
                error_msg = (
                    f"CRITICAL: Missing required secrets in production environment. "
                    f"Missing: {', '.join(missing_secrets)}. "
                    f"Set these environment variables before starting the application."
                )
                print(f"[Startup] Missing required env vars in prod: {', '.join(missing_names)}", flush=True)
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            logger.info("Production secrets validation passed")
        
        # Check for missing migrations (local-only, non-blocking)
        if is_local:
            print("[STARTUP] Checking database migrations (local only)...", flush=True)
            try:
                with engine.connect() as conn:
                    # Lightweight schema check: try to query encryption_version column
                    conn.execute(text("SELECT encryption_version FROM vehicle_tokens LIMIT 1"))
                logger.debug("Migration schema check passed")
                print("[STARTUP] Migration schema check passed", flush=True)
            except Exception as e:
                error_msg = str(e).lower()
                if "no such column" in error_msg or "encryption_version" in error_msg:
                    logger.warning(
                        "⚠️ Local database schema is behind. Run: cd nerava-backend-v9 && alembic upgrade head"
                    )
                    print("[STARTUP] WARNING: Database schema may be behind migrations", flush=True)
                else:
                    # Other errors (table doesn't exist, etc.) are fine - just log debug
                    logger.debug(f"Migration check skipped (expected in some setups): {e}")
        
        logger.info("Application startup completed successfully")
        print("[STARTUP] Application startup completed successfully", flush=True)
        
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down Nerava Backend v9...")
    
    try:
        # Stop weekly merchant report worker
        await weekly_merchant_report_worker.stop()
        logger.info("Weekly merchant report worker stopped")

        # Stop scheduled poll worker
        await scheduled_poll_worker.stop()
        logger.info("Scheduled poll worker stopped")

        # Stop analytics batch writer
        await analytics_batch_writer.stop()
        logger.info("Analytics batch writer stopped")
        
        # Stop cache prewarmer
        await cache_prewarmer.stop()
        logger.info("Cache prewarmer stopped")
        
        # Stop outbox relay
        await outbox_relay.stop()
        logger.info("Outbox relay stopped")
        
        # Stop async wallet processor
        await async_wallet.stop_worker()
        logger.info("Async wallet processor stopped")
        
        # Close database connections
        from app.db import get_engine
        engine = get_engine()
        engine.dispose()
        logger.info("Database connections closed")
        
        logger.info("Application shutdown completed successfully")
        
    except Exception as e:
        logger.error(f"Shutdown error: {e}")

# Export for use in main.py
__all__ = ['lifespan']
