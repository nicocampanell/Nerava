from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from ..core.uuid_type import UUIDType
from ..db import Base

UUID_TYPE = UUIDType  # Alias for backward compatibility


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"
    
    id = Column(UUID_TYPE, primary_key=True)
    phone = Column(String, nullable=False, index=True)  # E.164 format
    code_hash = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    consumed = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


