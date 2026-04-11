"""Funding source model for bank accounts/debit cards linked via Plaid."""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from ..core.uuid_type import UUIDType
from ..db import Base


class FundingSource(Base):
    __tablename__ = "funding_sources"

    id = Column(UUIDType, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(String(20), nullable=False, default="dwolla")  # "dwolla"
    external_id = Column(String(500), nullable=False)  # Dwolla funding source URL
    institution_name = Column(String(255), nullable=True)  # e.g. "Chase"
    account_mask = Column(String(10), nullable=True)  # last 4 digits
    account_type = Column(String(50), nullable=True)  # "checking", "savings", "debit_card"
    is_default = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    removed_at = Column(DateTime, nullable=True)
