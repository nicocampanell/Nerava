"""
MerchantPOSCredentials — separated POS credentials table.
Keeps sensitive tokens isolated from notification preferences.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from ..db import Base


class MerchantPOSCredentials(Base):
    """
    POS credentials for a merchant. Separated from notification config
    so that notification preferences can be edited without touching secrets.
    """
    __tablename__ = "merchant_pos_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    merchant_id = Column(String, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    # POS type: 'toast', 'square'
    pos_type = Column(String(20), nullable=False)

    # Toast-specific
    restaurant_guid = Column(String(100), nullable=True)

    # Encrypted OAuth tokens (Fernet, same pattern as Square)
    access_token_encrypted = Column(Text, nullable=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
