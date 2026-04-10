"""
Exclusive Session Model
Tracks driver exclusive activation sessions for web-only flow
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON
import enum
import uuid

from ..core.uuid_type import UUIDType
from ..db import Base


class ExclusiveSessionStatus(str, enum.Enum):
    """Exclusive session status"""
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"


class ExclusiveSession(Base):
    """Tracks driver exclusive activation sessions"""
    __tablename__ = "exclusive_sessions"
    
    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Merchant identification (store both if available)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True, index=True)
    merchant_place_id = Column(String, nullable=True, index=True)  # Google Places ID
    
    # Charger identification (optional but recommended)
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)
    charger_place_id = Column(String, nullable=True)
    
    # Link to intent capture session
    intent_session_id = Column(UUIDType(), ForeignKey("intent_sessions.id"), nullable=True, index=True)

    # Link to charging session (for post-charge expiry)
    charging_session_id = Column(UUIDType(), ForeignKey("session_events.id"), nullable=True, index=True)

    # Verification code (generated at activation for QR code display)
    verification_code = Column(String(50), nullable=True)
    
    # Status
    status = Column(SQLEnum(ExclusiveSessionStatus), nullable=False, default=ExclusiveSessionStatus.ACTIVE, index=True)
    
    # Timestamps
    activated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Activation location data
    activation_lat = Column(Float, nullable=True)
    activation_lng = Column(Float, nullable=True)
    activation_accuracy_m = Column(Float, nullable=True)
    activation_distance_to_charger_m = Column(Float, nullable=True)  # Computed distance at activation
    
    # V3: Intent capture fields
    intent = Column(String(50), nullable=True)  # "eat" | "work" | "quick-stop"
    intent_metadata = Column(JSON, nullable=True)  # {party_size, needs_power_outlet, is_to_go}
    
    # Idempotency key for deduplication (P0 race condition fix)
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    
    # Relationships
    driver = relationship("User", foreign_keys=[driver_id])
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
    charger = relationship("Charger", foreign_keys=[charger_id])
    intent_session = relationship("IntentSession", foreign_keys=[intent_session_id])
    charging_session = relationship("SessionEvent", foreign_keys=[charging_session_id])
    
    __table_args__ = (
        # Indexes for common queries
        # Note: SQLite doesn't support partial unique indexes, so we enforce uniqueness in code
    )

