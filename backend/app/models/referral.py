"""Referral system models."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from ..db import Base


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ReferralRedemption(Base):
    __tablename__ = "referral_redemptions"

    id = Column(Integer, primary_key=True)
    referral_code_id = Column(Integer, ForeignKey("referral_codes.id"), nullable=False)
    referred_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    redeemed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reward_granted = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint('referred_user_id', name='uq_referral_one_per_user'),
    )
