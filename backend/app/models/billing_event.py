"""
BillingEvent — records billable amounts for completed EV Arrival sessions.
Created only on merchant confirmation with a known total.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


class BillingEvent(Base):
    """
    A billing record for a completed arrival session.
    Created when merchant confirms AND a total is available.
    """
    __tablename__ = "billing_events"

    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)
    arrival_session_id = Column(UUIDType(), ForeignKey("arrival_sessions.id"), nullable=False, index=True)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=False, index=True)

    order_total_cents = Column(Integer, nullable=False)
    fee_bps = Column(Integer, nullable=False)  # e.g. 500 = 5%
    billable_cents = Column(Integer, nullable=False)  # order_total * fee_bps / 10000
    total_source = Column(String(20), nullable=False)  # 'pos', 'merchant_reported', 'driver_estimate'

    status = Column(String(20), default="pending", nullable=False)  # pending, invoiced, paid, disputed
    invoice_id = Column(String(100), nullable=True)  # Stripe invoice ID or internal ref

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    invoiced_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    # Relationships
    arrival_session = relationship("ArrivalSession", foreign_keys=[arrival_session_id])
    merchant = relationship("Merchant", foreign_keys=[merchant_id])

    __table_args__ = (
        Index("idx_billing_merchant_status", "merchant_id", "status"),
        Index("idx_billing_pending", "status"),
    )
