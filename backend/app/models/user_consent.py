"""
User Consent model for GDPR compliance
Tracks user consent for analytics, marketing, etc.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..db import Base


class UserConsent(Base):
    """Tracks user consent for various purposes"""
    __tablename__ = "user_consents"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    consent_type = Column(String(50), nullable=False)  # "analytics", "marketing", etc.
    granted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    ip_address = Column(String, nullable=True)  # IP address when consent was granted/revoked
    privacy_policy_version = Column(String, nullable=True)  # Version of privacy policy at time of consent
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('ix_user_consents_user_type', 'user_id', 'consent_type', unique=True),
    )
    
    def is_granted(self) -> bool:
        """Check if consent is currently granted"""
        return self.granted_at is not None and (
            self.revoked_at is None or self.granted_at > self.revoked_at
        )
