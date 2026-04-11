"""
Models for Vehicle Onboarding (Anti-Fraud)
- VehicleOnboarding: Stores vehicle verification photos and status
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


class VehicleOnboarding(Base):
    """Vehicle onboarding records for anti-fraud verification"""
    __tablename__ = "vehicle_onboarding"
    
    id = Column(UUIDType(), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Status tracking
    status = Column(String, nullable=False, default="SUBMITTED", index=True)  # SUBMITTED, APPROVED, REJECTED, PENDING_REVIEW
    
    # Photo storage (S3 URLs)
    photo_urls = Column(Text, nullable=False)  # JSON array of S3 signed URLs
    
    # Optional license plate extraction (manual entry, no ML)
    license_plate = Column(String, nullable=True)
    
    # Session context
    intent_session_id = Column(UUIDType(), ForeignKey("intent_sessions.id"), nullable=True, index=True)
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True)
    
    # Review metadata
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Admin user who reviewed
    reviewed_at = Column(DateTime, nullable=True)
    review_notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Retention policy tracking
    expires_at = Column(DateTime, nullable=True, index=True)  # 90 days from creation
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    intent_session = relationship("IntentSession", foreign_keys=[intent_session_id])
    charger = relationship("Charger", foreign_keys=[charger_id])
    
    __table_args__ = (
        Index('idx_vehicle_onboarding_user_status', 'user_id', 'status'),
        Index('idx_vehicle_onboarding_status_created', 'status', 'created_at'),
        Index('idx_vehicle_onboarding_expires', 'expires_at'),
    )



