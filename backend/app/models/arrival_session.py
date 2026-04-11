"""
ArrivalSession model — core EV Arrival coordination primitive.

States: pending_order → awaiting_arrival → arrived → merchant_notified → completed
Also: expired, canceled, completed_unbillable
"""
import random
import string
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from ..core.uuid_type import UUIDType
from ..db import Base


def _generate_reply_code() -> str:
    """Generate a 4-digit merchant reply code for SMS session mapping."""
    return "".join(random.choices(string.digits, k=4))


class ArrivalSession(Base):
    """
    EV Arrival session between a driver and a merchant.

    Lifecycle:
        pending_order → awaiting_arrival → arrived → merchant_notified → completed
    Terminal states: completed, completed_unbillable, expired, canceled
    """
    __tablename__ = "arrival_sessions"

    id = Column(UUIDType(), primary_key=True, default=uuid.uuid4)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=False, index=True)
    charger_id = Column(String, ForeignKey("chargers.id"), nullable=True, index=True)

    # Mode
    arrival_type = Column(String(20), nullable=False)  # 'ev_curbside' or 'ev_dine_in'

    # Browser detection fields
    browser_source = Column(String(30), nullable=True)  # 'tesla_browser', 'web', 'ios_app'
    ev_brand = Column(String(30), nullable=True)  # 'Tesla', 'Polestar', etc.
    ev_firmware = Column(String(50), nullable=True)

    # Fulfillment type (both are Ready on Arrival)
    fulfillment_type = Column(String(20), nullable=True)  # 'ev_dine_in', 'ev_curbside'
    # ev_dine_in = Driver walks to restaurant, food ready when they arrive
    # ev_curbside = Driver stays at car, merchant brings food to charger

    # Order binding
    order_number = Column(String(100), nullable=True)
    order_source = Column(String(20), nullable=True)  # 'manual', 'toast', 'square'
    order_total_cents = Column(Integer, nullable=True)  # POS or driver estimate
    order_status = Column(String(20), nullable=True)  # 'unknown','placed','ready','completed' (POS status)
    driver_estimate_cents = Column(Integer, nullable=True)  # driver's estimate
    merchant_reported_total_cents = Column(Integer, nullable=True)  # merchant-reported
    total_source = Column(String(20), nullable=True)  # 'pos', 'merchant_reported', 'driver_estimate'

    # Order queuing and release
    queued_order_status = Column(String(20), nullable=True, default="queued")
    # 'queued' — Order placed, waiting for arrival trigger
    # 'released' — Arrival detected, order sent to kitchen
    # 'preparing' — Kitchen acknowledged, cooking
    # 'ready' — Food ready for pickup/delivery
    # 'completed' — Order fulfilled

    # Destination (restaurant location for arrival detection)
    destination_merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True)
    destination_lat = Column(Float, nullable=True)
    destination_lng = Column(Float, nullable=True)

    # Vehicle (copied from user at session creation for immutability)
    vehicle_color = Column(String(30), nullable=True)
    vehicle_model = Column(String(60), nullable=True)

    # Session lifecycle status
    status = Column(String(30), nullable=False, default="pending_order")
    # pending_order → awaiting_arrival → arrived → merchant_notified → completed
    # also: expired, canceled, completed_unbillable

    # Merchant reply code for SMS session mapping (4 digits)
    merchant_reply_code = Column(String(4), nullable=True, default=_generate_reply_code)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    order_bound_at = Column(DateTime, nullable=True)
    geofence_entered_at = Column(DateTime, nullable=True)
    merchant_notified_at = Column(DateTime, nullable=True)
    merchant_confirmed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)

    # Arrival detection timestamps
    arrival_detected_at = Column(DateTime, nullable=True)
    order_released_at = Column(DateTime, nullable=True)
    order_ready_at = Column(DateTime, nullable=True)

    # Geofence verification data
    arrival_lat = Column(Float, nullable=True)
    arrival_lng = Column(Float, nullable=True)
    arrival_accuracy_m = Column(Float, nullable=True)

    # Distance when arrival was detected
    arrival_distance_m = Column(Float, nullable=True)

    # Virtual Key integration
    virtual_key_id = Column(UUIDType(), ForeignKey("virtual_keys.id"), nullable=True, index=True)
    arrival_source = Column(String(30), nullable=True)  # 'virtual_key', 'geofence', 'manual'
    vehicle_soc_at_arrival = Column(Float, nullable=True)  # Battery % from telemetry

    # Billing
    platform_fee_bps = Column(Integer, nullable=False, default=2000)  # 20% = 2000 bps
    billable_amount_cents = Column(Integer, nullable=True)
    billing_status = Column(String(20), default="pending")  # pending, invoiced, paid

    # Feedback
    feedback_rating = Column(String(10), nullable=True)  # 'up' or 'down'
    feedback_reason = Column(String(50), nullable=True)
    feedback_comment = Column(String(200), nullable=True)

    # Idempotency
    idempotency_key = Column(String(100), unique=True, nullable=True)

    # ─── V0 EV Arrival Code Fields ────────────────────────────────────
    # Flow type: 'legacy' (existing flow) or 'arrival_code' (V0 code-first)
    flow_type = Column(String(20), nullable=False, default='legacy')

    # Arrival code (format: NVR-XXXX)
    arrival_code = Column(String(10), unique=True, nullable=True, index=True)
    arrival_code_generated_at = Column(DateTime, nullable=True)
    arrival_code_expires_at = Column(DateTime, nullable=True)
    arrival_code_redeemed_at = Column(DateTime, nullable=True)
    arrival_code_redemption_count = Column(Integer, default=0)

    # Verification tracking
    verification_method = Column(String(20), nullable=True)  # 'browser_geofence', 'phone_geofence', 'qr_scan'
    verified_at = Column(DateTime, nullable=True)
    verification_attempts = Column(Integer, default=0)

    # SMS tracking
    checkout_url_sent = Column(String(500), nullable=True)
    sms_sent_at = Column(DateTime, nullable=True)
    sms_message_sid = Column(String(50), nullable=True)

    # QR pairing fields (for unauthenticated car browser users)
    pairing_token = Column(String(64), unique=True, nullable=True, index=True)
    pairing_token_expires_at = Column(DateTime, nullable=True)
    paired_at = Column(DateTime, nullable=True)
    paired_phone = Column(String(20), nullable=True)

    # Relationships
    driver = relationship("User", foreign_keys=[driver_id])
    merchant = relationship("Merchant", foreign_keys=[merchant_id])
    virtual_key = relationship("VirtualKey", foreign_keys=[virtual_key_id])

    __table_args__ = (
        # Partial unique index: one active session per driver
        # Only driver_id is constrained (not driver_id + status)
        # Active statuses: pending_order, awaiting_arrival, arrived, merchant_notified
        # NOTE: Partial unique index must be created in migration SQL for PostgreSQL.
        # For SQLAlchemy, we enforce this in application code.
        Index("idx_arrival_merchant_status", "merchant_id", "status"),
        Index("idx_arrival_billing", "billing_status"),
        Index("idx_arrival_created", "created_at"),
        Index("idx_arrival_reply_code", "merchant_reply_code"),
        Index("idx_arrival_driver_active", "driver_id", "status"),
        Index("idx_arrival_queued", "queued_order_status", "destination_merchant_id"),
    )


# Valid status transitions (legacy flow)
VALID_TRANSITIONS = {
    "pending_order": {"awaiting_arrival", "expired", "canceled"},
    "awaiting_arrival": {"arrived", "expired", "canceled"},
    "arrived": {"merchant_notified", "expired", "canceled"},
    "merchant_notified": {"completed", "completed_unbillable", "expired", "canceled"},
}

# V0 Arrival Code flow statuses
ARRIVAL_CODE_STATUSES = {
    "pending_pairing",      # Waiting for phone to complete OTP
    "pending_verification", # Paired but not verified at charger
    "verified",             # Verified at charger, ready for code generation
    "code_generated",       # Code generated, SMS sent
    "code_redeemed",        # Code was used at checkout
    "merchant_confirmed",   # Merchant confirmed fulfillment
}

ACTIVE_STATUSES = {"pending_order", "awaiting_arrival", "arrived", "merchant_notified",
                   "pending_pairing", "pending_verification", "verified", "code_generated", "code_redeemed"}
TERMINAL_STATUSES = {"completed", "completed_unbillable", "expired", "canceled", "merchant_confirmed"}
