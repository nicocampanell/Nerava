"""
QueuedOrder model — holds driver's order intent until arrival trigger fires.

Status flow:
  QUEUED → RELEASED (on arrival confirmation)
  QUEUED → CANCELED (if session canceled)
  QUEUED → EXPIRED (if session expires)

The queued order is NOT sent to the merchant until RELEASED.
This is the core timing control for "Ready on Arrival".
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON
from ..core.uuid_type import UUIDType
from ..db import Base


class QueuedOrderStatus(str, Enum):
    QUEUED = "QUEUED"
    RELEASED = "RELEASED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class QueuedOrder(Base):
    """
    A queued order that will be released when the driver arrives at the charger.

    One queued order per arrival session (enforced by unique constraint).
    The order is not actually placed with the merchant until status=RELEASED.
    """
    __tablename__ = "queued_orders"

    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)

    # Link to arrival session (one queued order per session)
    arrival_session_id = Column(
        UUIDType(),
        ForeignKey("arrival_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Merchant reference
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=False, index=True)

    # Status
    status = Column(
        String(20),
        nullable=False,
        default=QueuedOrderStatus.QUEUED.value,
    )

    # URLs
    ordering_url = Column(Text, nullable=False)  # Snapshot from merchant at queue time
    release_url = Column(Text, nullable=True)    # Computed on release (with tracking params)

    # Optional order metadata
    order_number = Column(String(100), nullable=True)  # Driver may paste order/receipt ID
    payload_json = Column(JSON, nullable=True)        # Placeholder for future order data

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    expired_at = Column(DateTime, nullable=True)

    # Relationships
    arrival_session = relationship("ArrivalSession", backref="queued_order", uselist=False)
    merchant = relationship("Merchant", foreign_keys=[merchant_id])

    __table_args__ = (
        # Ensure one queued order per session
        UniqueConstraint("arrival_session_id", name="uq_queued_order_session"),
        # Index for finding queued orders by merchant and status
        Index("idx_queued_order_merchant_status", "merchant_id", "status"),
        # Index for finding orders to expire
        Index("idx_queued_order_created", "created_at"),
    )

    def release(self, release_url: str) -> None:
        """Mark this queued order as released."""
        self.status = QueuedOrderStatus.RELEASED.value
        self.released_at = datetime.utcnow()
        self.release_url = release_url

    def cancel(self) -> None:
        """Mark this queued order as canceled."""
        self.status = QueuedOrderStatus.CANCELED.value
        self.canceled_at = datetime.utcnow()

    def expire(self) -> None:
        """Mark this queued order as expired."""
        self.status = QueuedOrderStatus.EXPIRED.value
        self.expired_at = datetime.utcnow()

    @property
    def is_active(self) -> bool:
        """Returns True if the order is still queued (not released/canceled/expired)."""
        return self.status == QueuedOrderStatus.QUEUED.value
