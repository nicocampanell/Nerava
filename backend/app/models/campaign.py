"""
Campaign Models — Programmable Incentive Layer

Simplified design per review:
- No separate `funders` table — sponsor info stored as columns on Campaign
- No EAV `campaign_rules` table — rules stored as JSON columns on Campaign
- Rewards trigger on session END (or min duration threshold), not session start
- One session = one campaign grant (highest priority wins, no stacking in MVP)
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base

try:
    from sqlalchemy import JSON
except Exception:
    JSON = SQLITE_JSON


class Campaign(Base):
    """
    A time-bound, budget-capped incentive program.

    Sponsors (charging networks, hotels, cities, OEMs) create campaigns
    to reward drivers for charging behavior that matches certain rules.
    """
    __tablename__ = "campaigns"

    id = Column(UUIDType(), primary_key=True)

    # --- Sponsor info (inline, no separate funders table) ---
    sponsor_name = Column(String, nullable=False)
    sponsor_email = Column(String, nullable=True)
    sponsor_logo_url = Column(String, nullable=True)
    sponsor_type = Column(String, nullable=True)  # charging_network, hotel, city, oem, merchant, internal

    # --- Campaign basics ---
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    campaign_type = Column(String, nullable=False, default="custom")
    # Types: utilization_boost, off_peak, new_driver, repeat_visit,
    #        merchant_traffic, corridor, loyalty, custom
    status = Column(String, nullable=False, default="draft", index=True)
    # Statuses: draft, active, paused, exhausted, completed, canceled
    priority = Column(Integer, nullable=False, default=100)
    # Lower number = higher priority. Used to pick winner when session matches multiple.

    # --- Budget ---
    budget_cents = Column(Integer, nullable=False)
    spent_cents = Column(Integer, nullable=False, default=0)
    cost_per_session_cents = Column(Integer, nullable=False)
    max_sessions = Column(Integer, nullable=True)  # absolute cap (optional)
    sessions_granted = Column(Integer, nullable=False, default=0)

    # --- Schedule ---
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)  # null = run until budget exhausted
    auto_renew = Column(Boolean, default=False, nullable=False)
    auto_renew_budget_cents = Column(Integer, nullable=True)  # monthly renewal amount

    # --- Driver caps ---
    max_grants_per_driver_per_day = Column(Integer, nullable=True)  # e.g., 3
    max_grants_per_driver_per_campaign = Column(Integer, nullable=True)  # lifetime per driver
    max_grants_per_driver_per_charger = Column(Integer, nullable=True)  # per charger

    # --- Targeting rules (JSON columns, not EAV) ---
    # All non-null rules are AND-ed together
    rule_charger_ids = Column(JSON, nullable=True)      # ["ch_123", "ch_456"]
    rule_charger_networks = Column(JSON, nullable=True)  # ["Tesla", "ChargePoint"]
    rule_zone_ids = Column(JSON, nullable=True)          # ["domain_austin"]
    rule_geo_center_lat = Column(Float, nullable=True)   # for geo_radius rule
    rule_geo_center_lng = Column(Float, nullable=True)
    rule_geo_radius_m = Column(Integer, nullable=True)
    rule_time_start = Column(String, nullable=True)      # "22:00" (HH:MM)
    rule_time_end = Column(String, nullable=True)        # "06:00"
    rule_days_of_week = Column(JSON, nullable=True)      # [1,2,3,4,5] (Mon-Fri)
    rule_min_duration_minutes = Column(Integer, nullable=False, default=15)
    # ^ Mandatory per review: minimum charging duration to qualify
    rule_max_duration_minutes = Column(Integer, nullable=True)
    rule_min_power_kw = Column(Float, nullable=True)     # DC fast only
    rule_connector_types = Column(JSON, nullable=True)   # ["CCS", "CHAdeMO"]
    rule_driver_session_count_min = Column(Integer, nullable=True)  # repeat driver
    rule_driver_session_count_max = Column(Integer, nullable=True)  # new driver (e.g., max=1)
    rule_driver_allowlist = Column(JSON, nullable=True)  # email list or user_id list

    # --- Partner session controls ---
    allow_partner_sessions = Column(Boolean, default=True, nullable=False)
    rule_partner_ids = Column(JSON, nullable=True)       # restrict to specific partner IDs
    rule_min_trust_tier = Column(Integer, nullable=True)  # minimum partner trust tier required

    # --- Funding (Stripe Checkout) ---
    funding_status = Column(String, nullable=False, default="unfunded", server_default="unfunded")
    # Values: unfunded, pending, funded
    stripe_checkout_session_id = Column(String(255), nullable=True)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    funded_at = Column(DateTime, nullable=True)
    gross_funding_cents = Column(Integer, nullable=True)  # Total amount charged to sponsor
    platform_fee_cents = Column(Integer, nullable=True)   # Nerava's platform fee (20% default)

    # --- Metadata ---
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    metadata_json = Column("campaign_metadata", JSON, nullable=True)

    # --- Relationships ---
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    grants = relationship("IncentiveGrant", back_populates="campaign", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_campaigns_status_priority", "status", "priority"),
        Index("ix_campaigns_sponsor", "sponsor_name"),
        Index("ix_campaigns_start_end", "start_date", "end_date"),
        CheckConstraint("spent_cents <= budget_cents", name="ck_campaign_budget"),
        CheckConstraint("priority >= 0", name="ck_campaign_priority"),
        CheckConstraint("rule_min_duration_minutes >= 0", name="ck_campaign_min_duration"),
    )
