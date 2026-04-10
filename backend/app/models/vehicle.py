"""
Vehicle models for Smartcar integration
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON  # for non-sqlite engines
except Exception:
    JSON = SQLITE_JSON  # fallback for sqlite


class VehicleAccount(Base):
    """Vehicle account linked to a user via Smartcar or other providers"""
    __tablename__ = "vehicle_accounts"
    
    id = Column(UUIDType(), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String, nullable=False)  # e.g., "smartcar"
    provider_vehicle_id = Column(String, nullable=False)  # Smartcar vehicle ID
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    tokens = relationship("VehicleToken", back_populates="vehicle_account", cascade="all, delete-orphan")
    telemetry = relationship("VehicleTelemetry", back_populates="vehicle_account", cascade="all, delete-orphan")


class VehicleToken(Base):
    """OAuth tokens for vehicle API access"""
    __tablename__ = "vehicle_tokens"
    
    id = Column(UUIDType(), primary_key=True)
    vehicle_account_id = Column(String, ForeignKey("vehicle_accounts.id"), nullable=False, index=True)
    access_token = Column(String, nullable=False)  # Encrypted at rest (P0 security fix)
    refresh_token = Column(String, nullable=False)  # Encrypted at rest (P0 security fix)
    encryption_version = Column(Integer, nullable=False, default=1)  # Track encryption version for migration
    expires_at = Column(DateTime, nullable=False)
    scope = Column(Text, nullable=True)  # Space-separated scopes
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    vehicle_account = relationship("VehicleAccount", back_populates="tokens")


class VehicleTelemetry(Base):
    """Vehicle telemetry data (SOC, location, charging state)"""
    __tablename__ = "vehicle_telemetry"
    
    id = Column(UUIDType(), primary_key=True)
    vehicle_account_id = Column(String, ForeignKey("vehicle_accounts.id"), nullable=False, index=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    soc_pct = Column(Float, nullable=True)  # State of charge percentage
    charging_state = Column(String, nullable=True)  # e.g., "CHARGING", "FULLY_CHARGED", "NOT_CHARGING"
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    raw_json = Column(JSON, nullable=True)  # Full API response for debugging
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    vehicle_account = relationship("VehicleAccount", back_populates="telemetry")


