"""
MerchantSubscription model — tracks Stripe subscriptions for Pro tier and Nerava Ads.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from ..core.uuid_type import UUIDType
from ..db import Base


class MerchantSubscription(Base):
    __tablename__ = "merchant_subscriptions"

    id = Column(UUIDType(), primary_key=True)
    merchant_account_id = Column(String, ForeignKey("merchant_accounts.id"), nullable=False, index=True)
    place_id = Column(String, nullable=True, index=True)

    # "pro" | "ads_flat" | "ads_cpm"
    plan = Column(String, nullable=False)
    # "active" | "canceled" | "past_due"
    status = Column(String, nullable=False, default="active")

    stripe_subscription_id = Column(String, nullable=True, unique=True, index=True)
    stripe_customer_id = Column(String, nullable=True)

    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)

    __table_args__ = (
        Index("ix_merchant_sub_account_plan", "merchant_account_id", "plan"),
    )
