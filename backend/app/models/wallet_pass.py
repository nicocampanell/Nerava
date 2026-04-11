"""
Model for Wallet Pass States
Tracks active wallet passes tied to intent sessions and merchants
"""
import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy import Enum as SQLEnum

from ..core.uuid_type import UUIDType
from ..db import Base


class WalletPassStateEnum(str, enum.Enum):
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"


class WalletPassActivation(Base):
    """Tracks wallet pass activation state for intent sessions"""
    __tablename__ = "wallet_pass_states"
    
    id = Column(UUIDType(), primary_key=True)
    session_id = Column(UUIDType(), nullable=False, index=True)  # FK to intent_sessions.id
    merchant_id = Column(String, nullable=False, index=True)  # FK to merchants.id
    
    state = Column(SQLEnum(WalletPassStateEnum), nullable=False, default=WalletPassStateEnum.ACTIVE, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_wallet_pass_session_merchant', 'session_id', 'merchant_id'),
        Index('idx_wallet_pass_expires', 'expires_at'),
    )

