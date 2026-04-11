"""
Models for Wallet Pass State (Mocked)
- WalletPassState: Mocked state machine for wallet pass
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


class WalletPassState(Base):
    """Mocked wallet pass state machine (no Apple Wallet API integration)"""
    __tablename__ = "wallet_pass_state"
    
    id = Column(UUIDType(), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # State machine
    state = Column(String, nullable=False, default="IDLE", index=True)  # IDLE, CHARGING_MOMENT, PERK_UNLOCKED
    
    # Context
    intent_session_id = Column(UUIDType(), ForeignKey("intent_sessions.id"), nullable=True, index=True)
    perk_id = Column(Integer, ForeignKey("merchant_perks.id"), nullable=True, index=True)
    
    # State transitions
    state_changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Metadata (named state_metadata to avoid SQLAlchemy reserved name conflict)
    state_metadata = Column("metadata", Text, nullable=True)  # JSON string for additional state data
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    intent_session = relationship("IntentSession", foreign_keys=[intent_session_id])
    perk = relationship("MerchantPerk", foreign_keys=[perk_id])
    
    __table_args__ = (
        Index('idx_wallet_pass_state_user_state', 'user_id', 'state'),
        Index('idx_wallet_pass_state_user_created', 'user_id', 'created_at'),
    )



