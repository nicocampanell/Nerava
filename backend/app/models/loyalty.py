"""Loyalty card models — punch card programs and driver progress."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from ..db import Base


class LoyaltyCard(Base):
    """A merchant's punch card program."""
    __tablename__ = "loyalty_cards"

    id = Column(String(36), primary_key=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=False, index=True)
    place_id = Column(String, nullable=True, index=True)
    program_name = Column(String, nullable=False)
    visits_required = Column(Integer, nullable=False)
    reward_cents = Column(Integer, nullable=False, default=0)
    reward_description = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    progress = relationship("LoyaltyProgress", back_populates="loyalty_card")


class LoyaltyProgress(Base):
    """A driver's progress on a specific loyalty card."""
    __tablename__ = "loyalty_progress"

    id = Column(String(36), primary_key=True)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    loyalty_card_id = Column(String(36), ForeignKey("loyalty_cards.id"), nullable=False, index=True)
    merchant_id = Column(String, nullable=False, index=True)
    visit_count = Column(Integer, nullable=False, default=0)
    last_visit_at = Column(DateTime, nullable=True)
    reward_unlocked = Column(Boolean, nullable=False, default=False)
    reward_unlocked_at = Column(DateTime, nullable=True)
    reward_claimed = Column(Boolean, nullable=False, default=False)
    reward_claimed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    loyalty_card = relationship("LoyaltyCard", back_populates="progress")

    __table_args__ = (
        UniqueConstraint("driver_user_id", "loyalty_card_id", name="uq_loyalty_progress_driver_card"),
    )
