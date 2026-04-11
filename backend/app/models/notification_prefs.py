"""
Notification preferences model
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship

from ..db import Base


class UserNotificationPrefs(Base):
    """User notification preferences"""
    __tablename__ = "user_notification_prefs"
    
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    earned_nova = Column(Boolean, default=True, nullable=False)
    nearby_nova = Column(Boolean, default=True, nullable=False)
    wallet_reminders = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship
    user = relationship("User", backref="notification_prefs")







