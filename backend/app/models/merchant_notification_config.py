"""
MerchantNotificationConfig — how a merchant wants to receive arrival notifications.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from ..db import Base


class MerchantNotificationConfig(Base):
    """Merchant notification preferences for EV Arrivals."""
    __tablename__ = "merchant_notification_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # Notification channels
    notify_sms = Column(Boolean, default=True, nullable=False)
    notify_email = Column(Boolean, default=False, nullable=False)
    sms_phone = Column(String(20), nullable=True)  # E.164 format
    email_address = Column(String(255), nullable=True)

    # POS integration type (none, toast, square)
    pos_integration = Column(String(20), default="none", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
