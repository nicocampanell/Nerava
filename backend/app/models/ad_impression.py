"""
AdImpression model — tracks driver-side impressions of merchant listings for CPM billing.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String

from ..core.uuid_type import UUIDType
from ..db import Base


class AdImpression(Base):
    __tablename__ = "ad_impressions"

    id = Column(UUIDType(), primary_key=True)
    merchant_id = Column(String, ForeignKey("domain_merchants.id"), nullable=False, index=True)
    place_id = Column(String, nullable=True)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # "carousel" | "featured" | "search"
    impression_type = Column(String, nullable=False)
    session_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_ad_impressions_merchant_created", "merchant_id", "created_at"),
    )
