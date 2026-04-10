"""Claim session model for merchant onboarding"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.sql import func

from ..core.uuid_type import UUIDType
from ..db import Base


class ClaimSession(Base):
    """Temporary session for merchant business claim flow"""
    __tablename__ = "claim_sessions"

    id = Column(UUIDType(), primary_key=True, default=lambda: str(uuid.uuid4()))
    merchant_id = Column(UUIDType(), ForeignKey("domain_merchants.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(20), nullable=False, index=True)
    business_name = Column(String(255), nullable=False)
    phone_verified = Column(Boolean, nullable=False, default=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    magic_link_token = Column(String(255), nullable=True, unique=True, index=True)
    magic_link_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)




