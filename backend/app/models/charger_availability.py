"""Charger real-time availability snapshot model."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String

from app.db import Base


class ChargerAvailabilitySnapshot(Base):
    """Stores periodic availability snapshots from TomTom/Google/Tesla APIs."""
    __tablename__ = "charger_availability_snapshots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    charger_id = Column(String, nullable=False, index=True)
    tomtom_availability_id = Column(String, nullable=True)
    source = Column(String(30), nullable=False)  # "tomtom", "google_places", "tesla_fleet"
    total_ports = Column(Integer, nullable=True)
    available_ports = Column(Integer, nullable=True)
    occupied_ports = Column(Integer, nullable=True)
    out_of_service_ports = Column(Integer, nullable=True)
    connector_details = Column(JSON, nullable=True)  # Full per-connector breakdown
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_avail_charger_recorded", "charger_id", "recorded_at"),
    )
