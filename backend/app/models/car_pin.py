"""
CarPin model — PIN tokens for Phase 0 EV arrival pairing.

PINs are generated in the car browser and entered on the phone to link
car verification to phone sessions.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.orm import relationship

from ..db import Base


def generate_uuid():
    """Generate a UUID string."""
    return str(uuid.uuid4())


class CarPin(Base):
    """PIN token for car browser verification."""
    __tablename__ = "car_pins"

    # Use String(36) to match database schema (not native UUID)
    id = Column(String(36), primary_key=True, default=generate_uuid)
    pin = Column(String(7), unique=True, nullable=False)  # Format: XXX-XXX
    user_agent = Column(String(512), nullable=False)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)  # NULL = available
    used_by_session_id = Column(String(36), ForeignKey("arrival_sessions.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationship
    session = relationship("ArrivalSession", foreign_keys=[used_by_session_id])

    def is_valid(self) -> bool:
        """Check if PIN is still valid (not expired, not used)."""
        return self.used_at is None and self.expires_at > datetime.utcnow()
