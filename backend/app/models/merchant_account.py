"""
Models for Merchant Onboarding and Placement Control
- MerchantAccount: Merchant owner account
- MerchantLocationClaim: Merchant claims a Google Place location
- MerchantPlacementRule: Placement rules (boost/cap/perks) per location
- MerchantPaymentMethod: Card-on-file via Stripe SetupIntent
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
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON  # for non-sqlite engines
except Exception:
    from sqlalchemy.dialects.sqlite import JSON as JSON  # fallback for sqlite


class MerchantAccount(Base):
    """Merchant account (one per merchant owner)"""
    __tablename__ = "merchant_accounts"
    
    id = Column(UUIDType(), primary_key=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    owner = relationship("User", foreign_keys=[owner_user_id])
    location_claims = relationship("MerchantLocationClaim", back_populates="merchant_account", cascade="all, delete-orphan")
    payment_methods = relationship("MerchantPaymentMethod", back_populates="merchant_account", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_merchant_accounts_owner', 'owner_user_id'),
    )


class MerchantLocationClaim(Base):
    """Merchant claims a Google Place location"""
    __tablename__ = "merchant_location_claims"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_account_id = Column(UUIDType(), ForeignKey("merchant_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    place_id = Column(String, nullable=False, index=True)  # Google Places place_id
    status = Column(String, nullable=False, default="CLAIMED", index=True)  # CLAIMED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    merchant_account = relationship("MerchantAccount", back_populates="location_claims")
    
    __table_args__ = (
        UniqueConstraint('merchant_account_id', 'place_id', name='uq_merchant_location_claim'),
        Index('idx_merchant_location_claims_account', 'merchant_account_id'),
        Index('idx_merchant_location_claims_place', 'place_id'),
    )


class MerchantPlacementRule(Base):
    """Placement rules (boost/cap/perks) per location"""
    __tablename__ = "merchant_placement_rules"
    
    id = Column(UUIDType(), primary_key=True)
    place_id = Column(String, nullable=False, unique=True, index=True)  # Google Places place_id
    status = Column(String, nullable=False, default="ACTIVE", index=True)  # ACTIVE, PAUSED
    daily_cap_cents = Column(Integer, nullable=False, default=0)  # Daily spending cap in cents
    boost_weight = Column(Float, nullable=False, default=0.0)  # Additive boost to ranking score
    perks_enabled = Column(Boolean, nullable=False, default=False)  # Whether perks are enabled
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True)
    
    __table_args__ = (
        Index('idx_merchant_placement_rules_status', 'status', 'updated_at'),
        Index('idx_merchant_placement_rules_place', 'place_id'),
    )


class MerchantPaymentMethod(Base):
    """Card-on-file payment method via Stripe SetupIntent"""
    __tablename__ = "merchant_payment_methods"
    
    id = Column(UUIDType(), primary_key=True)
    merchant_account_id = Column(UUIDType(), ForeignKey("merchant_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    stripe_customer_id = Column(String, nullable=False, index=True)  # Stripe Customer ID
    stripe_payment_method_id = Column(String, nullable=False)  # Stripe PaymentMethod ID (after SetupIntent confirmation)
    status = Column(String, nullable=False, default="ACTIVE", index=True)  # ACTIVE, INACTIVE
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    merchant_account = relationship("MerchantAccount", back_populates="payment_methods")
    
    __table_args__ = (
        Index('idx_merchant_payment_methods_account', 'merchant_account_id'),
        Index('idx_merchant_payment_methods_customer', 'stripe_customer_id'),
    )



