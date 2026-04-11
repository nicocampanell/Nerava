"""
Domain Charge Party MVP Models

These models are separate from the existing "While You Charge" models
to support the Domain-specific charge party event system with merchants,
drivers, Nova transactions, and Stripe integration.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON  # for non-sqlite engines
except Exception:
    JSON = SQLITE_JSON  # fallback for sqlite


class Zone(Base):
    """Geographic zone (e.g., domain_austin, south_lamar_austin)"""
    __tablename__ = "zones"
    
    slug = Column(String, primary_key=True)  # e.g., "domain_austin"
    name = Column(String, nullable=False)  # e.g., "The Domain, Austin"
    
    # Geographic bounds (for validation)
    center_lat = Column(Float, nullable=False)
    center_lng = Column(Float, nullable=False)
    radius_m = Column(Integer, nullable=False, default=1000)  # meters
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)


class EnergyEvent(Base):
    """Charge party event (e.g., domain_jan_2025)"""
    __tablename__ = "energy_events"
    
    id = Column(UUIDType(), primary_key=True)
    slug = Column(String, unique=True, nullable=False, index=True)  # e.g., "domain_jan_2025"
    zone_slug = Column(String, ForeignKey("zones.slug"), nullable=False, index=True)
    name = Column(String, nullable=False)  # e.g., "Domain Charge Party - January 2025"
    
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=True)  # None for ongoing events
    status = Column(String, nullable=False, default="draft", index=True)  # draft, active, closed
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    
    # Relationships
    zone = relationship("Zone", foreign_keys=[zone_slug])
    charging_sessions = relationship("DomainChargingSession", back_populates="energy_event")
    
    __table_args__ = (
        Index('ix_energy_events_zone_status', 'zone_slug', 'status'),
    )


class DomainMerchant(Base):
    """Domain Charge Party merchant - separate from While You Charge merchants"""
    __tablename__ = "domain_merchants"
    
    id = Column(UUIDType(), primary_key=True)
    name = Column(String, nullable=False)
    google_place_id = Column(String, nullable=True)
    
    # Address
    addr_line1 = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)
    country = Column(String, nullable=True, default="US")
    
    # Location
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    
    # Profile fields (editable by merchant)
    description = Column(Text, nullable=True)
    website = Column(String(512), nullable=True)
    hours_text = Column(String(512), nullable=True)
    photo_url = Column(String(512), nullable=True)

    # Contact
    public_phone = Column(String, nullable=True)
    
    # Ownership
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    
    # Status
    status = Column(String, nullable=False, default="pending")  # pending, active, flagged, suspended
    
    # Nova balance (in smallest unit, e.g., cents or points)
    nova_balance = Column(Integer, nullable=False, default=0)
    
    # Zone (data-scoped, not path-scoped)
    zone_slug = Column(String, nullable=False, index=True)  # e.g., "domain_austin" (no FK for flexibility)
    
    # Square sync fields (for national merchant onboarding)
    square_merchant_id = Column(String, nullable=True, index=True)
    square_location_id = Column(String, nullable=True)
    square_access_token = Column(Text, nullable=True)  # Encrypted at rest via token_encryption service
    square_connected_at = Column(DateTime, nullable=True)
    
    # Perk configuration (based on AOV)
    avg_order_value_cents = Column(Integer, nullable=True)
    recommended_perk_cents = Column(Integer, nullable=True)
    custom_perk_cents = Column(Integer, nullable=True)
    perk_label = Column(String, nullable=True)  # e.g., "$3 off any order"
    
    # Billing
    stripe_customer_id = Column(String, nullable=True, index=True)
    billing_type = Column(String, nullable=False, default="free")  # free, pay_as_you_go, campaign
    card_last4 = Column(String(4), nullable=True)
    card_brand = Column(String(20), nullable=True)

    # QR fields (for national checkout)
    qr_token = Column(String, unique=True, nullable=True, index=True)
    qr_created_at = Column(DateTime, nullable=True)
    qr_last_used_at = Column(DateTime, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    
    # Relationships
    owner = relationship("User", foreign_keys=[owner_user_id])
    transactions = relationship("NovaTransaction", back_populates="merchant")
    stripe_payments = relationship("StripePayment", back_populates="merchant")
    redemptions = relationship("MerchantRedemption", back_populates="merchant")
    
    __table_args__ = (
        Index('ix_domain_merchants_zone_status', 'zone_slug', 'status'),
        Index('ix_domain_merchants_location', 'lat', 'lng'),
    )


# DriverWallet is defined in driver_wallet.py (matching production schema from migration 073)
# Re-exported here for backward compatibility with existing imports
from .driver_wallet import DriverWallet  # noqa: F401


class ApplePassRegistration(Base):
    """Apple Wallet Pass registration per device for a driver wallet."""
    __tablename__ = "apple_pass_registrations"

    id = Column(UUIDType(), primary_key=True)
    driver_wallet_id = Column(Integer, ForeignKey("driver_wallets.driver_id"), nullable=False, index=True)

    # Device + PassKit identifiers
    device_library_identifier = Column(String, nullable=False, index=True)
    push_token = Column(String, nullable=True)
    pass_type_identifier = Column(String, nullable=False)

    # Serial number used by PassKit web service (backed by wallet_pass_token, no PII)
    serial_number = Column(String, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    # Relationships
    driver_wallet = relationship("DriverWallet", foreign_keys=[driver_wallet_id])

    __table_args__ = (
        Index("ix_apple_pass_registrations_driver_serial", "driver_wallet_id", "serial_number"),
    )


class GoogleWalletLink(Base):
    """Google Wallet link for a driver wallet."""
    __tablename__ = "google_wallet_links"

    id = Column(UUIDType(), primary_key=True)
    driver_wallet_id = Column(Integer, ForeignKey("driver_wallets.driver_id"), nullable=False, index=True)

    issuer_id = Column(String, nullable=False)
    class_id = Column(String, nullable=False)
    object_id = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, index=True)  # e.g., active, revoked, error

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    driver_wallet = relationship("DriverWallet", foreign_keys=[driver_wallet_id])

    __table_args__ = (
        Index("ix_google_wallet_links_driver_state", "driver_wallet_id", "state"),
    )


class NovaTransaction(Base):
    """Nova transaction ledger - tracks all Nova movements"""
    __tablename__ = "nova_transactions"
    
    id = Column(UUIDType(), primary_key=True)
    type = Column(String, nullable=False)  # driver_earn, driver_redeem, merchant_topup, admin_grant
    
    # Parties involved
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=True, index=True)
    
    # Amount (always positive; type indicates direction)
    amount = Column(Integer, nullable=False)
    
    # References
    stripe_payment_id = Column(String, ForeignKey("stripe_payments.id"), nullable=True)
    session_id = Column(String, ForeignKey("domain_charging_sessions.id"), nullable=True)
    event_id = Column(String, ForeignKey("energy_events.id"), nullable=True, index=True)  # Optional event reference
    # NOTE: campaign_id not yet migrated to production — do NOT uncomment until migration applied
    # campaign_id = Column(String, nullable=True)

    # Metadata (Python attribute is 'transaction_meta' to avoid SQLAlchemy reserved word 'metadata')
    # Database column name remains 'metadata' for backward compatibility
    transaction_meta = Column("metadata", JSON, nullable=True)  # Flexible JSON for additional context
    
    # Idempotency key for deduplication (P0 race condition fix)
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    
    # Payload hash for conflict detection (same idempotency_key + different payload → 409)
    payload_hash = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    driver = relationship("User", foreign_keys=[driver_user_id])
    merchant = relationship("DomainMerchant", foreign_keys=[merchant_id])
    stripe_payment = relationship("StripePayment", foreign_keys=[stripe_payment_id])
    charging_session = relationship("DomainChargingSession", foreign_keys=[session_id])
    energy_event = relationship("EnergyEvent", foreign_keys=[event_id])
    
    __table_args__ = (
        Index('ix_nova_transactions_type_created', 'type', 'created_at'),
    )


class DomainChargingSession(Base):
    """Domain Charge Party charging session"""
    __tablename__ = "domain_charging_sessions"
    
    id = Column(UUIDType(), primary_key=True)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    charger_provider = Column(String, nullable=False, default="manual")  # tesla, manual, demo, etc.
    
    # Timing
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    
    # Charging details
    kwh_estimate = Column(Float, nullable=True)
    
    # Verification
    verified = Column(Boolean, nullable=False, default=False, index=True)
    verification_source = Column(String, nullable=True)  # tesla_api, manual_code, admin, demo
    
    # Event tracking (data-scoped, not path-scoped)
    event_id = Column(String, ForeignKey("energy_events.id"), nullable=True, index=True)  # Optional event reference
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    
    # Relationships
    driver = relationship("User", foreign_keys=[driver_user_id])
    transactions = relationship("NovaTransaction", back_populates="charging_session")
    energy_event = relationship("EnergyEvent", back_populates="charging_sessions")


class StripePayment(Base):
    """Stripe payment records for merchant Nova purchases"""
    __tablename__ = "stripe_payments"
    
    id = Column(UUIDType(), primary_key=True)
    stripe_session_id = Column(String, nullable=False, unique=True)
    stripe_payment_intent_id = Column(String, nullable=True, index=True)
    
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=True, index=True)
    
    amount_usd = Column(Integer, nullable=False)  # in cents
    nova_issued = Column(Integer, nullable=False)  # Nova amount
    
    status = Column(String, nullable=False, default="pending", index=True)  # pending, paid, failed
    
    stripe_event_id = Column(String, nullable=True, unique=True)  # for idempotency
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    
    # Relationships
    merchant = relationship("DomainMerchant", foreign_keys=[merchant_id])
    transactions = relationship("NovaTransaction", back_populates="stripe_payment")


class MerchantRedemption(Base):
    """Merchant redemption record - tracks Nova redemptions at merchants"""
    __tablename__ = "merchant_redemptions"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=False, index=True)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # QR token used (if any)
    qr_token = Column(String, nullable=True, index=True)
    
    # Reward ID (if redeemed from predefined reward)
    reward_id = Column(String, ForeignKey("merchant_rewards.id"), nullable=True, index=True)
    
    # Order details
    order_total_cents = Column(Integer, nullable=False)
    discount_cents = Column(Integer, nullable=False)
    nova_spent_cents = Column(Integer, nullable=False)  # Amount of Nova driver spent
    
    # Square order ID (for Square POS integration)
    square_order_id = Column(String, nullable=True)
    
    # P1-F Security: Idempotency key for non-Square redemptions to prevent replay attacks
    idempotency_key = Column(String, nullable=True, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationships
    merchant = relationship("DomainMerchant", foreign_keys=[merchant_id])
    driver = relationship("User", foreign_keys=[driver_user_id])
    reward = relationship("MerchantReward", foreign_keys=[reward_id])
    
    __table_args__ = (
        Index('ix_merchant_redemptions_merchant_created', 'merchant_id', 'created_at'),
        Index('ix_merchant_redemptions_driver_created', 'driver_user_id', 'created_at'),
        # Unique constraint on (merchant_id, square_order_id) - enforced at application level for nulls
        Index('ix_merchant_redemptions_merchant_square_order', 'merchant_id', 'square_order_id', unique=True),
        # P1-F: Unique constraint on (merchant_id, idempotency_key) - enforced via migration 041
        # Note: Allows NULL idempotency_key for backward compatibility, but enforces uniqueness for non-NULL
    )


class MerchantReward(Base):
    """Predefined merchant rewards (e.g., 300 Nova for Free Coffee)"""
    __tablename__ = "merchant_rewards"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=False, index=True)
    
    nova_amount = Column(Integer, nullable=False)  # e.g. 300
    title = Column(String, nullable=False)  # "Free Coffee"
    description = Column(String, nullable=True)
    
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    merchant = relationship("DomainMerchant", foreign_keys=[merchant_id])
    
    __table_args__ = (
        Index('ix_merchant_rewards_merchant_active', 'merchant_id', 'is_active'),
    )


class SquareOAuthState(Base):
    """OAuth state for Square OAuth flow (CSRF protection)"""
    __tablename__ = "square_oauth_states"
    
    id = Column(UUIDType(), primary_key=True)
    state = Column(String, unique=True, nullable=False, index=True)  # OAuth state parameter
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)  # Default: created_at + 15 minutes
    used = Column(Boolean, nullable=False, default=False, index=True)  # Mark as used after validation
    
    # Note: indexes are created by index=True on columns, no need for explicit Index() here


class MerchantFeeLedger(Base):
    """Merchant fee ledger - tracks Nova redemptions and fees per period"""
    __tablename__ = "merchant_fee_ledger"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=False, index=True)
    
    # Period (monthly)
    period_start = Column(Date, nullable=False, index=True)  # First day of month
    period_end = Column(Date, nullable=True)  # Last day of month
    
    # Totals
    nova_redeemed_cents = Column(Integer, nullable=False, default=0)
    fee_cents = Column(Integer, nullable=False, default=0)  # 15% of nova_redeemed_cents
    
    # Status
    status = Column(String, nullable=False, default="accruing")  # accruing, invoiced, paid
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    
    # Relationships
    merchant = relationship("DomainMerchant", foreign_keys=[merchant_id])
    
    __table_args__ = (
        # Note: single-column indexes created by index=True on columns
        # Unique composite constraint on (merchant_id, period_start)
        Index('uq_merchant_fee_ledger_merchant_period', 'merchant_id', 'period_start', unique=True),
    )


