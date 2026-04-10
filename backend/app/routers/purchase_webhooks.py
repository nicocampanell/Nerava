"""
Purchase webhook ingestion and reconciliation endpoints
"""
import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services.purchases import find_or_create_merchant, match_session, normalize_event
from app.services.rewards import award_purchase_reward
from app.utils.log import get_logger, log_reward_event

router = APIRouter(prefix="/v1", tags=["purchases"])

logger = get_logger(__name__)


def verify_square_signature(body: bytes, signature: str, secret: str) -> bool:
    """
    Verify Square webhook signature using HMAC-SHA256.
    
    Square uses HMAC-SHA256 with the webhook signature key, then base64 encodes the result.
    
    Args:
        body: Raw request body as bytes
        signature: X-Square-Signature header value (base64 encoded HMAC)
        secret: SQUARE_WEBHOOK_SIGNATURE_KEY
        
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        # Compute HMAC-SHA256
        computed_hmac = hmac.new(
            secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).digest()
        
        # Base64 encode
        computed_signature = base64.b64encode(computed_hmac).decode('utf-8')
        
        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(computed_signature, signature)
    except Exception as e:
        logger.error(f"Square signature verification error: {e}")
        return False


class ClaimRequest(BaseModel):
    user_id: int
    payment_id: int


@router.post("/webhooks/purchase")
async def ingest_purchase_webhook(
    request: Request,
    x_webhook_secret: Optional[str] = Header(None, alias="X-Webhook-Secret"),
    x_square_signature: Optional[str] = Header(None, alias="X-Square-Signature"),
    db: Session = Depends(get_db)
):
    """
    Ingest purchase webhook from Square, CLO, or other providers.
    
    Rate limit: 60/min per IP (handled by middleware if configured)
    Security: 
    - P0-4: If SQUARE_WEBHOOK_SIGNATURE_KEY is configured, verifies X-Square-Signature header
    - Otherwise, falls back to X-Webhook-Secret header if WEBHOOK_SHARED_SECRET is set
    """
    # Get raw body for signature verification (must be bytes)
    raw_body = await request.body()
    
    # P0-4: Square webhook signature verification
    env = os.getenv("ENV", "dev").lower()
    is_local = env in {"local", "dev"}
    
    # Check if this is a Square webhook (provider will be determined after parsing, but we can check headers)
    is_square_webhook = x_square_signature is not None
    
    if settings.square_webhook_signature_key:
        # Signature key configured - require signature verification
        if not x_square_signature:
            logger.warning("Square webhook signature key configured but X-Square-Signature header missing")
            raise HTTPException(status_code=401, detail="Missing X-Square-Signature header")
        
        if not verify_square_signature(raw_body, x_square_signature, settings.square_webhook_signature_key):
            logger.warning("Square webhook signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid Square webhook signature")
        
        logger.debug("Square webhook signature verified successfully")
    elif not is_local and is_square_webhook:
        # In production, if Square signature header is present but key not configured, reject
        # This ensures we fail closed in production
        logger.error("Square webhook received but SQUARE_WEBHOOK_SIGNATURE_KEY not configured in production")
        raise HTTPException(
            status_code=500,
            detail="Square webhook signature verification not configured. SQUARE_WEBHOOK_SIGNATURE_KEY is required in production."
        )
    else:
        # Fallback to secret check if signature key not configured (backward compat for local/dev)
        if settings.webhook_shared_secret:
            if not x_webhook_secret or x_webhook_secret != settings.webhook_shared_secret:
                raise HTTPException(status_code=401, detail="Invalid webhook secret")
    
    # Parse body (already read as raw_body, now decode as JSON)
    try:
        body = json.loads(raw_body.decode('utf-8'))
    except Exception as e:
        logger.error(f"Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    now = datetime.utcnow()
    
    # Step 1: Normalize event
    try:
        normalized = normalize_event(body)
        log_reward_event(logger, "normalized", "webhook", normalized.get("user_id") or 0, True, {
            "provider": normalized.get("provider"),
            "merchant_ext_id": normalized.get("merchant_ext_id")
        })
    except Exception as e:
        logger.error(f"Normalization failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to normalize event: {str(e)}")
    
    # P0-1: Purchase webhook replay protection - reject events older than 5 minutes
    event_ts = normalized.get("ts")
    if event_ts:
        # event_ts is a datetime object from normalize_event
        if isinstance(event_ts, datetime):
            # Use UTC for comparison
            from datetime import timezone
            if event_ts.tzinfo is None:
                # Assume UTC if no timezone info
                event_time = event_ts.replace(tzinfo=timezone.utc)
            else:
                event_time = event_ts.astimezone(timezone.utc)
            
            now_utc = datetime.now(timezone.utc)
            age_minutes = (now_utc - event_time).total_seconds() / 60
            
            if age_minutes > 5:
                logger.warning(
                    f"Rejecting old purchase webhook event: {age_minutes:.1f} minutes old "
                    f"(replay protection). Provider: {normalized.get('provider')}, "
                    f"provider_ref: {normalized.get('provider_ref')}"
                )
                raise HTTPException(
                    status_code=400,
                    detail="Webhook event too old (replay protection). Events older than 5 minutes are rejected."
                )
    
    # Validate required fields
    if not normalized.get("provider") or not normalized.get("provider_ref"):
        raise HTTPException(status_code=400, detail="Missing provider or provider_ref")
    
    if normalized.get("user_id") is None:
        raise HTTPException(status_code=400, detail="Missing user_id in webhook payload")
    
    if not normalized.get("amount_cents"):
        raise HTTPException(status_code=400, detail="Missing amount_cents")
    
    user_id = int(normalized["user_id"])
    provider = normalized["provider"]
    provider_ref = normalized["provider_ref"]
    
    # Step 2: Idempotency check (by provider + provider_ref)
    existing_payment = db.execute(text("""
        SELECT id, status, claimed, claimed_at, merchant_id
        FROM payments
        WHERE transaction_id = :provider_ref
        AND metadata LIKE :pattern
        LIMIT 1
    """), {
        "provider_ref": provider_ref,
        "pattern": f'%{provider}%'
    }).first()
    
    if existing_payment:
        # Return existing payment info
        payment_id = existing_payment[0]
        claimed = bool(existing_payment[2]) if existing_payment[2] else False
        
        log_reward_event(logger, "idempotency", str(payment_id), user_id, True, {
            "provider": provider,
            "provider_ref": provider_ref
        })
        
        return {
            "ok": True,
            "payment_id": str(payment_id),
            "matched_session": None,
            "claimed": claimed,
            "message": "Idempotent: returning existing payment"
        }
    
    # Step 3: Upsert merchant
    merchant_id = None
    if normalized.get("merchant_ext_id"):
        try:
            merchant_id = find_or_create_merchant(
                db,
                ext_id=normalized["merchant_ext_id"],
                name=normalized.get("merchant_name"),
                lat=normalized.get("lat"),
                lng=normalized.get("lng"),
                category=normalized.get("category"),
                city=normalized.get("city")
            )
        except Exception as e:
            logger.warn(f"Merchant upsert failed: {e}, continuing without merchant_id")
    
    # Step 4: Insert payments row
    purchase_ts = normalized.get("ts") or now
    expires_at = purchase_ts + timedelta(minutes=settings.purchase_session_ttl_min)
    
    is_sqlite = settings.database_url.startswith("sqlite")
    
    if is_sqlite:
        db.execute(text("""
            INSERT INTO payments (
                user_id, merchant_id, amount_cents, payment_method, status,
                transaction_id, metadata, raw_json, claimed, expires_at, created_at
            ) VALUES (
                :user_id, :merchant_id, :amount_cents, 'webhook', 'confirmed',
                :transaction_id, :metadata, :raw_json, :claimed, :expires_at, :created_at
            )
        """), {
            "user_id": user_id,
            "merchant_id": merchant_id,
            "amount_cents": normalized["amount_cents"],
            "transaction_id": provider_ref,
            "metadata": json.dumps({"provider": provider, "provider_ref": provider_ref}),
            "raw_json": json.dumps(normalized.get("raw", {})),
            "claimed": False,
            "expires_at": expires_at,
            "created_at": now
        })
        payment_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
    else:
        payment_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO payments (
                id, user_id, merchant_id, amount_cents, payment_method, status,
                transaction_id, metadata, raw_json, claimed, expires_at, created_at
            ) VALUES (
                :id, :user_id, :merchant_id, :amount_cents, 'webhook', 'confirmed',
                :transaction_id, :metadata, :raw_json, :claimed, :expires_at, :created_at
            )
        """), {
            "id": payment_id,
            "user_id": user_id,
            "merchant_id": merchant_id,
            "amount_cents": normalized["amount_cents"],
            "transaction_id": provider_ref,
            "metadata": json.dumps({"provider": provider, "provider_ref": provider_ref}),
            "raw_json": json.dumps(normalized.get("raw", {})),
            "claimed": False,
            "expires_at": expires_at,
            "created_at": now
        })
    
    log_reward_event(logger, "inserted", str(payment_id), user_id, True, {
        "merchant_id": merchant_id,
        "amount": normalized["amount_cents"]
    })
    
    # Step 5: Try to match session
    matched_session_id = None
    claimed = False
    
    if merchant_id:
        try:
            matched_session_id = match_session(
                db,
                user_id=user_id,
                merchant_id=merchant_id,
                ts=purchase_ts,
                radius_m=settings.purchase_match_radius_m,
                ttl_min=settings.purchase_session_ttl_min
            )
        except Exception as e:
            logger.warn(f"Session matching failed: {e}")
    
    # Step 6: If match found, award reward and mark claimed
    if matched_session_id:
        try:
            reward_amount = settings.purchase_reward_flat_cents  # Use flat rate for now
            
            reward_result = award_purchase_reward(
                db=db,
                user_id=user_id,
                session_id=matched_session_id,
                payment_id=payment_id,
                amount=reward_amount,
                now=now
            )
            
            if reward_result.get("awarded"):
                # Mark payment as claimed
                db.execute(text("""
                    UPDATE payments
                    SET claimed = 1, claimed_at = :claimed_at
                    WHERE id = :payment_id
                """), {
                    "payment_id": payment_id,
                    "claimed_at": now
                })
                claimed = True
                log_reward_event(logger, "awarded", str(payment_id), user_id, True, {
                    "session_id": matched_session_id,
                    "amount": reward_amount
                })
            else:
                log_reward_event(logger, "awarded", str(payment_id), user_id, False, {
                    "reason": reward_result.get("reason")
                })
        except Exception as e:
            logger.error(f"Reward award failed: {e}")
            # Continue - payment created but not claimed
    
    if not claimed:
        log_reward_event(logger, "pending", str(payment_id), user_id, True, {
            "expires_at": expires_at.isoformat()
        })
    
    db.commit()
    
    return {
        "ok": True,
        "payment_id": str(payment_id),
        "matched_session": matched_session_id,
        "claimed": claimed
    }


@router.post("/purchases/claim")
async def claim_pending_purchase(
    request: ClaimRequest,
    db: Session = Depends(get_db)
):
    """
    Manually trigger reconciliation for a pending purchase.
    
    Dev/staging only. Attempts to match payment to eligible session and award reward.
    """
    # Guard: only allow in non-production
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if app_env == "prod" or app_env == "production":
        raise HTTPException(status_code=403, detail="Claim endpoint disabled in production")
    
    # Fetch payment
    payment_result = db.execute(text("""
        SELECT id, user_id, merchant_id, amount_cents, claimed, expires_at, created_at
        FROM payments
        WHERE id = :payment_id
    """), {"payment_id": request.payment_id}).first()
    
    if not payment_result:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    payment_id = payment_result[0]
    payment_user_id = payment_result[1]
    merchant_id = payment_result[2] if payment_result[2] else None
    payment_ts_str = payment_result[6] if len(payment_result) > 6 else None
    
    # Verify user_id matches
    if payment_user_id != request.user_id:
        raise HTTPException(status_code=403, detail="Payment belongs to different user")
    
    # Check if already claimed
    if payment_result[4]:  # claimed
        return {
            "ok": True,
            "payment_id": str(payment_id),
            "matched_session": None,
            "claimed": True,
            "message": "Payment already claimed"
        }
    
    # Check expiration
    expires_at = payment_result[5] if len(payment_result) > 5 else None
    if expires_at:
        if isinstance(expires_at, str):
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00')[:19])
            except:
                expires_dt = datetime.utcnow()
        else:
            expires_dt = expires_at
        
        if expires_dt < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Payment claim window has expired")
    
    # Parse payment timestamp
    now = datetime.utcnow()
    if payment_ts_str:
        try:
            if isinstance(payment_ts_str, str):
                purchase_ts = datetime.fromisoformat(payment_ts_str.replace('Z', '+00:00')[:19])
            else:
                purchase_ts = payment_ts_str
        except:
            purchase_ts = now
    else:
        purchase_ts = now
    
    # Try to match session
    matched_session_id = None
    if merchant_id:
        matched_session_id = match_session(
            db,
            user_id=request.user_id,
            merchant_id=merchant_id,
            ts=purchase_ts,
            radius_m=settings.purchase_match_radius_m,
            ttl_min=settings.purchase_session_ttl_min
        )
    
    # If match found, award reward
    if matched_session_id:
        try:
            reward_amount = settings.purchase_reward_flat_cents
            
            reward_result = award_purchase_reward(
                db=db,
                user_id=request.user_id,
                session_id=matched_session_id,
                payment_id=payment_id,
                amount=reward_amount,
                now=now
            )
            
            if reward_result.get("awarded"):
                db.execute(text("""
                    UPDATE payments
                    SET claimed = 1, claimed_at = :claimed_at
                    WHERE id = :payment_id
                """), {
                    "payment_id": payment_id,
                    "claimed_at": now
                })
                db.commit()
                
                return {
                    "ok": True,
                    "payment_id": str(payment_id),
                    "matched_session": matched_session_id,
                    "claimed": True
                }
            else:
                return {
                    "ok": False,
                    "payment_id": str(payment_id),
                    "matched_session": matched_session_id,
                    "claimed": False,
                    "reason": reward_result.get("reason")
                }
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to award reward: {str(e)}")
    else:
        return {
            "ok": False,
            "payment_id": str(payment_id),
            "matched_session": None,
            "claimed": False,
            "reason": "No matching session found"
        }

