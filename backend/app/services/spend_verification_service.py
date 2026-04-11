"""
Spend Verification Service - Fidel CLO Integration

Production-ready skeleton with mock mode for development.
Set FIDEL_SECRET_KEY and ENABLE_CLO=true for production.
"""
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Feature flags from environment
ENABLE_CLO = os.getenv("ENABLE_CLO", "false").lower() == "true"
FIDEL_SECRET_KEY = os.getenv("FIDEL_SECRET_KEY", "")
FIDEL_PROGRAM_ID = os.getenv("FIDEL_PROGRAM_ID", "")
FIDEL_WEBHOOK_SECRET = os.getenv("FIDEL_WEBHOOK_SECRET", "")

# Business rules
TRANSACTION_TIME_WINDOW_MINUTES = 180  # 3 hours - transaction must be within this window of charging session
MIN_ELIGIBLE_SPEND_CENTS = 500  # $5 minimum spend for any reward

# Initialize Fidel if key is available
fidel_client = None
if FIDEL_SECRET_KEY:
    logger.info("Fidel CLO service initialized with live key")


def _is_mock_mode() -> bool:
    """Check if we should run in mock mode"""
    return not ENABLE_CLO or not FIDEL_SECRET_KEY


class SpendVerificationService:
    """Service for verifying card-linked spend and processing rewards"""

    @staticmethod
    def link_card(
        db: Session,
        driver_id: int,
        card_number: str,  # In production, this would be tokenized
        expiry_month: int,
        expiry_year: int,
        cvv: str,
        country_code: str = "USA",
    ) -> Dict[str, Any]:
        """Link a card for CLO tracking"""
        from ..models.clo import Card

        # Extract last4 and brand (simplified)
        last4 = card_number[-4:]
        brand = SpendVerificationService._detect_card_brand(card_number)

        # Check for duplicate
        existing = db.query(Card).filter(
            Card.driver_id == driver_id,
            Card.last4 == last4,
            Card.brand == brand,
            Card.is_active == True,
        ).first()

        if existing:
            return {
                "card_id": existing.id,
                "status": "already_linked",
                "last4": last4,
                "brand": brand,
            }

        if _is_mock_mode():
            # Mock mode: create card without Fidel
            card = Card(
                id=str(uuid.uuid4()),
                driver_id=driver_id,
                fidel_card_id=f"fidel_mock_{uuid.uuid4().hex[:16]}",
                last4=last4,
                brand=brand,
                fingerprint=f"fp_{uuid.uuid4().hex[:16]}",
                is_active=True,
                linked_at=datetime.utcnow(),
            )
            db.add(card)
            db.commit()
            logger.info(f"[MOCK] Linked card {card.id} for driver {driver_id}")
            return {
                "card_id": card.id,
                "status": "linked",
                "last4": last4,
                "brand": brand,
                "mock": True,
            }

        # Production: Call Fidel API to enroll card
        try:
            # In production, implement actual Fidel card enrollment
            # fidel_response = fidel_client.cards.create(...)
            raise NotImplementedError("Fidel card enrollment not yet implemented")
        except Exception as e:
            logger.error(f"Failed to link card for driver {driver_id}: {e}")
            raise ValueError(f"Failed to link card: {str(e)}")

    @staticmethod
    def _detect_card_brand(card_number: str) -> str:
        """Detect card brand from number (simplified)"""
        first_digit = card_number[0] if card_number else ""
        first_two = card_number[:2] if len(card_number) >= 2 else ""

        if first_digit == "4":
            return "visa"
        elif first_two in ["51", "52", "53", "54", "55"]:
            return "mastercard"
        elif first_two in ["34", "37"]:
            return "amex"
        elif first_two == "60":
            return "discover"
        return "unknown"

    @staticmethod
    def get_linked_cards(db: Session, driver_id: int) -> List[Dict[str, Any]]:
        """Get driver's linked cards"""
        from ..models.clo import Card

        cards = db.query(Card).filter(
            Card.driver_id == driver_id,
            Card.is_active == True,
        ).all()

        return [
            {
                "id": c.id,
                "last4": c.last4,
                "brand": c.brand,
                "linked_at": c.linked_at.isoformat() if c.linked_at else None,
            }
            for c in cards
        ]

    @staticmethod
    def unlink_card(db: Session, driver_id: int, card_id: str) -> Dict[str, Any]:
        """Unlink a card"""
        from ..models.clo import Card

        card = db.query(Card).filter(
            Card.id == card_id,
            Card.driver_id == driver_id,
        ).first()

        if not card:
            raise ValueError("Card not found")

        card.is_active = False
        db.commit()

        if not _is_mock_mode() and card.fidel_card_id:
            # Production: Call Fidel to remove card
            # fidel_client.cards.delete(card.fidel_card_id)
            pass

        logger.info(f"Unlinked card {card_id} for driver {driver_id}")
        return {"status": "unlinked", "card_id": card_id}

    @staticmethod
    def verify_transaction(
        db: Session,
        driver_id: int,
        transaction_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Verify a transaction for CLO eligibility.

        In production, this is called from Fidel webhook.
        In mock mode, can be called directly for testing.
        """
        from ..models.clo import Card, CLOTransaction, MerchantOffer
        from ..services.payout_service import PayoutService

        external_id = transaction_payload.get("external_id", str(uuid.uuid4()))
        card_id = transaction_payload.get("card_id")
        amount_cents = transaction_payload.get("amount_cents", 0)
        merchant_id = transaction_payload.get("merchant_id")
        merchant_name = transaction_payload.get("merchant_name", "Unknown")
        transaction_time_str = transaction_payload.get("transaction_time")
        charging_session_id = transaction_payload.get("charging_session_id")

        # Parse transaction time
        if transaction_time_str:
            transaction_time = datetime.fromisoformat(transaction_time_str.replace("Z", "+00:00"))
        else:
            transaction_time = datetime.utcnow()

        # Get card
        card = db.query(Card).filter(
            Card.id == card_id,
            Card.driver_id == driver_id,
            Card.is_active == True,
        ).first()

        if not card:
            # In webhook flow, look up by fidel_card_id
            fidel_card_id = transaction_payload.get("fidel_card_id")
            if fidel_card_id:
                card = db.query(Card).filter(
                    Card.fidel_card_id == fidel_card_id,
                    Card.is_active == True,
                ).first()

        if not card:
            return {"status": "rejected", "reason": "card_not_found"}

        # Check for duplicate
        existing = db.query(CLOTransaction).filter(
            CLOTransaction.external_id == external_id
        ).first()
        if existing:
            return {"status": "duplicate", "transaction_id": existing.id}

        # Get applicable offer
        offer = db.query(MerchantOffer).filter(
            MerchantOffer.merchant_id == merchant_id,
            MerchantOffer.is_active == True,
        ).first()

        # Check eligibility
        eligibility_result = SpendVerificationService._check_eligibility(
            db, driver_id, card, amount_cents, merchant_id, transaction_time, charging_session_id, offer
        )

        # Create transaction record
        transaction = CLOTransaction(
            id=str(uuid.uuid4()),
            driver_id=driver_id,
            card_id=card.id,
            merchant_id=merchant_id,
            offer_id=offer.id if offer else None,
            amount_cents=amount_cents,
            status=eligibility_result["status"],
            external_id=external_id,
            charging_session_id=charging_session_id,
            transaction_time=transaction_time,
            merchant_name=merchant_name,
            merchant_location=transaction_payload.get("merchant_location"),
            eligibility_reason=eligibility_result.get("reason"),
        )

        # Calculate reward if eligible
        if eligibility_result["status"] == "eligible" and offer:
            reward_cents = SpendVerificationService._calculate_reward(amount_cents, offer)
            transaction.reward_cents = reward_cents
            transaction.status = "eligible"

            # In mock mode, auto-credit. In production, wait for confirmation
            if _is_mock_mode():
                transaction.status = "credited"
                transaction.processed_at = datetime.utcnow()
                db.add(transaction)
                db.commit()

                # Credit wallet
                PayoutService.credit_wallet(
                    db,
                    driver_id,
                    reward_cents,
                    "clo_reward",
                    transaction.id,
                    f"Reward from {merchant_name}",
                )

                logger.info(f"[MOCK] Credited {reward_cents} cents for transaction {transaction.id}")
                return {
                    "status": "credited",
                    "transaction_id": transaction.id,
                    "reward_cents": reward_cents,
                    "mock": True,
                }

        db.add(transaction)
        db.commit()

        return {
            "status": transaction.status,
            "transaction_id": transaction.id,
            "reward_cents": transaction.reward_cents,
            "reason": eligibility_result.get("reason"),
        }

    @staticmethod
    def _check_eligibility(
        db: Session,
        driver_id: int,
        card,
        amount_cents: int,
        merchant_id: str,
        transaction_time: datetime,
        charging_session_id: Optional[str],
        offer,
    ) -> Dict[str, Any]:
        """Check transaction eligibility for reward"""

        # Check minimum spend
        if amount_cents < MIN_ELIGIBLE_SPEND_CENTS:
            return {"status": "rejected", "reason": f"Below minimum spend of ${MIN_ELIGIBLE_SPEND_CENTS / 100:.2f}"}

        # Check offer exists and is valid
        if not offer:
            return {"status": "rejected", "reason": "No active offer for merchant"}

        if offer.min_spend_cents > 0 and amount_cents < offer.min_spend_cents:
            return {"status": "rejected", "reason": f"Below offer minimum of ${offer.min_spend_cents / 100:.2f}"}

        if offer.valid_from and transaction_time < offer.valid_from:
            return {"status": "rejected", "reason": "Offer not yet valid"}

        if offer.valid_until and transaction_time > offer.valid_until:
            return {"status": "rejected", "reason": "Offer expired"}

        # Check charging session overlap (if required)
        if charging_session_id:
            # Verify session belongs to driver and is within time window
            session_valid = SpendVerificationService._verify_charging_session(
                db, driver_id, charging_session_id, transaction_time
            )
            if not session_valid:
                return {"status": "rejected", "reason": "Transaction not within charging session window"}
        else:
            # In production, might require charging session
            # For now, allow transactions without explicit session
            pass

        return {"status": "eligible", "reason": "All criteria met"}

    @staticmethod
    def _verify_charging_session(
        db: Session,
        driver_id: int,
        session_id: str,
        transaction_time: datetime,
    ) -> bool:
        """Verify charging session is valid and transaction is within time window"""
        # Check intent sessions (arrival sessions)
        from ..models.intent import IntentSession

        session = db.query(IntentSession).filter(
            IntentSession.id == session_id,
        ).first()

        if not session:
            return False

        # Check transaction is within window of session
        session_start = session.created_at
        session_end = session.ended_at or (session_start + timedelta(hours=3))
        window_start = session_start - timedelta(minutes=30)
        window_end = session_end + timedelta(minutes=TRANSACTION_TIME_WINDOW_MINUTES)

        return window_start <= transaction_time <= window_end

    @staticmethod
    def _calculate_reward(amount_cents: int, offer) -> int:
        """Calculate reward amount based on offer rules"""
        if offer.reward_percent:
            reward = int(amount_cents * offer.reward_percent / 100)
            if offer.max_reward_cents:
                reward = min(reward, offer.max_reward_cents)
            return reward
        return offer.reward_cents

    @staticmethod
    def _verify_webhook_signature(payload: Dict[str, Any], signature: str) -> bool:
        """Verify Fidel webhook signature using HMAC-SHA256"""
        if not FIDEL_WEBHOOK_SECRET:
            logger.warning("FIDEL_WEBHOOK_SECRET not configured, skipping verification")
            return True  # Allow in dev when secret not set

        if not signature:
            return False

        # Fidel uses HMAC-SHA256 with JSON payload
        expected = hmac.new(
            FIDEL_WEBHOOK_SECRET.encode(),
            json.dumps(payload, separators=(',', ':'), sort_keys=True).encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    @staticmethod
    def process_webhook(db: Session, payload: Dict[str, Any], signature: str) -> Dict[str, Any]:
        """Process Fidel webhook event"""

        if _is_mock_mode():
            return {"status": "ignored", "reason": "mock_mode"}

        # Verify webhook signature in production
        if not SpendVerificationService._verify_webhook_signature(payload, signature):
            logger.error("Invalid Fidel webhook signature")
            raise ValueError("Invalid webhook signature")

        event_type = payload.get("event")

        if event_type == "transaction.auth":
            return SpendVerificationService._handle_transaction_auth(db, payload)
        elif event_type == "transaction.clearing":
            return SpendVerificationService._handle_transaction_clearing(db, payload)
        elif event_type == "transaction.refund":
            return SpendVerificationService._handle_transaction_refund(db, payload)
        else:
            logger.info(f"Unhandled Fidel webhook event: {event_type}")
            return {"status": "ignored", "event_type": event_type}

    @staticmethod
    def _handle_transaction_auth(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle transaction authorization (real-time)"""
        transaction_data = payload.get("transaction", {})

        # Extract relevant fields
        fidel_card_id = transaction_data.get("card", {}).get("id")
        amount_cents = int(transaction_data.get("amount", 0) * 100)
        merchant_data = transaction_data.get("merchant", {})

        from ..models.clo import Card

        card = db.query(Card).filter(Card.fidel_card_id == fidel_card_id).first()
        if not card:
            return {"status": "ignored", "reason": "card_not_enrolled"}

        # Process transaction
        result = SpendVerificationService.verify_transaction(
            db,
            card.driver_id,
            {
                "external_id": transaction_data.get("id"),
                "fidel_card_id": fidel_card_id,
                "card_id": card.id,
                "amount_cents": amount_cents,
                "merchant_id": merchant_data.get("merchantId"),
                "merchant_name": merchant_data.get("name"),
                "merchant_location": merchant_data.get("address"),
                "transaction_time": transaction_data.get("datetime"),
            },
        )

        return result

    @staticmethod
    def _handle_transaction_clearing(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle transaction clearing (settlement confirmation)"""
        from ..models.clo import CLOTransaction
        from ..services.payout_service import PayoutService

        transaction_data = payload.get("transaction", {})
        external_id = transaction_data.get("id")

        transaction = db.query(CLOTransaction).filter(
            CLOTransaction.external_id == external_id
        ).first()

        if not transaction:
            return {"status": "ignored", "reason": "transaction_not_found"}

        if transaction.status == "eligible" and transaction.reward_cents:
            # Credit wallet now that transaction has cleared
            transaction.status = "credited"
            transaction.processed_at = datetime.utcnow()
            db.commit()

            PayoutService.credit_wallet(
                db,
                transaction.driver_id,
                transaction.reward_cents,
                "clo_reward",
                transaction.id,
                f"Reward from {transaction.merchant_name}",
            )

            logger.info(f"Credited {transaction.reward_cents} cents for cleared transaction {transaction.id}")
            return {"status": "credited", "transaction_id": transaction.id}

        return {"status": "ignored", "reason": "not_eligible_or_already_processed"}

    @staticmethod
    def _handle_transaction_refund(db: Session, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle transaction refund"""
        from ..models.clo import CLOTransaction
        from ..models.driver_wallet import DriverWallet, WalletLedger

        transaction_data = payload.get("transaction", {})
        original_transaction_id = transaction_data.get("originalTransactionId")

        transaction = db.query(CLOTransaction).filter(
            CLOTransaction.external_id == original_transaction_id
        ).first()

        if not transaction:
            return {"status": "ignored", "reason": "original_transaction_not_found"}

        if transaction.status != "credited":
            transaction.status = "refunded"
            db.commit()
            return {"status": "refunded", "transaction_id": transaction.id, "no_clawback": True}

        # Claw back the reward
        if transaction.reward_cents:
            wallet = db.query(DriverWallet).filter(
                DriverWallet.driver_id == transaction.driver_id
            ).first()

            if wallet:
                wallet.balance_cents -= transaction.reward_cents
                wallet.total_earned_cents -= transaction.reward_cents
                wallet.updated_at = datetime.utcnow()

                ledger = WalletLedger(
                    id=str(uuid.uuid4()),
                    wallet_id=wallet.id,
                    driver_id=transaction.driver_id,
                    amount_cents=-transaction.reward_cents,
                    balance_after_cents=wallet.balance_cents,
                    transaction_type="debit",
                    reference_type="clo_refund",
                    reference_id=transaction.id,
                    description=f"Refund clawback for {transaction.merchant_name}",
                )
                db.add(ledger)

        transaction.status = "refunded"
        db.commit()

        logger.info(f"Processed refund for transaction {transaction.id}, clawed back {transaction.reward_cents} cents")
        return {"status": "refunded", "transaction_id": transaction.id, "clawback_cents": transaction.reward_cents}

    @staticmethod
    def get_transaction_history(db: Session, driver_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Get driver's CLO transaction history"""
        from ..models.clo import CLOTransaction

        transactions = db.query(CLOTransaction).filter(
            CLOTransaction.driver_id == driver_id
        ).order_by(CLOTransaction.created_at.desc()).limit(limit).all()

        return [
            {
                "id": t.id,
                "merchant_name": t.merchant_name,
                "amount_cents": t.amount_cents,
                "reward_cents": t.reward_cents,
                "status": t.status,
                "transaction_time": t.transaction_time.isoformat(),
                "eligibility_reason": t.eligibility_reason,
            }
            for t in transactions
        ]

    @staticmethod
    def create_card_enrollment_session(db: Session, driver_id: int) -> Dict[str, Any]:
        """Create a secure card enrollment session (Fidel Select SDK)"""
        if _is_mock_mode():
            return {
                "session_token": f"mock_session_{uuid.uuid4().hex}",
                "program_id": "mock_program",
                "mock": True,
            }

        # In production, call Fidel to create enrollment session
        # session = fidel_client.enrollment_sessions.create(program_id=FIDEL_PROGRAM_ID)
        raise NotImplementedError("Fidel enrollment session not yet implemented")

    @staticmethod
    def create_merchant_offer(
        db: Session,
        merchant_id: str,
        min_spend_cents: int,
        reward_cents: int,
        reward_percent: Optional[int] = None,
        max_reward_cents: Optional[int] = None,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Create a CLO offer for a merchant (admin function)"""
        from ..models.clo import MerchantOffer

        offer = MerchantOffer(
            id=str(uuid.uuid4()),
            merchant_id=merchant_id,
            min_spend_cents=min_spend_cents,
            reward_cents=reward_cents,
            reward_percent=reward_percent,
            max_reward_cents=max_reward_cents,
            valid_from=valid_from,
            valid_until=valid_until,
            is_active=True,
        )
        db.add(offer)
        db.commit()

        logger.info(f"Created offer {offer.id} for merchant {merchant_id}")
        return {
            "offer_id": offer.id,
            "merchant_id": merchant_id,
            "min_spend_cents": min_spend_cents,
            "reward_cents": reward_cents,
        }
