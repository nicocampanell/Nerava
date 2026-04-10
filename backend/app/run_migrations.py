"""
Run Alembic migrations programmatically.

This module can be called before uvicorn starts to ensure the database schema
is up to date. Safe to call multiple times - Alembic will be a no-op if already at head.
Also seeds Domain hub chargers after migrations to ensure they exist for merchant fetching.
"""
import logging
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """
    Run Alembic migrations up to head using the current DATABASE_URL.
    This should be safe to call on startup; Alembic will be a no-op if already at head.
    """
    # Resolve alembic.ini relative to project root
    # This file is at: nerava-backend-v9/app/run_migrations.py
    # alembic.ini is at: nerava-backend-v9/alembic.ini
    # So we need to go up 2 levels from this file
    project_root = Path(__file__).resolve().parents[1]
    alembic_ini = project_root / "alembic.ini"

    if not alembic_ini.exists():
        logger.error(f"Alembic config not found at {alembic_ini}")
        raise FileNotFoundError(f"Alembic config not found at {alembic_ini}")

    cfg = Config(str(alembic_ini))
    
    # Get DATABASE_URL from environment (matches how the app uses it)
    database_url = os.getenv("DATABASE_URL", "sqlite:///./nerava.db")
    
    # Force URL from runtime environment (overrides alembic.ini default)
    cfg.set_main_option("sqlalchemy.url", database_url)

    logger.info(f"Running Alembic migrations to head on {database_url.split('@')[-1] if '@' in database_url else database_url}")
    try:
        command.upgrade(cfg, "head")
        logger.info("Alembic migrations complete.")
        
        # Seed Domain hub chargers after migrations (merchants will be auto-fetched when API is called)
        _seed_domain_chargers(database_url)
        
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        raise


def _seed_domain_chargers(database_url: str) -> None:
    """
    Seed Domain hub chargers after migrations.
    This ensures chargers exist so merchants can be linked to them.
    """
    try:
        # Import here to avoid circular dependencies
        from app.db import SessionLocal
        from app.domains.domain_hub import DOMAIN_CHARGERS
        from app.models_while_you_charge import Charger
        
        logger.info("Seeding Domain hub chargers...")
        db = SessionLocal()
        
        try:
            chargers_inserted = 0
            chargers_updated = 0
            
            for charger_config in DOMAIN_CHARGERS:
                existing = db.query(Charger).filter(Charger.id == charger_config["id"]).first()
                
                if existing:
                    # Update existing charger
                    existing.name = charger_config["name"]
                    existing.network_name = charger_config["network_name"]
                    existing.lat = charger_config["lat"]
                    existing.lng = charger_config["lng"]
                    existing.address = charger_config.get("address")
                    existing.city = charger_config.get("city", "Austin")
                    existing.state = charger_config.get("state", "TX")
                    existing.zip_code = charger_config.get("zip_code")
                    existing.connector_types = charger_config.get("connector_types", [])
                    existing.power_kw = charger_config.get("power_kw")
                    existing.is_public = charger_config.get("is_public", True)
                    existing.status = charger_config.get("status", "available")
                    existing.external_id = charger_config.get("external_id")
                    chargers_updated += 1
                else:
                    # Insert new charger
                    charger = Charger(
                        id=charger_config["id"],
                        external_id=charger_config.get("external_id"),
                        name=charger_config["name"],
                        network_name=charger_config["network_name"],
                        lat=charger_config["lat"],
                        lng=charger_config["lng"],
                        address=charger_config.get("address"),
                        city=charger_config.get("city", "Austin"),
                        state=charger_config.get("state", "TX"),
                        zip_code=charger_config.get("zip_code"),
                        connector_types=charger_config.get("connector_types", []),
                        power_kw=charger_config.get("power_kw"),
                        is_public=charger_config.get("is_public", True),
                        status=charger_config.get("status", "available"),
                    )
                    db.add(charger)
                    chargers_inserted += 1
            
            db.commit()
            logger.info(f"Domain hub chargers seeded: {chargers_inserted} inserted, {chargers_updated} updated")
            
        except Exception as seed_err:
            db.rollback()
            logger.warning(f"Failed to seed Domain hub chargers (non-fatal): {seed_err}")
            # Don't fail migrations if seeding fails - chargers can be seeded manually later
        finally:
            db.close()
            
    except Exception as e:
        logger.warning(f"Failed to seed Domain hub chargers (non-fatal): {e}")
        # Don't fail migrations if seeding fails


if __name__ == "__main__":
    # Set up basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s"
    )
    try:
        run_migrations()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to run migrations: {e}", exc_info=True)
        sys.exit(1)

