"""
Merchant Reward Models

Covers:
- MerchantJoinRequest: Driver demand signal for non-partner merchants
- RewardClaim: Driver claims a merchant reward (before purchase)
- ReceiptSubmission: Receipt photo upload + OCR verification
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import relationship

from app.db import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 1. Request-to-Join (demand capture)
# ---------------------------------------------------------------------------

class MerchantJoinRequest(Base):
    __tablename__ = "merchant_join_requests"

    id = Column(String(36), primary_key=True, default=_uuid)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant_id = Column(String, nullable=True, index=True)       # WYC merchant ID if exists
    place_id = Column(String, nullable=True, index=True)          # Google place_id
    merchant_name = Column(String, nullable=False)
    charger_id = Column(String, nullable=True)                    # Which charger driver was near
    interest_tags = Column(JSON, nullable=True)                   # ["coffee", "food", "discount"]
    note = Column(Text, nullable=True)                            # Optional free-text
    status = Column(String(20), nullable=False, default="pending")  # pending | contacted | joined | declined
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    driver = relationship("User", foreign_keys=[driver_user_id])

    __table_args__ = (
        # One request per driver per merchant (idempotent)
        Index("ix_join_req_driver_merchant", "driver_user_id", "place_id", unique=True),
    )


# ---------------------------------------------------------------------------
# 2. Reward Claims
# ---------------------------------------------------------------------------

class RewardClaimStatus(str, enum.Enum):
    CLAIMED = "claimed"
    RECEIPT_UPLOADED = "receipt_uploaded"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RewardClaim(Base):
    __tablename__ = "reward_claims"

    id = Column(String(36), primary_key=True, default=_uuid)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    campaign_id = Column(String(36), nullable=True, index=True)   # FK to campaigns if applicable
    merchant_id = Column(String, nullable=True, index=True)       # WYC merchant ID
    place_id = Column(String, nullable=True)                      # Google place_id
    merchant_name = Column(String, nullable=True)
    session_event_id = Column(String(36), nullable=True)          # Linked charging session
    charger_id = Column(String, nullable=True)

    status = Column(
        SQLEnum(RewardClaimStatus, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=RewardClaimStatus.CLAIMED,
    )

    reward_cents = Column(Integer, nullable=True)                 # Locked reward at claim time
    reward_description = Column(String, nullable=True)            # "Free Margarita", "$4 cashback"

    claimed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    receipt_submission_id = Column(String(36), nullable=True)     # Set after receipt upload

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("User", foreign_keys=[driver_user_id])

    __table_args__ = (
        Index("ix_reward_claim_driver_status", "driver_user_id", "status"),
    )


# ---------------------------------------------------------------------------
# 3. Receipt Submissions
# ---------------------------------------------------------------------------

class ReceiptStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ReceiptSubmission(Base):
    __tablename__ = "receipt_submissions"

    id = Column(String(36), primary_key=True, default=_uuid)
    driver_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reward_claim_id = Column(String(36), ForeignKey("reward_claims.id"), nullable=False, index=True)
    campaign_id = Column(String(36), nullable=True)
    merchant_id = Column(String, nullable=True)
    place_id = Column(String, nullable=True)

    # Image
    image_url = Column(Text, nullable=False)                      # S3 URL
    image_key = Column(String, nullable=True)                     # S3 object key

    # Status
    status = Column(
        SQLEnum(ReceiptStatus, values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ReceiptStatus.PENDING,
    )

    # OCR results
    ocr_provider = Column(String(50), nullable=True)              # "taggun" | "manual"
    ocr_raw_response = Column(JSON, nullable=True)
    ocr_merchant_name = Column(String, nullable=True)
    ocr_total_cents = Column(Integer, nullable=True)
    ocr_timestamp = Column(DateTime(timezone=True), nullable=True)
    ocr_confidence = Column(Float, nullable=True)                 # 0.0 - 1.0

    # Approval
    rejection_reason = Column(Text, nullable=True)
    approved_reward_cents = Column(Integer, nullable=True)
    reviewed_by = Column(String, nullable=True)                   # "auto" | admin user id

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("User", foreign_keys=[driver_user_id])
    reward_claim = relationship("RewardClaim", foreign_keys=[reward_claim_id])
