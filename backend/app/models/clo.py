"""Card Linked Offers (CLO) Models for Fidel Integration"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from ..db import Base


def generate_uuid():
    return str(uuid.uuid4())


class Card(Base):
    """Linked payment card for CLO transactions"""
    __tablename__ = "cards"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    fidel_card_id = Column(String(255), nullable=True)
    last4 = Column(String(4), nullable=False)
    brand = Column(String(20), nullable=False)  # visa, mastercard, amex
    fingerprint = Column(String(100), nullable=True)  # For dedup
    is_active = Column(Boolean, nullable=False, default=True)
    linked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    driver = relationship("User", backref="linked_cards")
    transactions = relationship("CLOTransaction", back_populates="card")


class MerchantOffer(Base):
    """CLO offer configuration for a merchant"""
    __tablename__ = "merchant_offers"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    merchant_id = Column(String(36), nullable=False)  # References domain_merchants
    fidel_offer_id = Column(String(255), nullable=True)
    fidel_program_id = Column(String(255), nullable=True)
    min_spend_cents = Column(Integer, nullable=False, default=0)
    reward_cents = Column(Integer, nullable=False)
    reward_percent = Column(Integer, nullable=True)  # If percentage-based
    max_reward_cents = Column(Integer, nullable=True)  # Cap for percentage rewards
    is_active = Column(Boolean, nullable=False, default=True)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    # Relationships
    transactions = relationship("CLOTransaction", back_populates="offer")


class CLOTransaction(Base):
    """Card Linked Offer transaction record"""
    __tablename__ = "clo_transactions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    card_id = Column(String(36), ForeignKey("cards.id"), nullable=False)
    merchant_id = Column(String(36), nullable=False)
    offer_id = Column(String(36), ForeignKey("merchant_offers.id"), nullable=True)
    amount_cents = Column(Integer, nullable=False)
    reward_cents = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending, eligible, rejected, refunded, credited
    external_id = Column(String(255), nullable=True)  # Fidel transaction ID
    charging_session_id = Column(String(36), nullable=True)  # Link to charging session
    transaction_time = Column(DateTime, nullable=False)
    merchant_name = Column(String(255), nullable=True)
    merchant_location = Column(String(500), nullable=True)
    eligibility_reason = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    # Relationships
    driver = relationship("User", backref="clo_transactions")
    card = relationship("Card", back_populates="transactions")
    offer = relationship("MerchantOffer", back_populates="transactions")
