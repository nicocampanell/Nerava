"""
Verified Visit Model
Tracks verified merchant visits with incremental verification codes for merchant-driver linkage.
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String

from ..db import Base


class VerifiedVisit(Base):
    """
    Tracks verified visits at merchants with incremental verification codes.

    Verification codes follow the format: {REGION}-{MERCHANT_CODE}-{VISIT_NUMBER}
    Example: ATX-ASADAS-023 (23rd visit to Asadas in Austin region)

    Merchants use these codes to manually link orders to redemptions.
    """
    __tablename__ = "verified_visits"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Verification code (unique, human-readable)
    verification_code = Column(String(32), unique=True, nullable=False, index=True)  # e.g., "ATX-ASADAS-023"

    # Components of the verification code (for querying)
    region_code = Column(String(8), nullable=False, index=True)  # e.g., "ATX"
    merchant_code = Column(String(16), nullable=False, index=True)  # e.g., "ASADAS"
    visit_number = Column(Integer, nullable=False)  # e.g., 23

    # Foreign keys
    # Note: Using String instead of UUIDType for session/charger IDs to match manual table creation
    merchant_id = Column(String, nullable=False, index=True)
    driver_id = Column(Integer, nullable=False, index=True)
    exclusive_session_id = Column(String(36), nullable=True, index=True)
    charger_id = Column(String, nullable=True, index=True)

    # Timestamps
    verified_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Redemption tracking (for merchant to link orders)
    redeemed_at = Column(DateTime(timezone=True), nullable=True)
    order_reference = Column(String(128), nullable=True)  # Merchant's POS order ID
    redemption_notes = Column(String(512), nullable=True)  # Optional notes from merchant

    # Location data at verification time
    verification_lat = Column(Float, nullable=True)
    verification_lng = Column(Float, nullable=True)

    # Relationships removed - using manual joins instead to avoid FK constraint issues
    # merchant = relationship("Merchant")
    # driver = relationship("User")
    # exclusive_session = relationship("ExclusiveSession")
    # charger = relationship("Charger")

    # Visit date (for daily code reset)
    visit_date = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # Unique constraint on merchant + visit number + date (allows daily reset)
        Index('uq_verified_visits_merchant_visit_date', 'merchant_id', 'visit_number', 'visit_date', unique=True),
        # Index for merchant lookups
        Index('ix_verified_visits_merchant_verified', 'merchant_id', 'verified_at'),
        # Index for driver history
        Index('ix_verified_visits_driver_verified', 'driver_id', 'verified_at'),
        # Index for daily lookups
        Index('ix_verified_visits_merchant_date', 'merchant_id', 'visit_date'),
    )

    @classmethod
    def generate_verification_code(cls, region_code: str, merchant_code: str, visit_number: int) -> str:
        """Generate a verification code string."""
        return f"{region_code}-{merchant_code}-{visit_number:03d}"
