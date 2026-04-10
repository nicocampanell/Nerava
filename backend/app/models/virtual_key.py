"""
Virtual Key model for Tesla Fleet API integration.

Stores Tesla vehicle pairing information for automatic arrival detection.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


class VirtualKey(Base):
    """Virtual Key for Tesla vehicle pairing and arrival tracking."""
    __tablename__ = "virtual_keys"

    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Tesla Fleet API identifiers
    tesla_vehicle_id = Column(String(100), nullable=True)  # From Fleet API
    vin = Column(String(17), nullable=True)  # Vehicle Identification Number
    vehicle_name = Column(String(100), nullable=True)  # User-friendly name

    # Provisioning state
    provisioning_token = Column(String(255), unique=True, nullable=False, index=True)
    qr_code_url = Column(String(500), nullable=True)  # S3 URL for QR image

    # Status: 'pending', 'paired', 'active', 'revoked', 'expired'
    status = Column(String(20), default='pending', nullable=False, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    paired_at = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    # Metadata
    pairing_attempts = Column(Integer, default=0)
    last_telemetry_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_virtual_key_user_status", "user_id", "status"),
        Index("idx_virtual_key_provisioning_token", "provisioning_token"),
        # Unique constraint: one active virtual key per VIN
        # Note: This will be enforced at application level for SQLite compatibility
    )
