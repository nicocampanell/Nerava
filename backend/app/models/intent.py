"""
Models for Intent Capture system
- IntentSession: Tracks user location intent captures
- PerkUnlock: Tracks perk unlocks by users
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


class IntentSession(Base):
    """Tracks user location intent captures for charging moments"""
    __tablename__ = "intent_sessions"
    
    id = Column(UUIDType(), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Location data
    lat = Column(Float, nullable=False, index=True)
    lng = Column(Float, nullable=False, index=True)
    accuracy_m = Column(Float, nullable=True)  # Location accuracy in meters
    client_ts = Column(DateTime, nullable=True)  # Client timestamp (ISO string parsed)
    
    # Charger proximity
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)
    charger_distance_m = Column(Float, nullable=True)  # Distance to nearest charger in meters
    confidence_tier = Column(String, nullable=False, index=True)  # "A", "B", "C"
    
    # Source tracking
    source = Column(String, default="web", nullable=False)  # "web", "mobile", etc.
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    charger = relationship("Charger", foreign_keys=[charger_id])
    
    __table_args__ = (
        Index('idx_intent_sessions_user_created', 'user_id', 'created_at'),
        Index('idx_intent_sessions_location', 'lat', 'lng'),
        Index('idx_intent_sessions_confidence', 'confidence_tier', 'created_at'),
    )


class PerkUnlock(Base):
    """Tracks perk unlocks by users"""
    __tablename__ = "perk_unlocks"
    
    id = Column(UUIDType(), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    perk_id = Column(Integer, ForeignKey("merchant_perks.id"), nullable=False, index=True)
    
    # Unlock method
    unlock_method = Column(String, nullable=False)  # "dwell_time", "user_confirmation"
    
    # Context
    intent_session_id = Column(UUIDType(), ForeignKey("intent_sessions.id"), nullable=True, index=True)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True, index=True)
    
    # Dwell time data (if applicable)
    dwell_time_seconds = Column(Integer, nullable=True)
    
    # Timestamps
    unlocked_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    perk = relationship("MerchantPerk", foreign_keys=[perk_id])
    intent_session = relationship("IntentSession", foreign_keys=[intent_session_id])
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
    
    __table_args__ = (
        Index('idx_perk_unlocks_user_perk', 'user_id', 'perk_id'),
        Index('idx_perk_unlocks_user_unlocked', 'user_id', 'unlocked_at'),
        Index('idx_perk_unlocks_perk_unlocked', 'perk_id', 'unlocked_at'),
    )

