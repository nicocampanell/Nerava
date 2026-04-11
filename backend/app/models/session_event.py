"""
Session Event & Incentive Grant Models

SessionEvent: The atomic unit — a verified charging session.
IncentiveGrant: Links a completed session to a campaign grant.

Key design decisions per review:
- Grants only created on session END (or min duration threshold crossed)
- One session = one grant max (highest priority campaign wins)
- idempotency_key on IncentiveGrant for atomic Nova grants
- ended_reason and quality_score fields for anti-fraud
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON


class SessionEvent(Base):
    """
    A verified EV charging session.

    Created when a driver starts charging (via Tesla API polling or webhook).
    Updated when session ends. Incentive evaluation happens on session end.
    """
    __tablename__ = "session_events"

    id = Column(UUIDType(), primary_key=True)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Legacy column from original create_all() — kept in sync with driver_user_id
    user_id = Column(Integer, nullable=True)

    # --- Charger info ---
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)
    charger_network = Column(String, nullable=True)   # "Tesla", "ChargePoint", etc.
    zone_id = Column(String, nullable=True, index=True)
    connector_type = Column(String, nullable=True)     # "CCS", "Tesla", etc.
    power_kw = Column(Float, nullable=True)

    # --- Timing ---
    session_start = Column(DateTime, nullable=False, index=True)
    session_end = Column(DateTime, nullable=True)       # null = still charging
    duration_minutes = Column(Integer, nullable=True)   # computed on session_end

    # --- Energy ---
    kwh_delivered = Column(Float, nullable=True)

    # --- Source & verification ---
    source = Column(String, nullable=False, default="tesla_api")
    # Sources: tesla_api, chargepoint_api, evgo_api, ocpp, manual, demo
    source_session_id = Column(String, nullable=True)   # external ID from provider
    verified = Column(Boolean, default=False, nullable=False)
    verification_method = Column(String, nullable=True)
    # Methods: api_polling, webhook, manual, admin

    # --- Location ---
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)

    # --- Vehicle telemetry ---
    battery_start_pct = Column(Integer, nullable=True)
    battery_end_pct = Column(Integer, nullable=True)
    vehicle_id = Column(String, nullable=True)
    vehicle_vin = Column(String, nullable=True)

    # --- Anti-fraud (per review) ---
    ended_reason = Column(String, nullable=True)
    # Reasons: unplugged, full, moved, timeout, unknown
    quality_score = Column(Integer, nullable=True)
    # 0-100, computed by anti-fraud heuristics. null = not yet scored.

    # --- Smart polling ---
    next_poll_at = Column(DateTime, nullable=True, index=True)
    # Scheduled time for the ScheduledPollWorker to run the verification poll.
    # Set when a session is created; cleared when session ends.

    # --- Partner integration ---
    partner_id = Column(String(36), ForeignKey("partners.id"), nullable=True)
    partner_driver_id = Column(String(200), nullable=True)
    partner_status = Column(String(30), nullable=True)
    # Partner-specific lifecycle status: candidate, charging, completed
    signal_confidence = Column(Float, nullable=True)
    # Partner's confidence that charging is happening (0-1)

    # --- Charger cable / adapter telemetry (Tesla charge_state) ---
    conn_charge_cable = Column(String(50), nullable=True)
    # e.g. "IEC", "SAE", "GB_AC", "GB_DC" — identifies adapter in use
    fast_charger_brand = Column(String(100), nullable=True)
    # e.g. "Tesla", "EVject", "" — brand of the DC fast charger
    charger_voltage = Column(Float, nullable=True)
    charger_actual_current = Column(Float, nullable=True)

    # --- Vehicle info (partner-submitted) ---
    vehicle_make = Column(String, nullable=True)
    vehicle_model = Column(String, nullable=True)
    vehicle_year = Column(Integer, nullable=True)

    # --- Metadata ---
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    session_metadata = Column("metadata", JSON, nullable=True)

    # --- Relationships ---
    driver = relationship("User", foreign_keys=[driver_user_id])
    grant = relationship("IncentiveGrant", back_populates="session_event", uselist=False)

    __table_args__ = (
        Index("ix_session_events_driver_start", "driver_user_id", "session_start"),
        Index("ix_session_events_charger_start", "charger_id", "session_start"),
        Index("ix_session_events_partner_start", "partner_id", "session_start"),
        UniqueConstraint("source", "source_session_id", name="uq_session_source"),
    )


class IncentiveGrant(Base):
    """
    Links a completed session event to a campaign grant.

    One session can earn at most one campaign grant (highest priority wins).
    Grant is created when session ends and meets minimum duration.
    """
    __tablename__ = "incentive_grants"

    id = Column(UUIDType(), primary_key=True)
    session_event_id = Column(UUIDType(), ForeignKey("session_events.id"), nullable=False, index=True)
    campaign_id = Column(UUIDType(), ForeignKey("campaigns.id"), nullable=False, index=True)
    # Production DB column is "user_id" (from original create_all), ORM attribute is "driver_user_id"
    driver_user_id = Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True)

    amount_cents = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    # Statuses: pending, granted, clawed_back

    # Link to Nova ledger
    nova_transaction_id = Column(UUIDType(), ForeignKey("nova_transactions.id"), nullable=True)

    # Reward routing for partner sessions
    reward_destination = Column(String(30), nullable=False, default="nerava_wallet")
    # Values: nerava_wallet, partner_managed, deferred

    # Idempotency (per review: must be present for atomic Nova grants)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)

    granted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    grant_metadata = Column("metadata", JSON, nullable=True)

    # --- Relationships ---
    session_event = relationship("SessionEvent", back_populates="grant")
    campaign = relationship("Campaign", back_populates="grants", foreign_keys=[campaign_id])
    driver = relationship("User", foreign_keys=[driver_user_id])
    nova_transaction = relationship("NovaTransaction", foreign_keys=[nova_transaction_id])

    __table_args__ = (
        # One grant per session (no stacking in MVP)
        UniqueConstraint("session_event_id", name="uq_one_grant_per_session"),
        Index("ix_incentive_grants_campaign", "campaign_id", "created_at"),
        Index("ix_incentive_grants_driver", "user_id", "created_at"),
    )
