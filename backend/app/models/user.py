import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..db import Base

try:
    from sqlalchemy import JSON  # for non-sqlite engines
except Exception:
    JSON = SQLITE_JSON  # fallback for sqlite

from ..core.uuid_type import UUIDType

UUID_TYPE = UUIDType  # Alias for backward compatibility


def generate_public_id():
    """Generate a UUID string for public_id"""
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    public_id = Column(UUID_TYPE, unique=True, nullable=False, index=True, default=generate_public_id)  # External identifier for JWT sub
    email = Column(String, nullable=True, index=True)  # Nullable for phone-only users
    phone = Column(String, nullable=True, index=True)  # E.164 format, nullable for email-only users
    password_hash = Column(String, nullable=True)  # Nullable for OAuth users
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    preferences = relationship("UserPreferences", uselist=False, back_populates="user", cascade="all, delete")
    
    # Domain Charge Party MVP fields
    display_name = Column(String, nullable=True)
    role_flags = Column(String, nullable=True, default="driver")  # comma-separated: "driver,merchant_admin,admin"
    auth_provider = Column(String, nullable=False, default="local")  # local, google, apple, phone
    provider_sub = Column(String, nullable=True)  # OAuth subject ID (renamed from oauth_sub)
    admin_role = Column(String, nullable=True)  # AdminRole enum value
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    # Notification / marketing preferences
    notifications_enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    email_marketing = Column(Boolean, default=False, nullable=False, server_default="0")

    # EV Arrival: vehicle info (cached, one-time setup)
    vehicle_color = Column(String(30), nullable=True)
    vehicle_model = Column(String(60), nullable=True)
    vehicle_set_at = Column(DateTime, nullable=True)
    
    # Virtual Keys relationship
    virtual_keys = relationship("VirtualKey", foreign_keys="VirtualKey.user_id", back_populates="user")
    
    # Unique constraints enforced at application level (SQLite doesn't support partial unique indexes)
    __table_args__ = (
        # Unique constraint on (auth_provider, provider_sub) where provider_sub is not null
        # Enforced in application code for SQLite compatibility
        Index('ix_users_auth_provider_sub', 'auth_provider', 'provider_sub'),
    )

class UserPreferences(Base):
    __tablename__ = "user_preferences"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    food_tags = Column(JSON, default=list, nullable=False)          # ["coffee","tacos"]
    max_detour_minutes = Column(Integer, default=10, nullable=False)
    preferred_networks = Column(JSON, default=list, nullable=False) # ["Tesla","ChargePoint"]
    typical_start = Column(Time, default=time(18, 0), nullable=False)
    typical_end = Column(Time, default=time(22, 0), nullable=False)
    home_zip = Column(String, nullable=True)
    user = relationship("User", back_populates="preferences")


