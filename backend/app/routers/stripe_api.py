"""
Stripe Connect API endpoints for payouts and webhooks
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.clients.stripe_client import create_express_account_if_needed, create_transfer, get_stripe
from app.config import settings
from app.db import get_db
from app.dependencies.domain import get_current_user
from app.dependencies.feature_flags import require_stripe
from app.services.nova_service import compute_payload_hash
from app.utils.log import get_logger, log_reward_event

router = APIRouter(prefix="/v1", tags=["stripe"])

logger = get_logger(__name__)


class PayoutRequest(BaseModel):
    user_id: int
    amount_cents: int
    method: str
    client_token: Optional[str] = None


@router.post("/payouts/create", dependencies=[Depends(require_stripe)])
async def create_payout(
    request: PayoutRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Create a payout by debiting user wallet and initiating Stripe transfer.

    Rate limit: 5/min per user (enforced by middleware if configured)
    """
    # Verify the caller owns this wallet or is admin
    is_admin = getattr(current_user, 'role_flags', '') and 'admin' in (current_user.role_flags or '')
    if request.user_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Cannot create payout for another user")

    now = datetime.utcnow()
    
    # Validate method
    if request.method not in ["wallet", "card_push"]:
        raise HTTPException(status_code=400, detail=f"Invalid method: {request.method}. Must be 'wallet' or 'card_push'")
    
    # Validate amount limits
    if request.amount_cents < settings.payout_min_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Amount too low: {request.amount_cents} cents (minimum: {settings.payout_min_cents})"
        )
    if request.amount_cents > settings.payout_max_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Amount too high: {request.amount_cents} cents (maximum: {settings.payout_max_cents})"
        )
    
    # Check daily cap (normalize old 'paid' to 'succeeded')
    day_start = now - timedelta(hours=24)
    daily_total_result = db.execute(text("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM payments
        WHERE user_id = :user_id 
        AND created_at >= :day_start
        AND status IN ('pending', 'succeeded', 'paid')
    """), {
        "user_id": request.user_id,
        "day_start": day_start
    }).scalar()
    daily_total = int(daily_total_result) if daily_total_result else 0
    
    if daily_total + request.amount_cents > settings.payout_daily_cap_cents:
        raise HTTPException(
            status_code=400,
            detail=f"Daily cap exceeded: {daily_total} + {request.amount_cents} > {settings.payout_daily_cap_cents} cents"
        )
    
    # Require idempotency key in non-local environments
    from app.core.env import is_local_env
    
    if not request.client_token:
        if not is_local_env():
            raise HTTPException(
                status_code=400,
                detail="client_token (idempotency key) is required in non-local environment"
            )
        # In local, generate deterministic fallback for dev only
        client_token = f"payout_{request.user_id}_{request.amount_cents}_{request.method}"
    else:
        client_token = request.client_token
    
    # Compute payload hash for conflict detection
    payload = {
        "user_id": request.user_id,
        "amount_cents": request.amount_cents,
        "method": request.method
    }
    payload_hash = compute_payload_hash(payload)
    
    # Check existing payment by idempotency_key (state machine handling)
    if request.client_token:
        existing_payment = db.execute(text("""
            SELECT id, status, metadata, payload_hash, no_transfer_confirmed FROM payments
            WHERE idempotency_key = :client_token
            LIMIT 1
        """), {
            "client_token": client_token
        }).first()
        
        if existing_payment:
            payment_id_existing = existing_payment[0]
            status_existing = existing_payment[1]
            existing_hash = existing_payment[3] if len(existing_payment) > 3 else None
            no_transfer_confirmed = existing_payment[4] if len(existing_payment) > 4 else False
            
            # Check payload_hash conflict
            if existing_hash and existing_hash != payload_hash:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency key conflict: same key with different payload"
                )
            
            # Normalize old status: 'paid' -> 'succeeded'
            if status_existing == "paid":
                status_existing = "succeeded"
            
            # State machine replay logic
            if status_existing == "succeeded":
                # Extract provider_ref from metadata
                metadata_str = existing_payment[2] if len(existing_payment) > 2 else "{}"
                try:
                    meta = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                    provider_ref = meta.get("provider_ref", None)
                except:
                    provider_ref = None
                return {
                    "ok": True,
                    "payment_id": str(payment_id_existing),
                    "status": "succeeded",
                    "provider_ref": provider_ref,
                    "message": "Idempotent: returning existing payment"
                }
            elif status_existing == "pending":
                return Response(
                    content=json.dumps({
                        "ok": True,
                        "payment_id": str(payment_id_existing),
                        "status": "pending",
                        "message": "Payment pending"
                    }),
                    status_code=202,
                    media_type="application/json"
                )
            elif status_existing == "unknown":
                return Response(
                    content=json.dumps({
                        "ok": True,
                        "payment_id": str(payment_id_existing),
                        "status": "unknown",
                        "message": "Payment pending reconciliation"
                    }),
                    status_code=202,
                    media_type="application/json"
                )
            elif status_existing == "failed":
                # Allow retry ONLY if reconciliation confirmed NO Stripe transfer exists
                if no_transfer_confirmed:
                    # Allow retry - continue to create new payment
                    pass
                else:
                    return Response(
                        content=json.dumps({
                            "ok": True,
                            "payment_id": str(payment_id_existing),
                            "status": "failed",
                            "message": "Payment failed, pending reconciliation"
                        }),
                        status_code=202,
                        media_type="application/json"
                    )
    
    # ============================================
    # PHASE A: DB Transaction (NO Stripe call)
    # ============================================
    payment_id = None
    transfer_result = None
    stripe_error = None
    
    try:
        # 1. Upsert wallet lock row
        is_sqlite = settings.database_url.startswith("sqlite")
        if is_sqlite:
            # SQLite: ON CONFLICT DO NOTHING (no column list needed)
            db.execute(text("""
                INSERT OR IGNORE INTO wallet_locks (user_id) VALUES (:user_id)
            """), {"user_id": request.user_id})
        else:
            # Postgres: ON CONFLICT (user_id) DO NOTHING
            db.execute(text("""
                INSERT INTO wallet_locks (user_id) VALUES (:user_id)
                ON CONFLICT (user_id) DO NOTHING
            """), {"user_id": request.user_id})
        
        # 2. Acquire lock with FOR UPDATE
        # SQLite lacks FOR UPDATE; transaction provides the necessary write lock for this path.
        is_sqlite = settings.database_url.startswith("sqlite")
        for_update = "" if is_sqlite else " FOR UPDATE"
        lock_result = db.execute(text(f"""
            SELECT user_id FROM wallet_locks WHERE user_id = :user_id{for_update}
        """), {"user_id": request.user_id}).first()
        
        if not lock_result:
            raise HTTPException(status_code=500, detail="Failed to acquire wallet lock")
        
        # 3. Compute balance under lock
        wallet_result = db.execute(text("""
            SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
            WHERE user_id = :user_id
        """), {"user_id": request.user_id}).scalar()
        wallet_balance = int(wallet_result) if wallet_result else 0
        
        # 4. Check sufficient funds
        if wallet_balance < request.amount_cents:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient funds: balance={wallet_balance} cents, requested={request.amount_cents} cents"
            )
        
        # 5. Calculate new balance
        new_balance = wallet_balance - request.amount_cents
        
        # 6. Create payment row (status='pending')
        is_sqlite = settings.database_url.startswith("sqlite")
        
        log_reward_event(logger, "payout_start", client_token, request.user_id, True, {
            "amount": request.amount_cents,
            "method": request.method,
            "client_token": client_token
        })
        
        metadata_dict = {
            "payment_id_placeholder": client_token,
            "user_id": request.user_id,
            "provider": "stripe",
            "client_token": client_token
        }
        
        if is_sqlite:
            db.execute(text("""
                INSERT INTO payments (
                    user_id, amount_cents, payment_method, status,
                    transaction_id, metadata, created_at, idempotency_key, payload_hash
                ) VALUES (
                    :user_id, :amount_cents, :payment_method, 'pending',
                    NULL, :metadata, :created_at, :idempotency_key, :payload_hash
                )
            """), {
                "user_id": request.user_id,
                "amount_cents": request.amount_cents,
                "payment_method": request.method,
                "metadata": json.dumps(metadata_dict),
                "created_at": now,
                "idempotency_key": client_token,
                "payload_hash": payload_hash
            })
            payment_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
            metadata_dict["payment_id"] = str(payment_id)
            db.execute(text("""
                UPDATE payments SET metadata = :metadata WHERE id = :payment_id
            """), {
                "payment_id": payment_id,
                "metadata": json.dumps(metadata_dict)
            })
        else:
            payment_id = str(uuid.uuid4())
            metadata_dict["payment_id"] = payment_id
            db.execute(text("""
                INSERT INTO payments (
                    id, user_id, amount_cents, payment_method, status,
                    transaction_id, metadata, created_at, idempotency_key, payload_hash
                ) VALUES (
                    :id, :user_id, :amount_cents, :payment_method, 'pending',
                    NULL, :metadata, :created_at, :idempotency_key, :payload_hash
                )
            """), {
                "id": payment_id,
                "user_id": request.user_id,
                "amount_cents": request.amount_cents,
                "payment_method": request.method,
                "metadata": json.dumps(metadata_dict),
                "created_at": now,
                "idempotency_key": client_token,
                "payload_hash": payload_hash
            })
        
        log_reward_event(logger, "payout_payment_created", payment_id, request.user_id, True)
        
        # 7. Insert wallet_ledger debit
        db.execute(text("""
            INSERT INTO wallet_ledger (
                user_id, amount_cents, transaction_type,
                reference_id, reference_type, balance_cents, metadata, created_at
            ) VALUES (
                :user_id, :amount_cents, 'debit',
                :reference_id, 'payout', :balance_cents, :metadata, :created_at
            )
        """), {
            "user_id": request.user_id,
            "amount_cents": -request.amount_cents,
            "reference_id": payment_id,
            "balance_cents": new_balance,
            "metadata": json.dumps({"payment_id": str(payment_id), "type": "payout", "client_token": client_token}),
            "created_at": now
        })
        
        log_reward_event(logger, "payout_debit", payment_id, request.user_id, True, {
            "amount": -request.amount_cents,
            "new_balance": new_balance
        })
        
        # 8. COMMIT Phase A
        db.commit()
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log_reward_event(logger, "payout_phase_a_failed", payment_id or "unknown", request.user_id, False, {
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise HTTPException(status_code=500, detail=f"Payout Phase A failed: {str(e)}")
    
    # ============================================
    # PHASE B: Stripe Transfer (OUTSIDE TX)
    # ============================================
    transfer_result = None
    stripe_error = None
    
    stripe_client = get_stripe()
    
    if not stripe_client or not settings.stripe_secret:
        # Simulation mode: mark as succeeded immediately
        transfer_result = {
            "id": f"tr_sim_{payment_id}",
            "status": "paid",
            "simulated": True
        }
    else:
        # Real Stripe mode: call Stripe API OUTSIDE transaction
        try:
            # Get or create Stripe account for user
            user_account_result = db.execute(text("""
                SELECT stripe_account_id FROM users WHERE id = :user_id
            """), {"user_id": request.user_id}).first()
            
            stripe_account_id = user_account_result[0] if user_account_result and user_account_result[0] else None
            
            if not stripe_account_id:
                stripe_account_id = create_express_account_if_needed(request.user_id)
                db.execute(text("""
                    UPDATE users
                    SET stripe_account_id = :stripe_account_id, stripe_onboarded = 1
                    WHERE id = :user_id
                """), {
                    "user_id": request.user_id,
                    "stripe_account_id": stripe_account_id
                })
                db.commit()
            
            # Call Stripe transfer (OUTSIDE transaction)
            try:
                transfer_result = create_transfer(
                    connected_account_id=stripe_account_id,
                    amount_cents=request.amount_cents,
                    metadata={
                        "payment_id": str(payment_id),
                        "user_id": str(request.user_id),
                        "idempotency_key": client_token
                    }
                )
            except Exception as e:
                # Timeout/network error or Stripe error - mark as unknown
                stripe_error = str(e)
                transfer_result = None
        except Exception as e:
            # Account creation/update error - mark as unknown
            stripe_error = str(e)
            transfer_result = None
    
    # ============================================
    # PHASE C: Finalize Payment Status (NEW DB TX)
    # ============================================
    try:
        # Lock payment row FOR UPDATE
        # SQLite lacks FOR UPDATE; transaction provides the necessary write lock for this path.
        is_sqlite = settings.database_url.startswith("sqlite")
        for_update = "" if is_sqlite else " FOR UPDATE"
        payment_row = db.execute(text(f"""
            SELECT id, status FROM payments WHERE id = :payment_id{for_update}
        """), {"payment_id": payment_id}).first()
        
        if not payment_row:
            raise HTTPException(status_code=500, detail="Payment not found after creation")
        
        if transfer_result and transfer_result.get("simulated"):
            # Simulation success
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
                "transfer_id": transfer_result.get("id"),
                "stripe_status": "paid",
                "reconciled_at": datetime.utcnow()
            })
            db.commit()
            
            log_reward_event(logger, "payout_stripe_transfer", payment_id, request.user_id, True, {
                "simulated": True,
                "status": "succeeded"
            })
            
            return {
                "ok": True,
                "payment_id": str(payment_id),
                "status": "succeeded",
                "provider_ref": transfer_result.get("id"),
                "message": "Simulated payout (Stripe keys not configured)"
            }
        elif transfer_result and transfer_result.get("status") == "paid":
            # Stripe success
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
                "transfer_id": transfer_result.get("id"),
                "stripe_status": transfer_result.get("status"),
                "reconciled_at": datetime.utcnow()
            })
            db.commit()
            
            log_reward_event(logger, "payout_stripe_transfer", payment_id, request.user_id, True, {
                "transfer_id": transfer_result.get("id"),
                "status": "succeeded"
            })
            
            return {
                "ok": True,
                "payment_id": str(payment_id),
                "status": "succeeded",
                "provider_ref": transfer_result.get("id")
            }
        elif transfer_result:
            # Stripe returned error (definitive failure)
            error_msg = transfer_result.get("error", "Stripe transfer failed")
            db.execute(text("""
                UPDATE payments
                SET status = 'failed',
                    error_message = :error_message,
                    reconciled_at = :reconciled_at,
                    no_transfer_confirmed = TRUE
                WHERE id = :payment_id
            """), {
                "payment_id": payment_id,
                "error_message": error_msg,
                "reconciled_at": datetime.utcnow()
            })
            
            # Insert reversal credit
            current_balance_result = db.execute(text("""
                SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
                WHERE user_id = :user_id
            """), {"user_id": request.user_id}).scalar()
            reversal_balance = int(current_balance_result) + request.amount_cents
            
            db.execute(text("""
                INSERT INTO wallet_ledger (
                    user_id, amount_cents, transaction_type,
                    reference_id, reference_type, balance_cents, metadata, created_at
                ) VALUES (
                    :user_id, :amount_cents, 'credit',
                    :reference_id, 'payout_reversal', :balance_cents, :metadata, :created_at
                )
            """), {
                "user_id": request.user_id,
                "amount_cents": request.amount_cents,  # Positive for credit
                "reference_id": payment_id,
                "balance_cents": reversal_balance,
                "metadata": json.dumps({"payment_id": str(payment_id), "type": "payout_reversal", "reason": "stripe_failed"}),
                "created_at": datetime.utcnow()
            })
            
            db.commit()
            
            log_reward_event(logger, "payout_stripe_transfer", payment_id, request.user_id, False, {
                "error": error_msg,
                "status": "failed"
            })
            
            raise HTTPException(status_code=400, detail=f"Stripe transfer failed: {error_msg}")
        else:
            # Timeout/network error - mark as unknown (DO NOT reverse)
            db.execute(text("""
                UPDATE payments
                SET status = 'unknown',
                    error_message = :error_message,
                    reconciled_at = NULL,
                    no_transfer_confirmed = FALSE
                WHERE id = :payment_id
            """), {
                "payment_id": payment_id,
                "error_message": stripe_error or "Stripe timeout/network error"
            })
            db.commit()
            
            log_reward_event(logger, "payout_stripe_transfer", payment_id, request.user_id, False, {
                "error": stripe_error or "timeout",
                "status": "unknown"
            })
            
            return Response(
                content=json.dumps({
                    "ok": True,
                    "payment_id": str(payment_id),
                    "status": "unknown",
                    "message": "Payment pending reconciliation"
                }),
                status_code=202,
                media_type="application/json"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        log_reward_event(logger, "payout_phase_c_failed", payment_id, request.user_id, False, {
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise HTTPException(status_code=500, detail=f"Payout Phase C failed: {str(e)}")


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events to finalize payouts.
    
    Verifies webhook signature (required in non-local environments).
    Deduplicates events using stripe_webhook_events table.
    """
    import json
    import os
    
    # Check feature flags (P1 stability fix)
    if settings.emergency_readonly_mode:
        # In readonly mode, still process webhooks but don't update balances
        logger.warning("Emergency readonly mode: webhook received but not processing")
        return {"ok": True, "message": "Readonly mode - webhook logged but not processed"}
    
    # Check if in local environment
    env = os.getenv("ENV", "dev").lower()
    region = settings.region.lower()
    is_local = env == "local" or region == "local"
    
    body = await request.body()
    signature = request.headers.get("stripe-signature")
    
    # Require webhook secret in non-local environments (P0 security fix)
    if not is_local:
        if not settings.stripe_webhook_secret:
            error_msg = (
                "CRITICAL: STRIPE_WEBHOOK_SECRET is required in non-local environment. "
                f"ENV={env}, REGION={region}. Set STRIPE_WEBHOOK_SECRET environment variable."
            )
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail="Webhook secret not configured")
        
        if not signature:
            logger.error("Missing stripe-signature header in non-local environment")
            raise HTTPException(status_code=400, detail="Missing webhook signature")
    
    # Verify signature
    if settings.stripe_webhook_secret and signature:
        try:
            stripe_client = get_stripe()
            if stripe_client:
                event = stripe_client.Webhook.construct_event(
                    body, signature, settings.stripe_webhook_secret
                )
            else:
                # In simulation/local, skip verification
                event = json.loads(body)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            raise HTTPException(status_code=400, detail=f"Webhook signature verification failed: {str(e)}")
    else:
        # No secret configured (local dev only)
        if not is_local:
            raise HTTPException(status_code=500, detail="Webhook secret required in non-local environment")
        try:
            event = json.loads(body)
        except:
            raise HTTPException(status_code=400, detail="Invalid webhook body")
    
    event_id = event.get("id")
    event_type = event.get("type")
    event_data = event.get("data", {}).get("object", {})
    
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event ID")
    
    # Stripe signature verification handles replay protection.
    # Do NOT reject old events — Stripe retries failed webhooks for up to 3 days.

    logger.info(f"Stripe webhook received: type={event_type} id={event_id}")

    try:
        # Route checkout.session.completed to campaign funding handler
        if event_type == "checkout.session.completed":
            metadata = event_data.get("metadata", {})
            checkout_type = metadata.get("type", "")

            if checkout_type == "campaign_funding":
                from app.services.campaign_service import CampaignService
                stripe_session_id = event_data.get("id", "")
                payment_intent_id = event_data.get("payment_intent")
                result = CampaignService.fund_campaign(
                    db,
                    checkout_session_id=stripe_session_id,
                    payment_intent_id=payment_intent_id,
                    metadata=metadata,
                )
                logger.info(f"Campaign funding processed: {result}")
                return {"ok": True, "handler": "campaign_funding", **result}

            elif checkout_type == "merchant_subscription" or metadata.get("plan"):
                from app.services.merchant_subscription_service import handle_checkout_completed
                handle_checkout_completed(db, event_data)
                return {"ok": True, "handler": "merchant_subscription"}

        # Handle transfer/payout success events
        if event_type in ["transfer.paid", "payout.paid", "balance.available"]:
            payment_id = event_data.get("metadata", {}).get("payment_id")
            if payment_id:
                result = db.execute(text("""
                    UPDATE payments
                    SET status = 'succeeded'
                    WHERE id = :payment_id AND status = 'pending'
                """), {"payment_id": payment_id})
                db.commit()
                if result.rowcount > 0:
                    logger.info(f"Payment {payment_id} marked as succeeded")
                    return {"ok": True, "payment_id": payment_id, "status": "succeeded"}
                return {"ok": True, "message": "Payment already processed or not found"}

        # Handle subscription lifecycle events
        if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            from app.services.merchant_subscription_service import (
                handle_subscription_deleted,
                handle_subscription_updated,
            )
            if event_type == "customer.subscription.updated":
                handle_subscription_updated(db, event_data)
            else:
                handle_subscription_deleted(db, event_data)
            return {"ok": True, "handler": "merchant_subscription", "event_type": event_type}

        logger.debug(f"Unhandled Stripe event type: {event_type}")
        return {"ok": True, "message": f"Event type acknowledged: {event_type}"}

    except Exception as e:
        logger.error(f"Error processing Stripe webhook {event_type}: {e}", exc_info=True)
        # Return 200 to prevent Stripe from retrying on application errors
        return {"ok": True, "error": str(e)}

