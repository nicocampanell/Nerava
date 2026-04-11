"""
Models Aggregator - Single Canonical Source for All Models

This module imports all model modules to ensure Base.metadata contains all tables.
Both Alembic (env.py) and the application should import from this module to ensure
models are registered exactly once.

DO NOT import models directly from individual model modules in Alembic env.py.
Always import this module instead.
"""
# Import Base first (defines the metadata instance)
from app.db import Base  # noqa: F401

# Import all model modules to register them with Base.metadata
# Import order matters for foreign key relationships, but SQLAlchemy handles this
from app.models import *  # noqa: F401, F403  # Core models: User, UserPreferences, etc.
from app.models_domain import *  # noqa: F401, F403  # Zone, EnergyEvent, DomainMerchant, DriverWallet, NovaTransaction, DomainChargingSession, StripePayment
from app.models_vehicle import *  # noqa: F401, F403  # VehicleAccount, VehicleToken, VehicleTelemetry
from app.models_while_you_charge import *  # noqa: F401, F403  # Charger, Merchant, MerchantPerk, etc.

# Optional: Import other model modules if they exist
try:
    from app.models_extra import *  # noqa: F401, F403  # Legacy/extended models (but NOT EnergyEvent - that's in models_domain)
except ImportError:
    pass

try:
    from app.models_demo import *  # noqa: F401, F403  # Demo models if they exist
except ImportError:
    pass

# Export Base for Alembic to use
__all__ = ["Base"]

