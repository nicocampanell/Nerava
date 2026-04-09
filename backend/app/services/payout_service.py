"""
Stripe Payout Service - Driver Express Account Payouts

Production-ready skeleton with mock mode for development.
Set STRIPE_SECRET_KEY and ENABLE_STRIPE_PAYOUTS=true for production.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Feature flags from environment
ENABLE_STRIPE_PAYOUTS = os.getenv("ENABLE_STRIPE_PAYOUTS", "false").lower() == "true"
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv(
    "STRIPE_PAYOUT_WEBHOOK_SECRET", os.getenv("STRIPE_WEBHOOK_SECRET", "")
)

# Business rules
MINIMUM_WITHDRAWAL_CENTS = 100  # $1 minimum
FEE_THRESHOLD_CENTS = 2000  # $20 — withdrawals below this incur the Stripe fee
STRIPE_FIXED_FEE_CENTS = 25  # $0.25 per transfer
STRIPE_PERCENT_FEE = 0.0025  # 0.25% per transfer
WEEKLY_WITHDRAWAL_LIMIT_CENTS = 100000  # $1000/week fraud cap
MAX_DAILY_WITHDRAWALS = 3


def calculate_withdrawal_fee(amount_cents: int) -> int:
    """Calculate Stripe fee for withdrawals under $20. Returns fee in cents."""
    if amount_cents >= FEE_THRESHOLD_CENTS:
        return 0
    fee = STRIPE_FIXED_FEE_CENTS + int(amount_cents * STRIPE_PERCENT_FEE)
    return fee


# Initialize Stripe if key is available
stripe = None
if STRIPE_SECRET_KEY:
    try:
        import stripe as stripe_module

        stripe = stripe_module
        stripe.api_key = STRIPE_SECRET_KEY
        logger.info("Stripe payout service initialized with live key")
    except ImportError:
        logger.warning("Stripe module not installed, payouts will run in mock mode")


def _is_mock_mode() -> bool:
    """Check if we should run in mock mode"""
    return not ENABLE_STRIPE_PAYOUTS or not stripe or not STRIPE_SECRET_KEY


class PayoutService:
    """Service for handling driver payouts via Stripe Express"""

    @staticmethod
    def get_or_create_wallet(db: Session, driver_id: int) -> Dict[str, Any]:
        """Get or create a driver wallet"""
        from ..models.driver_wallet import DriverWallet

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()

        if not wallet:
            wallet = DriverWallet(
                id=str(uuid.uuid4()),
                driver_id=driver_id,
                balance_cents=0,
                pending_balance_cents=0,
            )
            db.add(wallet)
            db.commit()
            db.refresh(wallet)
            logger.info(f"Created wallet for driver {driver_id}")

        # Always report Stripe as provider when Dwolla is disabled
        dwolla_enabled = os.getenv("ENABLE_DWOLLA_PAYOUTS", "false").lower() == "true"
        wallet_provider = getattr(wallet, "payout_provider", "stripe") or "stripe"
        effective_provider = (
            wallet_provider if (wallet_provider == "dwolla" and dwolla_enabled) else "stripe"
        )

        return {
            "wallet_id": wallet.id,
            "balance_cents": wallet.balance_cents,
            "pending_balance_cents": wallet.pending_balance_cents,
            "available_cents": wallet.balance_cents,
            "stripe_account_id": wallet.stripe_account_id,
            "stripe_onboarding_complete": wallet.stripe_onboarding_complete,
            "total_earned_cents": wallet.total_earned_cents,
            "total_withdrawn_cents": wallet.total_withdrawn_cents,
            "payout_provider": effective_provider,
            "bank_verified": getattr(wallet, "bank_verified", False),
        }

    @staticmethod
    def get_balance(db: Session, driver_id: int) -> Dict[str, Any]:
        """Get driver wallet balance"""
        wallet_data = PayoutService.get_or_create_wallet(db, driver_id)
        bal = wallet_data["balance_cents"]
        return {
            "available_cents": bal,
            "pending_cents": wallet_data["pending_balance_cents"],
            "total_earned_cents": wallet_data["total_earned_cents"],
            "total_withdrawn_cents": wallet_data["total_withdrawn_cents"],
            "can_withdraw": bal >= MINIMUM_WITHDRAWAL_CENTS,
            "minimum_withdrawal_cents": MINIMUM_WITHDRAWAL_CENTS,
            "fee_threshold_cents": FEE_THRESHOLD_CENTS,
            "stripe_onboarding_complete": wallet_data["stripe_onboarding_complete"],
        }

    @staticmethod
    def credit_wallet(
        db: Session,
        driver_id: int,
        amount_cents: int,
        reference_type: str,
        reference_id: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Credit a driver's wallet (e.g., from CLO reward)"""
        from ..models.driver_wallet import DriverWallet, WalletLedger

        wallet = (
            db.query(DriverWallet)
            .filter(DriverWallet.driver_id == driver_id)
            .with_for_update()
            .first()
        )
        if not wallet:
            wallet_data = PayoutService.get_or_create_wallet(db, driver_id)
            wallet = (
                db.query(DriverWallet)
                .filter(DriverWallet.driver_id == driver_id)
                .with_for_update()
                .first()
            )

        # Update balance (row locked via with_for_update to prevent lost updates)
        wallet.balance_cents += amount_cents
        wallet.total_earned_cents += amount_cents
        wallet.updated_at = datetime.utcnow()

        # Create ledger entry
        ledger = WalletLedger(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            driver_id=driver_id,
            amount_cents=amount_cents,
            balance_after_cents=wallet.balance_cents,
            transaction_type="credit",
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
        )
        db.add(ledger)
        db.commit()

        logger.info(
            f"Credited {amount_cents} cents to driver {driver_id} wallet for {reference_type}"
        )
        return {"new_balance_cents": wallet.balance_cents, "ledger_id": ledger.id}

    @staticmethod
    def create_express_account(db: Session, driver_id: int, email: str) -> Dict[str, Any]:
        """Create or retrieve Stripe Express account for driver"""
        from ..models.driver_wallet import DriverWallet

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()
        if not wallet:
            wallet_data = PayoutService.get_or_create_wallet(db, driver_id)
            wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()

        if wallet.stripe_account_id:
            return {
                "stripe_account_id": wallet.stripe_account_id,
                "status": "existing",
                "onboarding_complete": wallet.stripe_onboarding_complete,
            }

        if _is_mock_mode():
            # Mock mode: generate fake account ID
            mock_account_id = f"acct_mock_{uuid.uuid4().hex[:16]}"
            wallet.stripe_account_id = mock_account_id
            wallet.stripe_account_status = "enabled"
            wallet.stripe_onboarding_complete = True
            wallet.updated_at = datetime.utcnow()
            db.commit()
            logger.info(
                f"[MOCK] Created mock Stripe account {mock_account_id} for driver {driver_id}"
            )
            return {
                "stripe_account_id": mock_account_id,
                "status": "mock_created",
                "onboarding_complete": True,
            }

        # Production: Create real Stripe Express account
        try:
            account_params = {
                "type": "express",
                "country": "US",
                "business_type": "individual",
                "capabilities": {
                    "transfers": {"requested": True},
                    "card_payments": {"requested": True},
                },
                "settings": {
                    "payouts": {
                        "debit_negative_balances": True,
                    },
                },
                "business_profile": {
                    "product_description": "EV charging rewards recipient",
                    "mcc": "7299",  # Miscellaneous recreation services
                    "url": "https://nerava.network",
                },
                "metadata": {
                    "driver_id": str(driver_id),
                    "platform": "nerava",
                },
            }
            if email:
                account_params["email"] = email
            account = stripe.Account.create(**account_params)
            wallet.stripe_account_id = account.id
            wallet.stripe_account_status = "restricted"
            wallet.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Created Stripe Express account {account.id} for driver {driver_id}")
            return {
                "stripe_account_id": account.id,
                "status": "created",
                "onboarding_complete": False,
            }
        except Exception as e:
            logger.error(f"Failed to create Stripe account for driver {driver_id}: {e}")
            raise ValueError(f"Failed to create payout account: {str(e)}")

    @staticmethod
    def create_account_link(
        db: Session, driver_id: int, return_url: str, refresh_url: str
    ) -> Dict[str, Any]:
        """Create Stripe account onboarding link"""
        from ..models.driver_wallet import DriverWallet

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()
        if not wallet or not wallet.stripe_account_id:
            raise ValueError("Driver must have a Stripe account first")

        if _is_mock_mode():
            # Mock mode: return fake onboarding URL
            return {
                "url": f"{return_url}?mock_onboarding=complete",
                "expires_at": datetime.utcnow().isoformat(),
            }

        try:
            account_link = stripe.AccountLink.create(
                account=wallet.stripe_account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding",
            )
            return {
                "url": account_link.url,
                "expires_at": datetime.fromtimestamp(account_link.expires_at).isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to create account link for driver {driver_id}: {e}")
            raise ValueError(f"Failed to create onboarding link: {str(e)}")

    @staticmethod
    def check_stripe_onboarding_status(db: Session, driver_id: int) -> Dict[str, Any]:
        """Check Stripe account status by calling Stripe API directly"""
        from ..models.driver_wallet import DriverWallet

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()
        if not wallet or not wallet.stripe_account_id:
            return {"onboarding_complete": False, "has_account": False}

        if _is_mock_mode():
            return {
                "onboarding_complete": wallet.stripe_onboarding_complete,
                "has_account": True,
            }

        try:
            account = stripe.Account.retrieve(wallet.stripe_account_id)
            capabilities = account.get("capabilities", {})
            transfers_active = capabilities.get("transfers") == "active"

            if transfers_active and not wallet.stripe_onboarding_complete:
                wallet.stripe_onboarding_complete = True
                wallet.stripe_account_status = "enabled"
                wallet.updated_at = datetime.utcnow()
                db.commit()
                logger.info(f"Stripe onboarding confirmed complete for driver {driver_id}")

            return {
                "onboarding_complete": transfers_active,
                "has_account": True,
                "details_submitted": account.get("details_submitted", False),
                "charges_enabled": account.get("charges_enabled", False),
            }
        except Exception as e:
            logger.error(f"Failed to check Stripe status for driver {driver_id}: {e}")
            return {
                "onboarding_complete": wallet.stripe_onboarding_complete,
                "has_account": True,
            }

    @staticmethod
    def check_withdrawal_eligibility(
        db: Session, driver_id: int, amount_cents: int
    ) -> Tuple[bool, str]:
        """Check if driver is eligible for withdrawal"""
        from ..models.driver_wallet import DriverWallet, Payout

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()
        if not wallet:
            return False, "No wallet found"

        # Check minimum
        if amount_cents < MINIMUM_WITHDRAWAL_CENTS:
            return False, f"Minimum withdrawal is ${MINIMUM_WITHDRAWAL_CENTS / 100:.2f}"

        # Check balance (must cover amount + fee)
        fee = calculate_withdrawal_fee(amount_cents)
        if wallet.balance_cents < amount_cents:
            return False, "Insufficient balance"
        if fee > 0 and wallet.balance_cents < amount_cents + fee:
            return False, f"Insufficient balance to cover ${fee / 100:.2f} processing fee"

        # Check payout account based on active provider
        # When Dwolla is disabled, always check Stripe regardless of wallet.payout_provider
        dwolla_enabled = os.getenv("ENABLE_DWOLLA_PAYOUTS", "false").lower() == "true"
        provider = getattr(wallet, "payout_provider", "stripe") or "stripe"
        if provider == "dwolla" and dwolla_enabled:
            if not getattr(wallet, "external_account_id", None):
                return False, "Payout account not set up"
            if not getattr(wallet, "bank_verified", False) and not _is_mock_mode():
                return False, "Bank account not linked"
        else:
            if not wallet.stripe_account_id:
                return False, "Payout account not set up"
            if not wallet.stripe_onboarding_complete and not _is_mock_mode():
                return False, "Payout account onboarding not complete"

        # Check daily withdrawal limit (fraud prevention)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        daily_withdrawals = (
            db.query(Payout)
            .filter(
                Payout.driver_id == driver_id,
                Payout.created_at >= today_start,
                Payout.status.in_(["pending", "processing", "paid"]),
            )
            .count()
        )
        if daily_withdrawals >= MAX_DAILY_WITHDRAWALS:
            return False, f"Maximum {MAX_DAILY_WITHDRAWALS} withdrawals per day"

        # Check weekly limit (fraud prevention)
        from datetime import timedelta

        week_start = datetime.utcnow() - timedelta(days=7)
        weekly_total = (
            db.query(Payout)
            .filter(
                Payout.driver_id == driver_id,
                Payout.created_at >= week_start,
                Payout.status.in_(["pending", "processing", "paid"]),
            )
            .with_entities(func.sum(Payout.amount_cents))
            .scalar()
            or 0
        )

        if weekly_total + amount_cents > WEEKLY_WITHDRAWAL_LIMIT_CENTS:
            return (
                False,
                f"Weekly withdrawal limit of ${WEEKLY_WITHDRAWAL_LIMIT_CENTS / 100:.2f} exceeded",
            )

        return True, "Eligible"

    @staticmethod
    def request_withdrawal(db: Session, driver_id: int, amount_cents: int) -> Dict[str, Any]:
        """Request a withdrawal (creates payout, moves funds to pending).

        For withdrawals under $20, the Stripe processing fee ($0.25 + 0.25%)
        is deducted from the driver's balance. The full requested amount is
        still transferred to the driver's bank.
        """
        from ..models.driver_wallet import DriverWallet, Payout, WalletLedger

        # Pre-check eligibility (non-locked, fast-fail for obvious issues)
        eligible, reason = PayoutService.check_withdrawal_eligibility(db, driver_id, amount_cents)
        if not eligible:
            raise ValueError(reason)

        fee_cents = calculate_withdrawal_fee(amount_cents)
        total_debit = amount_cents + fee_cents

        wallet = (
            db.query(DriverWallet)
            .filter(DriverWallet.driver_id == driver_id)
            .with_for_update()
            .first()
        )

        # Re-validate balance after acquiring row lock (prevents race condition)
        if wallet.balance_cents < total_debit:
            raise ValueError("Insufficient balance")

        # Generate idempotency key
        idempotency_key = f"payout_{driver_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # Move funds to pending (amount only — fee is consumed)
        wallet.balance_cents -= total_debit
        wallet.pending_balance_cents += amount_cents
        wallet.updated_at = datetime.utcnow()

        # Create payout record
        payout = Payout(
            id=str(uuid.uuid4()),
            driver_id=driver_id,
            wallet_id=wallet.id,
            amount_cents=amount_cents,
            status="pending",
            idempotency_key=idempotency_key,
        )
        db.add(payout)

        # Create ledger entry for withdrawal request
        ledger = WalletLedger(
            id=str(uuid.uuid4()),
            wallet_id=wallet.id,
            driver_id=driver_id,
            amount_cents=-amount_cents,
            balance_after_cents=wallet.balance_cents,
            transaction_type="withdrawal",
            reference_type="payout",
            reference_id=payout.id,
            description=f"Withdrawal request for ${amount_cents / 100:.2f}",
        )
        db.add(ledger)

        # Create separate fee ledger entry if fee applies
        if fee_cents > 0:
            fee_ledger = WalletLedger(
                id=str(uuid.uuid4()),
                wallet_id=wallet.id,
                driver_id=driver_id,
                amount_cents=-fee_cents,
                balance_after_cents=wallet.balance_cents,
                transaction_type="fee",
                reference_type="payout_fee",
                reference_id=payout.id,
                description=f"Processing fee for ${amount_cents / 100:.2f} withdrawal",
            )
            db.add(fee_ledger)

        db.commit()

        logger.info(
            f"Created payout request {payout.id} for driver {driver_id}: "
            f"${amount_cents / 100:.2f} (fee: ${fee_cents / 100:.2f})"
        )

        # Process the transfer
        result = PayoutService._process_transfer(db, payout)
        result["fee_cents"] = fee_cents
        return result

    @staticmethod
    def _process_transfer(db: Session, payout) -> Dict[str, Any]:
        """Process the transfer via the appropriate provider (Stripe or Dwolla)."""
        from ..models.driver_wallet import DriverWallet
        from .payout_provider import get_provider

        wallet = db.query(DriverWallet).filter(DriverWallet.id == payout.wallet_id).first()
        # Always route to Stripe unless Dwolla is explicitly enabled
        provider_name = getattr(wallet, "payout_provider", "stripe") or "stripe"
        provider = get_provider(provider_name)  # get_provider gates Dwolla behind env var

        if _is_mock_mode() and provider_name == "stripe":
            # Mock mode: immediately mark as paid
            payout.status = "paid"
            payout.stripe_transfer_id = f"tr_mock_{uuid.uuid4().hex[:16]}"
            payout.paid_at = datetime.utcnow()
            payout.updated_at = datetime.utcnow()

            # Move from pending to withdrawn
            wallet.pending_balance_cents -= payout.amount_cents
            wallet.total_withdrawn_cents += payout.amount_cents
            wallet.updated_at = datetime.utcnow()

            db.commit()
            logger.info(f"[MOCK] Processed payout {payout.id}: ${payout.amount_cents / 100:.2f}")
            return {
                "payout_id": payout.id,
                "status": "paid",
                "amount_cents": payout.amount_cents,
                "mock": True,
            }

        # Production: Create transfer via provider
        try:
            payout.status = "processing"
            payout.payout_provider = provider_name
            payout.updated_at = datetime.utcnow()
            db.commit()

            # Determine account ID based on provider
            if provider_name == "dwolla":
                account_id = wallet.external_account_id
                # Get default funding source for Dwolla transfers
                from ..models.funding_source import FundingSource

                funding_source = (
                    db.query(FundingSource)
                    .filter(
                        FundingSource.user_id == wallet.driver_id,
                        FundingSource.removed_at.is_(None),
                        FundingSource.is_default == True,
                    )
                    .first()
                )
                funding_source_url = funding_source.external_id if funding_source else None
                transfer_id = provider.create_transfer(
                    account_id,
                    payout.amount_cents,
                    payout.idempotency_key,
                    funding_source_url=funding_source_url,
                    driver_id=str(payout.driver_id),
                    metadata={"payout_id": payout.id, "driver_id": str(payout.driver_id)},
                )
                payout.external_transfer_id = transfer_id
                if funding_source:
                    payout.funding_source_id = str(funding_source.id)
            else:
                account_id = wallet.stripe_account_id
                transfer_id = provider.create_transfer(
                    account_id,
                    payout.amount_cents,
                    payout.idempotency_key,
                    metadata={"payout_id": payout.id, "driver_id": str(payout.driver_id)},
                )
                payout.stripe_transfer_id = transfer_id

            payout.updated_at = datetime.utcnow()
            db.commit()

            logger.info(f"Created {provider_name} transfer {transfer_id} for payout {payout.id}")
            return {
                "payout_id": payout.id,
                "status": "processing",
                "transfer_id": transfer_id,
                "amount_cents": payout.amount_cents,
            }
        except Exception as e:
            # Handle failure
            payout.status = "failed"
            payout.failure_reason = str(e)
            payout.updated_at = datetime.utcnow()

            # Revert funds from pending to available (amount + fee)
            fee_cents = getattr(payout, "_fee_cents", 0) or calculate_withdrawal_fee(
                payout.amount_cents
            )
            wallet.pending_balance_cents -= payout.amount_cents
            wallet.balance_cents += payout.amount_cents + fee_cents
            wallet.updated_at = datetime.utcnow()

            db.commit()
            logger.error(f"Failed to process payout {payout.id}: {e}")
            raise ValueError(f"Payout failed: {str(e)}")

    @staticmethod
    def handle_webhook(db: Session, payload: bytes, signature: str) -> Dict[str, Any]:
        """Handle Stripe webhook events for payouts"""

        if _is_mock_mode():
            return {"status": "ignored", "reason": "mock_mode"}

        if not STRIPE_WEBHOOK_SECRET:
            logger.warning("Stripe webhook secret not configured")
            return {"status": "error", "reason": "webhook_secret_not_configured"}

        try:
            event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            raise ValueError(f"Invalid webhook signature: {e}")

        event_type = event["type"]
        data = event["data"]["object"]

        if event_type in ("transfer.paid", "payout.paid"):
            return PayoutService._handle_transfer_paid(db, data)
        elif event_type in ("transfer.failed", "payout.failed"):
            return PayoutService._handle_transfer_failed(db, data)
        elif event_type == "account.updated":
            return PayoutService._handle_account_updated(db, data)
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            return {"status": "ignored", "event_type": event_type}

    @staticmethod
    def _handle_transfer_paid(db: Session, transfer_data: dict) -> Dict[str, Any]:
        """Handle transfer.paid webhook"""
        from ..models.driver_wallet import DriverWallet, Payout

        transfer_id = transfer_data["id"]
        payout = db.query(Payout).filter(Payout.stripe_transfer_id == transfer_id).first()

        if not payout:
            logger.warning(f"No payout found for transfer {transfer_id}")
            return {"status": "ignored", "reason": "payout_not_found"}

        if payout.status == "paid":
            return {"status": "already_processed", "payout_id": payout.id}

        wallet = db.query(DriverWallet).filter(DriverWallet.id == payout.wallet_id).first()

        payout.status = "paid"
        payout.paid_at = datetime.utcnow()
        payout.updated_at = datetime.utcnow()

        wallet.pending_balance_cents -= payout.amount_cents
        wallet.total_withdrawn_cents += payout.amount_cents
        wallet.updated_at = datetime.utcnow()

        db.commit()
        logger.info(f"Payout {payout.id} marked as paid via webhook")

        # Send push notification for payout complete (best-effort)
        try:
            from app.services.push_service import send_payout_complete_push

            send_payout_complete_push(db, payout.driver_id, payout.amount_cents)
        except Exception as push_err:
            logger.debug("Push notification failed (non-fatal): %s", push_err)

        return {"status": "success", "payout_id": payout.id, "action": "marked_paid"}

    @staticmethod
    def _handle_transfer_failed(db: Session, transfer_data: dict) -> Dict[str, Any]:
        """Handle transfer.failed webhook"""
        from ..models.driver_wallet import DriverWallet, Payout

        transfer_id = transfer_data["id"]
        payout = db.query(Payout).filter(Payout.stripe_transfer_id == transfer_id).first()

        if not payout:
            return {"status": "ignored", "reason": "payout_not_found"}

        if payout.status == "failed":
            return {"status": "already_processed", "payout_id": payout.id}

        wallet = db.query(DriverWallet).filter(DriverWallet.id == payout.wallet_id).first()

        # Revert funds (amount + fee that was deducted on request)
        payout.status = "failed"
        payout.failure_reason = transfer_data.get("failure_message", "Unknown failure")
        payout.updated_at = datetime.utcnow()

        fee_cents = calculate_withdrawal_fee(payout.amount_cents)
        wallet.pending_balance_cents -= payout.amount_cents
        wallet.balance_cents += payout.amount_cents + fee_cents
        wallet.updated_at = datetime.utcnow()

        db.commit()
        logger.warning(f"Payout {payout.id} failed via webhook: {payout.failure_reason}")
        return {"status": "success", "payout_id": payout.id, "action": "marked_failed"}

    @staticmethod
    def _handle_account_updated(db: Session, account_data: dict) -> Dict[str, Any]:
        """Handle account.updated webhook (onboarding completion)"""
        from ..models.driver_wallet import DriverWallet

        account_id = account_data["id"]
        wallet = db.query(DriverWallet).filter(DriverWallet.stripe_account_id == account_id).first()

        if not wallet:
            return {"status": "ignored", "reason": "wallet_not_found"}

        # Check if transfers capability is enabled
        capabilities = account_data.get("capabilities", {})
        transfers_active = capabilities.get("transfers") == "active"

        if transfers_active and not wallet.stripe_onboarding_complete:
            wallet.stripe_onboarding_complete = True
            wallet.stripe_account_status = "enabled"
            wallet.updated_at = datetime.utcnow()
            db.commit()
            logger.info(f"Driver wallet {wallet.id} onboarding completed")
            return {"status": "success", "wallet_id": wallet.id, "action": "onboarding_complete"}

        wallet.stripe_account_status = (
            account_data.get("charges_enabled") and "enabled" or "restricted"
        )
        wallet.updated_at = datetime.utcnow()
        db.commit()
        return {"status": "success", "wallet_id": wallet.id, "action": "status_updated"}

    @staticmethod
    def get_wallet_payout_provider(db: Session, driver_id: int) -> str:
        """Get the payout provider for a driver's wallet"""
        from ..models.driver_wallet import DriverWallet

        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver_id).first()
        if wallet:
            return getattr(wallet, "payout_provider", "stripe") or "stripe"
        return "stripe"

    @staticmethod
    def get_payout_history(db: Session, driver_id: int, limit: int = 20) -> list:
        """Get driver's payout history"""
        from ..models.driver_wallet import Payout

        payouts = (
            db.query(Payout)
            .filter(Payout.driver_id == driver_id)
            .order_by(Payout.created_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "id": p.id,
                "amount_cents": p.amount_cents,
                "status": p.status,
                "created_at": p.created_at.isoformat(),
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "failure_reason": p.failure_reason,
            }
            for p in payouts
        ]


def credit_wallet(db: Session, driver_id: int, amount_cents: int, description: str = ""):
    """Module-level convenience function to credit a driver's wallet."""
    return PayoutService.credit_wallet(
        db,
        driver_id,
        amount_cents,
        reference_type="referral",
        reference_id=f"referral_{driver_id}",
        description=description,
    )
