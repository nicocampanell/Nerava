"""
Device token model for push notifications (FCM / APNs).

Stores per-user device tokens so the backend can send push notifications
to the driver's Android or iOS app.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..db import Base


def _uuid_str():
    return str(uuid.uuid4())


class DeviceToken(Base):
    """Push notification device token (FCM for Android, APNs for iOS)."""
    __tablename__ = "device_tokens"

    id = Column(String(36), primary_key=True, default=_uuid_str)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # The FCM or APNs token string
    token = Column(String(512), nullable=False)

    # "android" or "ios"
    platform = Column(String(10), nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_device_token_user", "user_id"),
        Index("idx_device_token_token", "token", unique=True),
    )
