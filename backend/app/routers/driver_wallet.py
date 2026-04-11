"""
Driver Wallet Router - Stripe Express Payouts

Endpoints for driver wallet management, balance checks, and withdrawals.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.domain import get_current_user
from ..models.campaign import Campaign
from ..models.driver_wallet import DriverWallet, WalletLedger
from ..models.session_event import IncentiveGrant
from ..services.payout_service import PayoutService, calculate_withdrawal_fee

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/wallet", tags=["wallet"])


class WithdrawRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=10000000)


class CreateAccountRequest(BaseModel):
    email: str = ""


class AccountLinkRequest(BaseModel):
    return_url: str
    refresh_url: str


@router.get("/balance")
async def get_balance(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get driver wallet balance"""
    try:
        balance = PayoutService.get_balance(db, current_user.id)
        return balance
    except Exception as e:
        logger.exception("Failed to get wallet balance for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to retrieve wallet balance")


@router.get("/history")
async def get_wallet_history(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get wallet transaction history (payouts)"""
    try:
        history = PayoutService.get_payout_history(db, current_user.id, limit)
        return {"payouts": history}
    except Exception as e:
        logger.exception("Failed to get payout history for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to retrieve payout history")


@router.get("/ledger")
async def get_wallet_ledger(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get wallet ledger entries with campaign attribution."""
    entries = (
        db.query(WalletLedger)
        .filter(WalletLedger.driver_id == current_user.id)
        .order_by(WalletLedger.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for e in entries:
        campaign_name = None
        sponsor_name = None
        if e.reference_type == "campaign_grant" and e.reference_id:
            grant = db.query(IncentiveGrant).filter(IncentiveGrant.id == e.reference_id).first()
            if grant and grant.campaign_id:
                campaign = db.query(Campaign).filter(Campaign.id == grant.campaign_id).first()
                if campaign:
                    campaign_name = campaign.name
                    sponsor_name = campaign.sponsor_name

        results.append(
            {
                "id": e.id,
                "amount_cents": e.amount_cents,
                "balance_after_cents": e.balance_after_cents,
                "transaction_type": e.transaction_type,
                "description": e.description,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "campaign_name": campaign_name,
                "sponsor_name": sponsor_name,
            }
        )

    return {"entries": results, "count": len(results)}


@router.get("/withdraw/fee")
async def get_withdrawal_fee(
    amount_cents: int,
    current_user=Depends(get_current_user),
):
    """Calculate the processing fee for a withdrawal amount"""
    fee = calculate_withdrawal_fee(amount_cents)
    return {"amount_cents": amount_cents, "fee_cents": fee, "net_cents": amount_cents}


@router.post("/withdraw")
async def request_withdrawal(
    request: WithdrawRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Request a withdrawal to Stripe Express account"""
    try:
        result = PayoutService.request_withdrawal(db, current_user.id, request.amount_cents)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Withdrawal failed for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Withdrawal request failed")


@router.post("/stripe/account")
async def create_stripe_account(
    request: CreateAccountRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create or get Stripe Express account for driver"""
    try:
        result = PayoutService.create_express_account(db, current_user.id, request.email)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Stripe account creation failed for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to create payout account")


@router.post("/stripe/account-link")
async def create_stripe_account_link(
    request: AccountLinkRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create Stripe account onboarding link"""
    try:
        result = PayoutService.create_account_link(
            db, current_user.id, request.return_url, request.refresh_url
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Stripe account link creation failed for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to create onboarding link")


@router.get("/stripe/status")
async def check_stripe_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Check Stripe account onboarding status by calling Stripe API directly"""
    try:
        result = PayoutService.check_stripe_onboarding_status(db, current_user.id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Stripe status check failed for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to check status")


@router.post("/stripe/webhook")
async def handle_stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    """Handle Stripe webhook events for payouts"""
    from ..core.config import settings as core_settings

    if core_settings.is_prod and not stripe_signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    try:
        payload = await request.body()
        signature = stripe_signature or ""
        result = PayoutService.handle_webhook(db, payload, signature)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Dwolla Endpoints (gated behind ENABLE_DWOLLA_PAYOUTS) ---

import os as _os

_ENABLE_DWOLLA = _os.getenv("ENABLE_DWOLLA_PAYOUTS", "false").lower() == "true"


@router.post("/dwolla/webhook")
async def handle_dwolla_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Handle Dwolla webhook events for ACH payout lifecycle."""
    if not _ENABLE_DWOLLA:
        return {"status": "ignored", "reason": "dwolla_disabled"}

    import json

    from ..services.dwolla_webhook_service import DwollaWebhookService

    payload = await request.body()

    # Verify signature in production
    dwolla_secret = _os.getenv("DWOLLA_WEBHOOK_SECRET", "")
    from ..core.config import settings as core_settings

    if core_settings.is_prod:
        signature = request.headers.get("X-Request-Signature-SHA-256", "")
        if not DwollaWebhookService.verify_signature(payload, signature, dwolla_secret):
            logger.warning("Invalid Dwolla webhook signature")
            return {"status": "error", "reason": "invalid_signature"}

    try:
        body = json.loads(payload)
        topic = body.get("topic", "")
        resource_url = ""
        links = body.get("_links", {})
        if "resource" in links:
            resource_url = links["resource"].get("href", "")

        result = DwollaWebhookService.handle_event(db, topic, resource_url)
        return result
    except Exception as e:
        logger.error(f"Dwolla webhook processing error: {e}")
        return {"status": "error", "reason": str(e)}


class DwollaAccountRequest(BaseModel):
    email: str = ""
    first_name: str = "Driver"
    last_name: str = ""


@router.post("/dwolla/account")
async def create_dwolla_account(
    request: DwollaAccountRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create or retrieve Dwolla receive-only customer for driver."""
    if not _ENABLE_DWOLLA:
        raise HTTPException(
            status_code=400, detail="Dwolla payouts are not enabled. Use Stripe Connect instead."
        )

    from ..services.dwolla_payout_provider import DwollaPayoutProvider

    wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == current_user.id).first()

    if not wallet:
        import uuid

        wallet = DriverWallet(
            id=str(uuid.uuid4()),
            driver_id=current_user.id,
            balance_cents=0,
            pending_balance_cents=0,
        )
        db.add(wallet)
        db.flush()

    if wallet.external_account_id:
        return {
            "dwolla_customer_url": wallet.external_account_id,
            "status": "existing",
            "payout_provider": wallet.payout_provider,
        }

    try:
        provider = DwollaPayoutProvider()
        email = (
            request.email
            or getattr(current_user, "email", "")
            or f"driver-{current_user.id}@nerava.network"
        )
        customer_url = provider.create_account(
            current_user.id,
            email,
            first_name=request.first_name or "Driver",
            last_name=request.last_name or str(current_user.id),
        )
        wallet.external_account_id = customer_url
        wallet.payout_provider = "dwolla"
        from datetime import datetime as dt

        wallet.updated_at = dt.utcnow()
        db.commit()

        logger.info(f"Created Dwolla account for driver {current_user.id}: {customer_url}")
        return {
            "dwolla_customer_url": customer_url,
            "status": "created",
            "payout_provider": "dwolla",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create Dwolla account for driver {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payout account")


# Admin endpoints for testing/debugging
@router.post("/admin/credit")
async def admin_credit_wallet(
    driver_id: int,
    amount_cents: int,
    reference_type: str = "bonus",
    description: str = "Admin credit",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Admin endpoint to credit a driver's wallet"""
    # Check admin role
    if not current_user.admin_role:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Item 37: Max single-credit limit of $500 (50000 cents)
    if amount_cents > 50000:
        raise HTTPException(
            status_code=400, detail="Single credit cannot exceed $500 (50000 cents)"
        )

    try:
        import uuid

        result = PayoutService.credit_wallet(
            db, driver_id, amount_cents, reference_type, str(uuid.uuid4()), description
        )
        # Item 37: Structured audit log for admin wallet credits
        logger.info(
            "admin_wallet_credit",
            extra={
                "admin_user_id": current_user.id,
                "driver_id": driver_id,
                "amount_cents": amount_cents,
                "reference_type": reference_type,
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
