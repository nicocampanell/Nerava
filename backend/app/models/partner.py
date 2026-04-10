"""
Partner & Partner API Key Models

Partner: An external integration partner (charging network, driver app, fleet platform, etc.)
PartnerAPIKey: API key for partner authentication. Key stored as SHA-256 hash.

Key format: nrv_pk_{32_hex_chars} — plaintext returned once on creation only.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON


class Partner(Base):
    """
    An external integration partner that can submit charging sessions
    via the Partner Incentive API.
    """
    __tablename__ = "partners"

    id = Column(UUIDType(), primary_key=True)
    name = Column(String(200), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    partner_type = Column(String(50), nullable=False)
    # Types: charging_network, driver_app, fleet_platform, oem_app, hardware_mfr, utility
    trust_tier = Column(Integer, nullable=False, default=3)
    # 1=hardware-verified, 2=api-verified, 3=app-reported
    status = Column(String(20), nullable=False, default="pending", index=True)
    # Statuses: pending, active, suspended, revoked

    contact_name = Column(String(200), nullable=True)
    contact_email = Column(String(200), nullable=True)

    webhook_url = Column(String(500), nullable=True)
    webhook_secret = Column(String(200), nullable=True)
    webhook_enabled = Column(Boolean, default=False, nullable=False)

    rate_limit_rpm = Column(Integer, default=60, nullable=False)
    default_verification_method = Column(String(50), default="partner_app_signal", nullable=False)
    quality_score_modifier = Column(Integer, default=0, nullable=False)
    # Tier 1: +20, Tier 2: +10, Tier 3: -10

    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PartnerAPIKey(Base):
    """
    API key for partner authentication.

    Keys are stored as SHA-256 hashes. The plaintext key is returned
    exactly once at creation time.
    """
    __tablename__ = "partner_api_keys"

    id = Column(UUIDType(), primary_key=True)
    partner_id = Column(UUIDType(), ForeignKey("partners.id"), nullable=False, index=True)
    key_prefix = Column(String(12), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=True)
    scopes = Column(JSON, nullable=False)
    # Scopes: sessions:write, sessions:read, grants:read, campaigns:read
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_partner_api_keys_partner", "partner_id", "is_active"),
    )
