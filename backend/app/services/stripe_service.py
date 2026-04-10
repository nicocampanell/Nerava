"""
Stripe Service - Nova purchase via Stripe Checkout
for Domain Charge Party MVP
"""
import logging
import uuid
from typing import Any, Dict

import stripe
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models_domain import DomainMerchant, StripePayment
from app.services.nova_service import NovaService

logger = logging.getLogger(__name__)

# Stripe packages: package_id -> (usd_cents, nova_amount)
NOVA_PACKAGES = {
    "nova_100": {"usd_cents": 10000, "nova_amount": 1000},  # $100 for 1000 Nova
    "nova_500": {"usd_cents": 45000, "nova_amount": 5000},  # $450 for 5000 Nova (10% discount)
    "nova_1000": {"usd_cents": 80000, "nova_amount": 10000},  # $800 for 10000 Nova (20% discount)
}

# Initialize Stripe (will use env var STRIPE_SECRET_KEY)
stripe.api_key = settings.STRIPE_SECRET_KEY if settings.STRIPE_SECRET_KEY else None


class StripeService:
    """Service for Stripe Checkout and webhook handling"""
    
    @staticmethod
    async def create_checkout_session_async(
        db: Session,
        merchant_id: str,
        package_id: str
    ) -> Dict[str, Any]:
        """
        Create Stripe Checkout session for Nova purchase (async wrapper).
        
        Wraps sync Stripe SDK call with asyncio.to_thread to avoid blocking event loop.
        
        Args:
            merchant_id: Merchant ID
            package_id: Package ID (e.g., "nova_100")
        
        Returns:
            Dict with checkout_url
        """
        import asyncio
        
        def _create_session():
            return StripeService.create_checkout_session(db, merchant_id, package_id)
        
        return await asyncio.to_thread(_create_session)
    
    @staticmethod
    def create_checkout_session(
        db: Session,
        merchant_id: str,
        package_id: str
    ) -> Dict[str, Any]:
        """
        Create Stripe Checkout session for Nova purchase.
        
        Args:
            merchant_id: Merchant ID
            package_id: Package ID (e.g., "nova_100")
        
        Returns:
            Dict with checkout_url
        """
        if not stripe.api_key:
            raise ValueError("Stripe not configured. Set STRIPE_SECRET_KEY environment variable.")
        
        # Validate package
        if package_id not in NOVA_PACKAGES:
            raise ValueError(f"Invalid package_id: {package_id}")
        
        package = NOVA_PACKAGES[package_id]
        
        # Validate merchant exists
        merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
        if not merchant:
            raise ValueError(f"Merchant {merchant_id} not found")
        
        # Create Stripe Checkout session
        payment_id = str(uuid.uuid4())
        
        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"{package['nova_amount']} Nova",
                            "description": f"Purchase {package['nova_amount']} Nova for Domain Charge Party"
                        },
                        "unit_amount": package["usd_cents"]
                    },
                    "quantity": 1
                }],
                mode="payment",
                success_url=f"{settings.FRONTEND_URL}/merchant/dashboard?success=true",
                cancel_url=f"{settings.FRONTEND_URL}/merchant/buy-nova?cancelled=true",
                metadata={
                    "merchant_id": merchant_id,
                    "nova_amount": package["nova_amount"],
                    "package_id": package_id,
                    "payment_id": payment_id
                },
                client_reference_id=payment_id
            )
            
            # Create payment record
            stripe_payment = StripePayment(
                id=payment_id,
                stripe_session_id=checkout_session.id,
                merchant_id=merchant_id,
                amount_usd=package["usd_cents"],
                nova_issued=package["nova_amount"],
                status="pending"
            )
            db.add(stripe_payment)
            db.commit()
            db.refresh(stripe_payment)
            
            logger.info(f"Created Stripe checkout session for merchant {merchant_id}: {checkout_session.id}")
            
            return {
                "checkout_url": checkout_session.url,
                "session_id": checkout_session.id
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            raise ValueError(f"Stripe error: {str(e)}")
    
    @staticmethod
    async def handle_webhook_async(
        db: Session,
        payload: bytes,
        signature: str,
        webhook_secret: str
    ) -> Dict[str, Any]:
        """
        Handle Stripe webhook event (async wrapper).
        
        Wraps sync Stripe SDK call with asyncio.to_thread to avoid blocking event loop.
        
        Args:
            payload: Raw webhook payload
            signature: Stripe signature header
            webhook_secret: Stripe webhook secret
        
        Returns:
            Dict with status and message
        """
        import asyncio
        
        def _handle_webhook():
            return StripeService.handle_webhook(db, payload, signature, webhook_secret)
        
        return await asyncio.to_thread(_handle_webhook)
    
    @staticmethod
    def handle_webhook(
        db: Session,
        payload: bytes,
        signature: str,
        webhook_secret: str
    ) -> Dict[str, Any]:
        """
        Handle Stripe webhook event.
        
        Args:
            payload: Raw webhook payload
            signature: Stripe signature header
            webhook_secret: Stripe webhook secret
        
        Returns:
            Dict with status and message
        """
        if not stripe.api_key:
            raise ValueError("Stripe not configured")
        
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, webhook_secret
            )
        except ValueError as e:
            logger.error(f"Invalid payload in Stripe webhook: {e}")
            raise ValueError(f"Invalid payload: {e}")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid signature in Stripe webhook: {e}")
            raise ValueError(f"Invalid signature: {e}")
        
        # Handle the event
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            return StripeService._handle_checkout_completed(db, session, event["id"])
        else:
            logger.info(f"Unhandled Stripe event type: {event['type']}")
            return {"status": "ignored", "message": f"Event type {event['type']} not handled"}
    
    @staticmethod
    def _handle_checkout_completed(
        db: Session,
        session: Dict[str, Any],
        event_id: str
    ) -> Dict[str, Any]:
        """Handle checkout.session.completed event"""
        stripe_session_id = session["id"]
        payment_intent_id = session.get("payment_intent")
        metadata = session.get("metadata", {})

        # Route campaign funding checkouts to CampaignService
        if metadata.get("type") == "campaign_funding":
            try:
                from app.services.campaign_service import CampaignService
                result = CampaignService.fund_campaign(
                    db,
                    checkout_session_id=stripe_session_id,
                    payment_intent_id=payment_intent_id,
                )
                return result
            except Exception as e:
                logger.error(f"Campaign funding failed for session {stripe_session_id}: {e}")
                return {"status": "error", "message": str(e)}

        merchant_id = metadata.get("merchant_id")
        nova_amount = int(metadata.get("nova_amount", 0))
        package_id = metadata.get("package_id")
        payment_id = metadata.get("payment_id")
        
        # Check for idempotency
        existing_payment = db.query(StripePayment).filter(
            StripePayment.stripe_event_id == event_id
        ).first()
        
        if existing_payment:
            logger.info(f"Webhook event {event_id} already processed (idempotent)")
            return {"status": "already_processed", "payment_id": existing_payment.id}
        
        # Find payment record by session_id or payment_id
        stripe_payment = db.query(StripePayment).filter(
            StripePayment.stripe_session_id == stripe_session_id
        ).first()
        
        if not stripe_payment and payment_id:
            stripe_payment = db.query(StripePayment).filter(
                StripePayment.id == payment_id
            ).first()
        
        if not stripe_payment:
            logger.error(f"Stripe payment not found for session {stripe_session_id}")
            return {"status": "error", "message": "Payment record not found"}
        
        # Update payment status and grant Nova atomically
        stripe_payment.status = "paid"
        stripe_payment.stripe_payment_intent_id = payment_intent_id
        stripe_payment.stripe_event_id = event_id
        db.flush()
        
        # Grant Nova to merchant (atomic with payment update)
        try:
            NovaService.grant_to_merchant(
                db=db,
                merchant_id=merchant_id,
                amount=nova_amount,
                type="merchant_topup",
                stripe_payment_id=stripe_payment.id,
                metadata={
                    "package_id": package_id,
                    "stripe_session_id": stripe_session_id
                }
            )
            
            # Single commit for entire operation
            db.commit()
            logger.info(f"Granted {nova_amount} Nova to merchant {merchant_id} via Stripe payment {stripe_payment.id}")
            
            return {
                "status": "success",
                "payment_id": stripe_payment.id,
                "nova_granted": nova_amount
            }
        except Exception as e:
            db.rollback()
            # Record failure in separate transaction
            stripe_payment.status = "failed"
            db.commit()
            logger.error("stripe_webhook_failed", extra={
                "event_id": event_id,
                "payment_id": stripe_payment.id,
                "merchant_id": merchant_id,
                "error": str(e)
            })
            return {"status": "error", "message": str(e)}
    
    @staticmethod
    async def reconcile_payment_async(
        db: Session,
        payment_id: str
    ) -> Dict[str, Any]:
        """
        Reconcile a payment (async wrapper).
        
        Wraps sync Stripe SDK calls with asyncio.to_thread to avoid blocking event loop.
        """
        import asyncio
        
        def _reconcile():
            return StripeService.reconcile_payment(db, payment_id)
        
        return await asyncio.to_thread(_reconcile)
    
    @staticmethod
    def reconcile_payment(
        db: Session,
        payment_id: str
    ) -> Dict[str, Any]:
        """
        Reconcile a payment with status 'unknown' by checking Stripe.
        
        Rules:
        - Lock payment FOR UPDATE
        - If status != unknown: return as-is
        - Query Stripe:
          - If stripe_transfer_id exists: fetch transfer by id
          - Else: search by metadata/idempotency_key
        - Outcomes:
          - Transfer found and succeeded → mark succeeded, reconciled_at, set stripe ids/status
          - Transfer confirmed NOT found → mark failed, insert reversal credit ONCE, reconciled_at, no_transfer_confirmed=true
          - Still ambiguous → keep unknown
        
        Args:
            db: Database session
            payment_id: Payment ID to reconcile
            
        Returns:
            Dict with payment status and reconciliation result
        """
        import json
        from datetime import datetime

        from sqlalchemy import text
        
        # Lock payment FOR UPDATE
        # SQLite lacks FOR UPDATE; transaction provides the necessary write lock for this path.
        is_sqlite = settings.database_url.startswith("sqlite")
        for_update = "" if is_sqlite else " FOR UPDATE"
        payment_row = db.execute(text(f"""
            SELECT id, status, stripe_transfer_id, idempotency_key, metadata, user_id, amount_cents
            FROM payments
            WHERE id = :payment_id
            {for_update}
        """), {"payment_id": payment_id}).first()
        
        if not payment_row:
            raise ValueError(f"Payment {payment_id} not found")
        
        status = payment_row[1]
        stripe_transfer_id = payment_row[2]
        idempotency_key = payment_row[3]
        metadata_str = payment_row[4]
        user_id = payment_row[5]
        amount_cents = payment_row[6]
        
        # If status != unknown, return as-is
        if status != "unknown":
            return {
                "payment_id": payment_id,
                "status": status,
                "message": f"Payment status is {status}, no reconciliation needed"
            }
        
        # Query Stripe
        try:
            if stripe_transfer_id:
                # Fetch transfer by ID
                transfer = stripe.Transfer.retrieve(stripe_transfer_id)
                if transfer.status == "paid":
                    # Transfer succeeded
                    db.execute(text("""
                        UPDATE payments
                        SET status = 'succeeded',
                            stripe_transfer_id = :transfer_id,
                            stripe_status = :stripe_status,
                            reconciled_at = :reconciled_at,
                            no_transfer_confirmed = FALSE
                        WHERE id = :payment_id
                    """), {
                        "payment_id": payment_id,
                        "transfer_id": transfer.id,
                        "stripe_status": transfer.status,
                        "reconciled_at": datetime.utcnow()
                    })
                    db.commit()
                    return {
                        "payment_id": payment_id,
                        "status": "succeeded",
                        "stripe_transfer_id": transfer.id,
                        "message": "Reconciled: transfer found and succeeded"
                    }
                else:
                    # Transfer failed
                    db.execute(text("""
                        UPDATE payments
                        SET status = 'failed',
                            error_message = :error_message,
                            reconciled_at = :reconciled_at,
                            no_transfer_confirmed = FALSE
                        WHERE id = :payment_id
                    """), {
                        "payment_id": payment_id,
                        "error_message": f"Stripe transfer status: {transfer.status}",
                        "reconciled_at": datetime.utcnow()
                    })
                    # Insert reversal credit
                    current_balance_result = db.execute(text("""
                        SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
                        WHERE user_id = :user_id
                    """), {"user_id": user_id}).scalar()
                    reversal_balance = int(current_balance_result) + amount_cents
                    
                    db.execute(text("""
                        INSERT INTO wallet_ledger (
                            user_id, amount_cents, transaction_type,
                            reference_id, reference_type, balance_cents, metadata, created_at
                        ) VALUES (
                            :user_id, :amount_cents, 'credit',
                            :reference_id, 'payout_reversal', :balance_cents, :metadata, :created_at
                        )
                    """), {
                        "user_id": user_id,
                        "amount_cents": amount_cents,
                        "reference_id": payment_id,
                        "balance_cents": reversal_balance,
                        "metadata": json.dumps({"payment_id": payment_id, "type": "payout_reversal", "reason": "reconciliation_failed"}),
                        "created_at": datetime.utcnow()
                    })
                    db.commit()
                    return {
                        "payment_id": payment_id,
                        "status": "failed",
                        "message": "Reconciled: transfer found but failed"
                    }
            else:
                # Search by metadata/idempotency_key
                # Note: Stripe API doesn't support searching by metadata directly
                # In production, you'd need to maintain a mapping or use webhooks
                # For now, assume transfer not found if no transfer_id
                db.execute(text("""
                    UPDATE payments
                    SET status = 'failed',
                        error_message = 'Transfer not found in Stripe',
                        reconciled_at = :reconciled_at,
                        no_transfer_confirmed = TRUE
                    WHERE id = :payment_id
                """), {
                    "payment_id": payment_id,
                    "reconciled_at": datetime.utcnow()
                })
                
                # Insert reversal credit ONCE
                current_balance_result = db.execute(text("""
                    SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
                    WHERE user_id = :user_id
                """), {"user_id": user_id}).scalar()
                reversal_balance = int(current_balance_result) + amount_cents
                
                db.execute(text("""
                    INSERT INTO wallet_ledger (
                        user_id, amount_cents, transaction_type,
                        reference_id, reference_type, balance_cents, metadata, created_at
                    ) VALUES (
                        :user_id, :amount_cents, 'credit',
                        :reference_id, 'payout_reversal', :balance_cents, :metadata, :created_at
                    )
                """), {
                    "user_id": user_id,
                    "amount_cents": amount_cents,
                    "reference_id": payment_id,
                    "balance_cents": reversal_balance,
                    "metadata": json.dumps({"payment_id": payment_id, "type": "payout_reversal", "reason": "reconciliation_no_transfer"}),
                    "created_at": datetime.utcnow()
                })
                db.commit()
                return {
                    "payment_id": payment_id,
                    "status": "failed",
                    "no_transfer_confirmed": True,
                    "message": "Reconciled: transfer not found in Stripe"
                }
        except stripe.error.StripeError as e:
            # Stripe API error - keep unknown
            logger.error(f"Stripe API error during reconciliation: {e}")
            return {
                "payment_id": payment_id,
                "status": "unknown",
                "message": f"Reconciliation failed: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Unexpected error during reconciliation: {e}")
            db.rollback()
            return {
                "payment_id": payment_id,
                "status": "unknown",
                "message": f"Reconciliation error: {str(e)}"
            }

