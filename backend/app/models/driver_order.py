"""
Driver Order Model

Tracks when a driver opens a merchant's ordering URL (e.g. Toast) from within
the Nerava app, and when the order is completed or abandoned.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String

from ..core.uuid_type import UUIDType
from ..db import Base


class DriverOrder(Base):
    """Tracks driver in-app browser ordering sessions."""

    __tablename__ = "driver_orders"

    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant_id = Column(String, nullable=True)
    merchant_name = Column(String(255), nullable=True)
    ordering_url = Column(String(500), nullable=False)
    session_id = Column(String, nullable=True)
    status = Column(String(50), nullable=False, default="started", index=True)
    opened_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completion_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (Index("ix_driver_orders_driver_status", "driver_id", "status"),)
