"""Driver Wallet and Payout Models for Stripe Express Payouts"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship, synonym

from ..db import Base


def generate_uuid():
    return str(uuid.uuid4())


class DriverWallet(Base):
    """Driver wallet for accumulating rewards and processing payouts"""
    __tablename__ = "driver_wallets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    # Alias so that DriverWallet.user_id works (40+ references across codebase + raw SQL)
    user_id = synonym('driver_id')
    balance_cents = Column(Integer, nullable=False, default=0)
    pending_balance_cents = Column(Integer, nullable=False, default=0)
    stripe_account_id = Column(String(255), nullable=True)
    stripe_account_status = Column(String(50), nullable=True)  # restricted, pending, enabled
    stripe_onboarding_complete = Column(Boolean, nullable=False, default=False)
    total_earned_cents = Column(Integer, nullable=False, default=0)
    total_withdrawn_cents = Column(Integer, nullable=False, default=0)
    nova_balance = Column(Integer, nullable=False, default=0)
    energy_reputation_score = Column(Integer, nullable=False, default=0)
    # Dual-provider support
    payout_provider = Column(String(20), nullable=False, default="stripe", server_default="stripe")  # "stripe" or "dwolla"
    external_account_id = Column(String(500), nullable=True)  # Dwolla customer URL
    bank_verified = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    # Relationships
    driver = relationship("User", foreign_keys=[driver_id], backref="driver_wallet")
    payouts = relationship("Payout", back_populates="wallet")
    ledger_entries = relationship("WalletLedger", back_populates="wallet")

    __table_args__ = (
        CheckConstraint('balance_cents >= 0', name='ck_wallet_balance_non_negative'),
    )


class Payout(Base):
    """Payout record for driver withdrawals"""
    __tablename__ = "payouts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wallet_id = Column(String(36), ForeignKey("driver_wallets.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    stripe_transfer_id = Column(String(255), nullable=True)
    stripe_payout_id = Column(String(255), nullable=True)
    # Dual-provider support
    payout_provider = Column(String(20), nullable=False, default="stripe", server_default="stripe")
    external_transfer_id = Column(String(500), nullable=True)  # Dwolla transfer URL
    funding_source_id = Column(String(36), nullable=True)  # FK to funding_sources
    status = Column(String(20), nullable=False, default="pending")  # pending, processing, paid, failed
    failure_reason = Column(String(500), nullable=True)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    # Relationships
    driver = relationship("User", backref="payouts")
    wallet = relationship("DriverWallet", back_populates="payouts")


class WalletLedger(Base):
    """Transaction ledger for wallet balance changes"""
    __tablename__ = "wallet_ledger"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    wallet_id = Column(String(36), ForeignKey("driver_wallets.id"), nullable=False)
    driver_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False)  # Positive = credit, negative = debit
    balance_after_cents = Column(Integer, nullable=False)
    transaction_type = Column(String(30), nullable=False)  # credit, debit, withdrawal, reversal
    reference_type = Column(String(30), nullable=True)  # clo_reward, payout, reversal, bonus
    reference_id = Column(String(36), nullable=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    wallet = relationship("DriverWallet", back_populates="ledger_entries")
