"""
Dwolla Webhook Service — handles ACH transfer lifecycle events.

Events handled:
- transfer_completed → mark payout "paid", move pending → withdrawn
- transfer_failed / transfer_cancelled → mark "failed", revert funds to balance
"""
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.driver_wallet import DriverWallet, Payout

logger = logging.getLogger(__name__)


class DwollaWebhookService:
    """Processes Dwolla webhook events for payout lifecycle."""

    @staticmethod
    def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
        """Verify Dwolla webhook HMAC-SHA256 signature."""
        if not secret or not signature:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def handle_event(db: Session, topic: str, resource_url: str) -> Dict[str, Any]:
        """
        Route a Dwolla webhook event to the appropriate handler.

        Args:
            db: Database session
            topic: Dwolla event topic (e.g. "transfer_completed")
            resource_url: URL of the resource (e.g. transfer URL)
        """
        if topic == "transfer_completed":
            return DwollaWebhookService._handle_transfer_completed(db, resource_url)
        elif topic in ("transfer_failed", "transfer_cancelled"):
            return DwollaWebhookService._handle_transfer_failed(db, resource_url, topic)
        else:
            logger.info(f"Unhandled Dwolla webhook topic: {topic}")
            return {"status": "ignored", "topic": topic}

    @staticmethod
    def _handle_transfer_completed(db: Session, transfer_url: str) -> Dict[str, Any]:
        """Handle transfer_completed: mark payout paid, update wallet."""
        payout = db.query(Payout).filter(
            Payout.external_transfer_id == transfer_url
        ).first()

        if not payout:
            logger.warning(f"No payout found for Dwolla transfer {transfer_url}")
            return {"status": "ignored", "reason": "payout_not_found"}

        if payout.status == "paid":
            return {"status": "already_processed", "payout_id": payout.id}

        wallet = db.query(DriverWallet).filter(
            DriverWallet.id == payout.wallet_id
        ).first()

        payout.status = "paid"
        payout.paid_at = datetime.utcnow()
        payout.updated_at = datetime.utcnow()

        if wallet:
            wallet.pending_balance_cents -= payout.amount_cents
            wallet.total_withdrawn_cents += payout.amount_cents
            wallet.updated_at = datetime.utcnow()

        db.commit()
        logger.info(f"Dwolla payout {payout.id} marked as paid (transfer: {transfer_url})")

        # Send push notification (best-effort)
        try:
            from app.services.push_service import send_payout_complete_push
            send_payout_complete_push(db, payout.driver_id, payout.amount_cents)
        except Exception as push_err:
            logger.debug("Push notification failed (non-fatal): %s", push_err)

        return {"status": "success", "payout_id": payout.id, "action": "marked_paid"}

    @staticmethod
    def _handle_transfer_failed(
        db: Session, transfer_url: str, topic: str
    ) -> Dict[str, Any]:
        """Handle transfer_failed / transfer_cancelled: revert funds."""
        payout = db.query(Payout).filter(
            Payout.external_transfer_id == transfer_url
        ).first()

        if not payout:
            return {"status": "ignored", "reason": "payout_not_found"}

        if payout.status == "failed":
            return {"status": "already_processed", "payout_id": payout.id}

        wallet = db.query(DriverWallet).filter(
            DriverWallet.id == payout.wallet_id
        ).first()

        payout.status = "failed"
        payout.failure_reason = f"Dwolla {topic}"
        payout.updated_at = datetime.utcnow()

        if wallet:
            wallet.pending_balance_cents -= payout.amount_cents
            wallet.balance_cents += payout.amount_cents
            wallet.updated_at = datetime.utcnow()

        db.commit()
        logger.warning(f"Dwolla payout {payout.id} failed ({topic})")
        return {"status": "success", "payout_id": payout.id, "action": "marked_failed"}
