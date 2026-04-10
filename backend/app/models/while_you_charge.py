"""
Models for "While You Charge" feature
- Chargers (EV charging stations)
- Merchants (places near chargers)
- ChargerMerchants (junction table with walk times)
- MerchantPerks (active rewards/offers)
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON


class Charger(Base):
    """EV charging station"""
    __tablename__ = "chargers"
    
    id = Column(String, primary_key=True)  # e.g., "ch_123" or external ID
    external_id = Column(String, unique=True, index=True, nullable=True)  # NREL/OCM ID
    name = Column(String, nullable=False)
    network_name = Column(String, nullable=True)  # "Tesla", "ChargePoint", etc.
    lat = Column(Float, nullable=False, index=True)
    lng = Column(Float, nullable=False, index=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True, index=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    
    # Charger details
    connector_types = Column(JSON, default=list)  # ["CCS", "CHAdeMO", "Tesla"]
    power_kw = Column(Float, nullable=True)
    num_evse = Column(Integer, nullable=True)  # Number of EVSE stalls/plugs
    is_public = Column(Boolean, default=True, nullable=False)
    access_code = Column(String, nullable=True)
    
    # Pricing
    pricing_per_kwh = Column(Float, nullable=True)  # e.g. 0.43
    pricing_source = Column(String(50), nullable=True)  # 'network_average', 'user_reported', 'api'

    # Nerava Score (reliability rating 0-100)
    nerava_score = Column(Float, nullable=True)

    # Status
    status = Column(String, default="available", nullable=False)  # available, in_use, broken, unknown
    last_verified_at = Column(DateTime, nullable=True)
    
    # Metadata
    logo_url = Column(String, nullable=True)  # Network logo URL
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    merchants = relationship("ChargerMerchant", back_populates="charger", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_chargers_location', 'lat', 'lng'),
    )


class Merchant(Base):
    """Merchant/place near chargers"""
    __tablename__ = "merchants"
    
    id = Column(String, primary_key=True)  # e.g., "m_1"
    external_id = Column(String, unique=True, index=True, nullable=True)  # Google Places ID
    name = Column(String, nullable=False, index=True)
    category = Column(String, nullable=True, index=True)  # "coffee", "restaurant", "grocery_or_supermarket", "gym"
    
    lat = Column(Float, nullable=False, index=True)
    lng = Column(Float, nullable=False, index=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True, index=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    
    # Merchant details
    logo_url = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    price_level = Column(Integer, nullable=True)  # 1-4 (Google Places)
    phone = Column(String, nullable=True)
    website = Column(String, nullable=True)
    description = Column(String, nullable=True)  # Business description from Google Places
    
    # Google Places integration
    place_id = Column(String, unique=True, index=True, nullable=True)  # Google Places ID
    primary_photo_url = Column(String, nullable=True)  # Main photo from Google
    photo_urls = Column(JSON, default=list)  # Array of photo URLs
    user_rating_count = Column(Integer, nullable=True)  # Review count
    business_status = Column(String, nullable=True)  # OPERATIONAL, CLOSED_PERMANENTLY, etc.
    open_now = Column(Boolean, nullable=True)  # Current open/closed status
    hours_json = Column(JSON, nullable=True)  # Opening hours structure
    hours_text = Column(String, nullable=True)  # Simple hours text (e.g., "11 AM–11 PM")
    google_places_updated_at = Column(DateTime, nullable=True)  # Last sync timestamp
    last_status_check = Column(DateTime, nullable=True)  # Last open/closed check
    
    # Google Places types (array)
    place_types = Column(JSON, default=list)

    # Verification code components (for visit logging)
    short_code = Column(String(16), unique=True, nullable=True, index=True)  # e.g., "ASADAS"
    region_code = Column(String(8), nullable=True, default="ATX")  # e.g., "ATX" for Austin

    # EV Arrival: ordering info
    ordering_url = Column(String(500), nullable=True)  # Deep link or web URL
    ordering_app_scheme = Column(String(100), nullable=True)  # e.g., "toastapp://"
    ordering_instructions = Column(Text, nullable=True)  # "Order at counter"

    # Corporate chain flag (e.g., Starbucks, McDonald's) — still shown in app, may not have incentives
    is_corporate = Column(Boolean, default=False, nullable=False, server_default="false", index=True)

    # Category and charger proximity (cached for filtering)
    primary_category = Column(String(32), nullable=True, index=True)  # "coffee", "food", or "other"
    nearest_charger_id = Column(String(64), nullable=True)  # FK to charger
    nearest_charger_distance_m = Column(Integer, nullable=True, index=True)  # Cached distance in meters
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    charger_links = relationship("ChargerMerchant", back_populates="merchant", cascade="all, delete-orphan")
    perks = relationship("MerchantPerk", back_populates="merchant", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_merchants_location', 'lat', 'lng'),
    )


class ChargerMerchant(Base):
    """Junction table: which merchants are near which chargers, with walk times"""
    __tablename__ = "charger_merchants"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    charger_id = Column(String, ForeignKey("chargers.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Distance and travel time
    distance_m = Column(Float, nullable=False)  # Straight-line distance in meters
    walk_duration_s = Column(Integer, nullable=False)  # Walking time in seconds (from Distance Matrix)
    walk_distance_m = Column(Float, nullable=True)  # Actual walking distance (may differ from straight-line)
    
    # Primary merchant override
    is_primary = Column(Boolean, default=False, nullable=False, index=True)  # Primary merchant flag
    override_mode = Column(String, nullable=True)  # 'PRE_CHARGE_ONLY' or 'ALWAYS'
    suppress_others = Column(Boolean, default=False, nullable=False)  # Hide other merchants when primary exists
    exclusive_title = Column(String, nullable=True)  # e.g., "Free Margarita"
    exclusive_description = Column(Text, nullable=True)  # Full exclusive offer text
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    charger = relationship("Charger", back_populates="merchants")
    merchant = relationship("Merchant", back_populates="charger_links")
    
    __table_args__ = (
        Index('idx_charger_merchant_unique', 'charger_id', 'merchant_id', unique=True),
        Index('idx_charger_merchant_primary', 'charger_id', 'is_primary'),
    )


class MerchantPerk(Base):
    """Active perks/rewards for merchants"""
    __tablename__ = "merchant_perks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Perk details
    title = Column(String, nullable=False)  # "Earn 12 Nova"
    description = Column(Text, nullable=True)
    nova_reward = Column(Integer, nullable=False)  # Nova amount (cents or points)
    
    # Time window (optional - if null, always active)
    window_start = Column(String, nullable=True)  # "14:00" (HH:MM format)
    window_end = Column(String, nullable=True)  # "18:00"
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    
    # Relationships
    merchant = relationship("Merchant", back_populates="perks")


class MerchantBalance(Base):
    """Merchant balance tracking for discount budgets"""
    __tablename__ = "merchant_balance"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    balance_cents = Column(Integer, nullable=False, default=0)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    merchant = relationship("Merchant")


class MerchantBalanceLedger(Base):
    """Ledger of all balance transactions (credits/debits)"""
    __tablename__ = "merchant_balance_ledger"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    delta_cents = Column(Integer, nullable=False)  # Can be negative for debits
    reason = Column(String, nullable=False)  # Description of the transaction
    session_id = Column(String, nullable=True, index=True)  # Optional session reference
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index('idx_merchant_balance_ledger_merchant_created', 'merchant_id', 'created_at'),
    )


class MerchantOfferCode(Base):
    """Redemption codes for merchant discounts"""
    __tablename__ = "merchant_offer_codes"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # Unique redemption code (e.g., "DOM-SB-4821")
    amount_cents = Column(Integer, nullable=False)  # Discount amount in cents
    is_redeemed = Column(Boolean, default=False, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    merchant = relationship("Merchant")
    
    __table_args__ = (
        Index('idx_merchant_offer_codes_merchant_created', 'merchant_id', 'created_at'),
        Index('idx_merchant_offer_codes_code_redeemed', 'code', 'is_redeemed'),
    )


class ChargerCluster(Base):
    """Charger cluster for party events (e.g., asadas_party)"""
    __tablename__ = "charger_clusters"
    
    id = Column(UUIDType(), primary_key=True)
    name = Column(String, unique=True, nullable=False, index=True)  # "asadas_party"
    charger_lat = Column(Float, nullable=False)
    charger_lng = Column(Float, nullable=False)
    charger_radius_m = Column(Integer, nullable=False)  # 400
    merchant_radius_m = Column(Integer, nullable=False)  # 40
    
    # Charger ID (optional, links to Charger table if exists)
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    charger = relationship("Charger", foreign_keys=[charger_id])
    
    __table_args__ = (
        Index('idx_charger_clusters_location', 'charger_lat', 'charger_lng'),
    )


class FavoriteMerchant(Base):
    """User's favorite merchants"""
    __tablename__ = "favorite_merchants"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_favorite_merchant_unique', 'user_id', 'merchant_id', unique=True),
        Index('idx_favorite_merchant_user', 'user_id'),
        Index('idx_favorite_merchant_merchant', 'merchant_id'),
    )


class AmenityVote(Base):
    """User votes for merchant amenities (bathroom, wifi)"""
    __tablename__ = "amenity_votes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    amenity = Column(String(20), nullable=False)  # 'bathroom' or 'wifi'
    vote_type = Column(String(10), nullable=False)  # 'up' or 'down'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships (optional)
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        UniqueConstraint('merchant_id', 'user_id', 'amenity', name='uq_amenity_vote'),
        Index('idx_amenity_votes_merchant', 'merchant_id', 'amenity'),
        Index('idx_amenity_votes_user', 'user_id'),
    )

