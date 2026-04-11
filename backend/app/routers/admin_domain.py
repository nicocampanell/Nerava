"""
Domain Charge Party MVP Admin Router
Admin endpoints for overview, merchant management, and manual Nova grants
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import require_admin
from app.models import User
from app.models_domain import DomainMerchant, DriverWallet, NovaTransaction, StripePayment
from app.models_extra import CreditLedger
from app.routers.drivers_wallet import _add_ledger, _balance
from app.services.analytics import get_analytics_client
from app.services.audit import log_admin_action, log_wallet_mutation
from app.services.nova_service import NovaService
from app.services.stripe_service import StripeService
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/admin", tags=["admin-v1"])

logger = get_logger(__name__)


class GrantNovaRequest(BaseModel):
    target: Literal["driver", "merchant"]
    driver_user_id: Optional[int] = None
    merchant_id: Optional[str] = None
    amount: int
    reason: str
    idempotency_key: Optional[str] = None  # Optional idempotency key for deduplication


class RevenueBreakdown(BaseModel):
    campaign_gross_cents: int = 0
    campaign_platform_fees_cents: int = 0
    campaign_driver_rewards_cents: int = 0
    merchant_subscriptions_cents: int = 0
    active_subscriptions: int = 0
    nova_sales_cents: int = 0
    merchant_fees_cents: int = 0
    arrival_billing_cents: int = 0
    total_realized_cents: int = 0
    total_driver_payouts_cents: int = 0


class AdminOverviewResponse(BaseModel):
    total_drivers: int
    total_merchants: int
    total_chargers: int = 0
    total_charging_sessions: int = 0
    active_campaigns: int = 0
    total_driver_nova: int
    total_merchant_nova: int
    total_nova_outstanding: int
    total_stripe_usd: int
    total_tesla_connections: int = 0
    total_stripe_express_onboarded: int = 0
    revenue: Optional[RevenueBreakdown] = None


@router.get("/health")
def get_admin_health(admin: User = Depends(require_admin)):
    """
    Get system health status for admin console.

    Returns /readyz status to surface system health in admin UI.
    """
    from urllib.parse import urlparse

    import redis
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    from app.config import settings
    from app.db import get_engine

    checks = {
        "startup_validation": {"status": "ok", "error": None},
        "database": {"status": "unknown", "error": None},
        "redis": {"status": "unknown", "error": None},
    }

    # Check database with timeout
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"]["status"] = "ok"
    except Exception as e:
        checks["database"]["status"] = "error"
        checks["database"]["error"] = str(e)

    # Check Redis with timeout
    try:
        redis_url = settings.redis_url
        parsed = urlparse(redis_url)
        r = redis.Redis(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            db=int(parsed.path.lstrip("/")) if parsed.path else 0,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        r.ping()
        checks["redis"]["status"] = "ok"
    except Exception as e:
        checks["redis"]["status"] = "error"
        checks["redis"]["error"] = str(e)

    # Determine overall ready status
    all_ok = all(check["status"] == "ok" for check in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(status_code=status_code, content={"ready": all_ok, "checks": checks})


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Get admin overview statistics"""
    # Count drivers (users with driver role)
    total_drivers = db.query(User).filter(User.role_flags.contains("driver")).count()

    # Count merchants (While You Charge merchants — the real ones with perks)
    from app.models.while_you_charge import Charger as WYCCharger
    from app.models.while_you_charge import Merchant as WYCMerchant

    total_merchants = db.query(WYCMerchant).count()

    # Count chargers
    total_chargers = db.query(WYCCharger).count()

    # Count total charging sessions
    from app.models.session_event import SessionEvent

    total_charging_sessions = db.query(SessionEvent).count()

    # Count active campaigns
    from datetime import datetime as dt

    from app.models.campaign import Campaign

    now = dt.utcnow()
    active_campaigns = (
        db.query(Campaign)
        .filter(
            Campaign.status == "active",
            Campaign.start_date <= now,
            Campaign.spent_cents < Campaign.budget_cents,
        )
        .count()
    )

    # Sum driver Nova balances
    driver_nova_result = db.query(func.sum(DriverWallet.nova_balance)).scalar()
    total_driver_nova = int(driver_nova_result) if driver_nova_result else 0

    # Sum merchant Nova balances
    merchant_nova_result = db.query(func.sum(DomainMerchant.nova_balance)).scalar()
    total_merchant_nova = int(merchant_nova_result) if merchant_nova_result else 0

    # Total outstanding Nova
    total_nova_outstanding = total_driver_nova + total_merchant_nova

    # --- Revenue breakdown ---
    from app.core.config import settings
    from app.models.campaign import Campaign

    # 1. Campaign revenue — ALL campaigns that have been active (not just funding_status='funded')
    # Some campaigns may have been activated without going through Stripe checkout
    active_statuses = ("active", "paused", "exhausted", "completed")
    campaigns_with_revenue = (
        db.query(Campaign)
        .filter(
            or_(
                Campaign.funding_status == "funded",
                Campaign.status.in_(active_statuses),
                Campaign.spent_cents > 0,
            )
        )
        .all()
    )

    campaign_gross = 0
    campaign_fees = 0
    campaign_driver_rewards = 0
    fee_bps = getattr(settings, "PLATFORM_FEE_BPS", 2000)

    for c in campaigns_with_revenue:
        # Use stored values if available, otherwise calculate from budget
        if c.gross_funding_cents:
            campaign_gross += c.gross_funding_cents
            campaign_fees += c.platform_fee_cents or 0
        elif c.budget_cents:
            # Campaign was funded without fee extraction — gross = budget
            campaign_gross += c.budget_cents
            # Calculate implied platform fee
            campaign_fees += int(c.budget_cents * fee_bps / (10000 + fee_bps))
        campaign_driver_rewards += c.spent_cents or 0

    # 2. Merchant subscription revenue (amounts stored in Stripe, not DB)
    import stripe as stripe_module

    from app.models.merchant_subscription import MerchantSubscription

    active_subs = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.status == "active",
        )
        .all()
    )

    subscription_revenue = 0
    for sub in active_subs:
        if sub.stripe_customer_id:
            try:
                invoices = stripe_module.Invoice.list(
                    customer=sub.stripe_customer_id,
                    status="paid",
                    limit=100,
                )
                for inv in invoices.data:
                    subscription_revenue += inv.amount_paid or 0
            except Exception as e:
                logger.debug("Stripe invoice fetch failed for %s: %s", sub.stripe_customer_id, e)

    # 3. Legacy Nova sales (merchant Stripe checkout for Nova)
    nova_sales = (
        db.query(func.sum(StripePayment.amount_usd))
        .filter(
            StripePayment.status == "paid",
        )
        .scalar()
    )
    nova_sales = int(nova_sales) if nova_sales else 0

    # 4. Merchant redemption fees (15% of Nova redeemed)
    from app.models_domain import MerchantFeeLedger

    merchant_fees = (
        db.query(func.sum(MerchantFeeLedger.fee_cents))
        .filter(
            MerchantFeeLedger.status.in_(["invoiced", "paid"]),
        )
        .scalar()
    )
    merchant_fees = int(merchant_fees) if merchant_fees else 0

    # 5. Arrival billing fees
    from app.models.billing_event import BillingEvent

    arrival_billing = (
        db.query(func.sum(BillingEvent.billable_cents))
        .filter(
            BillingEvent.status == "paid",
        )
        .scalar()
    )
    arrival_billing = int(arrival_billing) if arrival_billing else 0

    total_realized = (
        campaign_gross + subscription_revenue + nova_sales + merchant_fees + arrival_billing
    )

    # Driver payouts (outflow)
    from app.models.driver_wallet import Payout

    driver_payouts = (
        db.query(func.sum(Payout.amount_cents))
        .filter(
            Payout.status.in_(["paid", "processing"]),
        )
        .scalar()
    )
    driver_payouts = int(driver_payouts) if driver_payouts else 0

    revenue = RevenueBreakdown(
        campaign_gross_cents=campaign_gross,
        campaign_platform_fees_cents=campaign_fees,
        campaign_driver_rewards_cents=campaign_driver_rewards,
        merchant_subscriptions_cents=subscription_revenue,
        active_subscriptions=len(active_subs),
        nova_sales_cents=nova_sales,
        merchant_fees_cents=merchant_fees,
        arrival_billing_cents=arrival_billing,
        total_realized_cents=total_realized,
        total_driver_payouts_cents=driver_payouts,
    )

    # Keep legacy field populated with total for backward compat
    total_stripe_usd = total_realized

    # Count active Tesla connections (cars on the network)
    from app.models.tesla_connection import TeslaConnection

    total_tesla_connections = (
        db.query(TeslaConnection).filter(TeslaConnection.is_active == True).count()
    )

    # Count completed Stripe Express onboardings
    total_stripe_express = (
        db.query(DriverWallet)
        .filter(
            DriverWallet.stripe_account_id.isnot(None),
            DriverWallet.stripe_onboarding_complete == True,
        )
        .count()
    )

    return AdminOverviewResponse(
        total_drivers=total_drivers,
        total_merchants=total_merchants,
        total_chargers=total_chargers,
        total_charging_sessions=total_charging_sessions,
        active_campaigns=active_campaigns,
        total_driver_nova=total_driver_nova,
        total_merchant_nova=total_merchant_nova,
        total_nova_outstanding=total_nova_outstanding,
        total_stripe_usd=total_stripe_usd,
        total_tesla_connections=total_tesla_connections,
        total_stripe_express_onboarded=total_stripe_express,
        revenue=revenue,
    )


@router.get("/sessions/history")
def list_session_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    start_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    driver_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all charging sessions with driver info and reward status."""
    from app.models.session_event import IncentiveGrant, SessionEvent

    q = db.query(SessionEvent).order_by(SessionEvent.session_start.desc())

    if driver_id:
        q = q.filter(SessionEvent.driver_user_id == driver_id)
    if start_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            q = q.filter(SessionEvent.session_start >= sd)
        except ValueError:
            pass
    if end_date:
        try:
            ed = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(
                days=1
            )
            q = q.filter(SessionEvent.session_start < ed)
        except ValueError:
            pass

    total = q.count()
    sessions = q.offset(offset).limit(limit).all()

    # Batch-load driver info and grants
    driver_ids = list({s.driver_user_id for s in sessions if s.driver_user_id})
    session_ids = [s.id for s in sessions]

    drivers_map = {}
    if driver_ids:
        drivers = db.query(User).filter(User.id.in_(driver_ids)).all()
        drivers_map = {d.id: d for d in drivers}

    grants_map = {}
    if session_ids:
        grants = (
            db.query(IncentiveGrant).filter(IncentiveGrant.session_event_id.in_(session_ids)).all()
        )
        grants_map = {g.session_event_id: g for g in grants}

    rows = []
    for s in sessions:
        driver = drivers_map.get(s.driver_user_id)
        grant = grants_map.get(s.id)
        driver_name = None
        if driver:
            if driver.display_name:
                parts = driver.display_name.split()
                driver_name = f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else parts[0]
            elif driver.phone:
                driver_name = f"***{driver.phone[-4:]}"

        rows.append(
            {
                "id": s.id,
                "driver_id": s.driver_user_id,
                "driver_name": driver_name,
                "session_start": s.session_start.isoformat() if s.session_start else None,
                "session_end": s.session_end.isoformat() if s.session_end else None,
                "duration_minutes": s.duration_minutes,
                "kwh_delivered": s.kwh_delivered,
                "charger_id": s.charger_id,
                "charger_network": s.charger_network,
                "quality_score": s.quality_score,
                "ended_reason": s.ended_reason,
                "source": s.source,
                "has_reward": grant is not None,
                "reward_cents": grant.amount_cents if grant else None,
                "reward_status": grant.status if grant else None,
                "campaign_id": grant.campaign_id if grant else None,
            }
        )

    return {"sessions": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/merchants")
def list_merchants(
    zone_slug: Optional[str] = Query(None, description="Filter by zone slug"),
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500, description="Number of merchants per page"),
    offset: int = Query(0, ge=0, description="Number of merchants to skip"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List merchants with filters and pagination"""
    # Fix N+1 query: Use subquery join for last activity
    from sqlalchemy import func

    last_activity_subq = (
        db.query(
            NovaTransaction.merchant_id,
            func.max(NovaTransaction.created_at).label("last_active_at"),
        )
        .group_by(NovaTransaction.merchant_id)
        .subquery()
    )

    # Join with merchants query and select both merchant and last_active_at
    merchants_with_activity = db.query(
        DomainMerchant, last_activity_subq.c.last_active_at
    ).outerjoin(last_activity_subq, DomainMerchant.id == last_activity_subq.c.merchant_id)

    if zone_slug:
        merchants_with_activity = merchants_with_activity.filter(
            DomainMerchant.zone_slug == zone_slug
        )
    if status_filter:
        merchants_with_activity = merchants_with_activity.filter(
            DomainMerchant.status == status_filter
        )

    # Get total count before pagination
    total_count = merchants_with_activity.count()

    # Apply pagination
    merchants_with_activity = (
        merchants_with_activity.order_by(DomainMerchant.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Build response
    merchant_list = []
    for row in merchants_with_activity:
        # Handle tuple result from join
        if isinstance(row, tuple):
            merchant = row[0]
            last_active_at = row[1] if len(row) > 1 else None
        else:
            merchant = row
            last_active_at = None
        merchant_list.append(
            {
                "id": merchant.id,
                "name": merchant.name,
                "zone_slug": merchant.zone_slug,
                "status": merchant.status,
                "nova_balance": merchant.nova_balance,
                "last_active_at": last_active_at.isoformat() if last_active_at else None,
                "created_at": merchant.created_at.isoformat(),
            }
        )

    return {"merchants": merchant_list, "total": total_count, "limit": limit, "offset": offset}


@router.post("/nova/grant")
def grant_nova(
    request: GrantNovaRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Manually grant Nova to driver or merchant (admin only)"""
    try:
        if request.target == "driver":
            if not request.driver_user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="driver_user_id required for driver target",
                )

            # Require idempotency key in non-local environments
            from app.core.env import is_local_env

            idempotency_key = request.idempotency_key
            if not idempotency_key:
                if not is_local_env():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="idempotency_key is required in non-local environment",
                    )
                # In local, generate deterministic fallback for dev only
                idempotency_key = f"grant_driver_{request.driver_user_id}_{request.amount}"

            # Get wallet before grant
            from app.models_domain import DriverWallet

            wallet_before = (
                db.query(DriverWallet)
                .filter(DriverWallet.user_id == request.driver_user_id)
                .first()
            )
            before_balance = wallet_before.nova_balance if wallet_before else 0

            transaction = NovaService.grant_to_driver(
                db=db,
                driver_id=request.driver_user_id,
                amount=request.amount,
                type="admin_grant",
                idempotency_key=idempotency_key,
                metadata={"reason": request.reason, "granted_by": admin.id},
            )

            # Get wallet after grant
            db.refresh(wallet_before) if wallet_before else None
            wallet_after = (
                db.query(DriverWallet)
                .filter(DriverWallet.user_id == request.driver_user_id)
                .first()
            )
            after_balance = wallet_after.nova_balance if wallet_after else 0

            # P1-1: Admin audit log
            log_admin_action(
                db=db,
                actor_id=admin.id,
                action="admin_grant_driver",
                target_type="wallet",
                target_id=str(request.driver_user_id),
                before_json={"nova_balance": before_balance},
                after_json={"nova_balance": after_balance},
                metadata={
                    "reason": request.reason,
                    "amount": request.amount,
                    "transaction_id": transaction.id,
                },
            )
            db.commit()  # Commit audit log

            return {
                "success": True,
                "transaction_id": transaction.id,
                "target": "driver",
                "driver_user_id": request.driver_user_id,
                "amount": request.amount,
            }

        elif request.target == "merchant":
            if not request.merchant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="merchant_id required for merchant target",
                )

            # Require idempotency key in non-local environments
            from app.core.env import is_local_env

            idempotency_key = request.idempotency_key
            if not idempotency_key:
                if not is_local_env():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="idempotency_key is required in non-local environment",
                    )
                # In local, generate deterministic fallback for dev only
                idempotency_key = f"grant_merchant_{request.merchant_id}_{request.amount}"

            # Get merchant balance before grant
            from app.models_domain import DomainMerchant

            merchant_before = (
                db.query(DomainMerchant).filter(DomainMerchant.id == request.merchant_id).first()
            )
            before_balance = merchant_before.nova_balance if merchant_before else 0

            transaction = NovaService.grant_to_merchant(
                db=db,
                merchant_id=request.merchant_id,
                amount=request.amount,
                type="admin_grant",
                idempotency_key=idempotency_key,
                metadata={"reason": request.reason, "granted_by": admin.id},
            )

            # Get merchant balance after grant
            db.refresh(merchant_before) if merchant_before else None
            merchant_after = (
                db.query(DomainMerchant).filter(DomainMerchant.id == request.merchant_id).first()
            )
            after_balance = merchant_after.nova_balance if merchant_after else 0

            # P1-1: Admin audit log
            log_admin_action(
                db=db,
                actor_id=admin.id,
                action="admin_grant_merchant",
                target_type="merchant_balance",
                target_id=request.merchant_id,
                before_json={"nova_balance": before_balance},
                after_json={"nova_balance": after_balance},
                metadata={
                    "reason": request.reason,
                    "amount": request.amount,
                    "transaction_id": transaction.id,
                },
            )
            db.commit()  # Commit audit log

            return {
                "success": True,
                "transaction_id": transaction.id,
                "target": "merchant",
                "merchant_id": request.merchant_id,
                "amount": request.amount,
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="target must be 'driver' or 'merchant'",
            )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/payments/{payment_id}/reconcile")
async def reconcile_payment(
    payment_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """
    Admin endpoint to reconcile a payment with status 'unknown'.

    If payment status is not 'unknown', returns current payment summary (no-op).
    If payment is 'unknown', calls Stripe to check transfer status and updates accordingly.
    """
    try:
        # Call reconciliation logic (async wrapper for sync Stripe calls)
        result = await StripeService.reconcile_payment_async(db, payment_id)

        # Fetch full payment details for response
        payment_row = db.execute(
            text(
                """
            SELECT id, status, stripe_transfer_id, stripe_status,
                   error_code, error_message, reconciled_at, no_transfer_confirmed
            FROM payments
            WHERE id = :payment_id
        """
            ),
            {"payment_id": payment_id},
        ).first()

        if not payment_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Payment {payment_id} not found"
            )

        # Audit log
        logger.info(
            f"Admin reconciliation triggered: payment_id={payment_id}, admin_id={admin.id}, result_status={result.get('status')}"
        )

        # Build response with all required fields
        response = {
            "payment_id": payment_row[0] or payment_id,
            "status": payment_row[1] or result.get("status"),
            "stripe_transfer_id": payment_row[2],
            "stripe_status": payment_row[3],
            "error_code": payment_row[4],
            "error_message": payment_row[5],
            "reconciled_at": payment_row[6].isoformat() if payment_row[6] else None,
            "no_transfer_confirmed": bool(payment_row[7]) if payment_row[7] is not None else None,
        }

        # Add message from reconciliation result if present
        if "message" in result:
            response["message"] = result["message"]

        return response

    except ValueError as e:
        # Payment not found
        if "not found" in str(e).lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error in admin reconcile endpoint: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconciliation failed: {str(e)}",
        )


# P1-2: Admin API endpoints


class WalletAdjustRequest(BaseModel):
    """Request to manually adjust user wallet"""

    amount_cents: int  # Positive for credit, negative for debit
    reason: str


class UserResponse(BaseModel):
    """User response model"""

    id: int
    public_id: str
    email: str
    role_flags: str
    is_active: bool
    created_at: str


class UserWalletResponse(BaseModel):
    """User wallet response model"""

    user_id: int
    balance_cents: int
    nova_balance: int
    transactions: List[dict]


class MerchantStatusResponse(BaseModel):
    """Merchant status response model"""

    merchant_id: str
    name: str
    status: str
    square_connected: bool
    square_last_error: Optional[str]
    nova_balance: int


class GooglePlaceCandidatesResponse(BaseModel):
    """Google Places candidates response"""

    candidates: List[dict]


class GooglePlaceResolveRequest(BaseModel):
    """Request to resolve Google Place ID"""

    place_id: str


@router.get("/users", response_model=List[UserResponse])
def search_users(
    query: Optional[str] = Query(None, description="Search by name, email, or public_id"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Search users by name, email, or public_id.

    P1-2: Admin API endpoint for user search.
    """
    q = db.query(User)

    if query:
        # Search by email, public_id, or name (if name column exists)
        search_filter = or_(User.email.ilike(f"%{query}%"), User.public_id.ilike(f"%{query}%"))
        # Try to search by name if column exists
        try:
            if hasattr(User, "name"):
                search_filter = or_(search_filter, User.name.ilike(f"%{query}%"))
        except Exception as e:
            logger.debug(f"Error checking User.name attribute: {e}")
            pass
        q = q.filter(search_filter)

    users = q.order_by(User.created_at.desc()).limit(50).all()

    return [
        UserResponse(
            id=user.id,
            public_id=user.public_id,
            email=user.email,
            role_flags=user.role_flags or "",
            is_active=user.is_active,
            created_at=user.created_at.isoformat() if user.created_at else "",
        )
        for user in users
    ]


@router.get("/users/{user_id}/wallet", response_model=UserWalletResponse)
def get_user_wallet(
    user_id: int = Path(..., description="User ID"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Get user wallet balance and transaction history.

    P1-2: Admin API endpoint for viewing user wallet.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
        )

    # Get wallet balance from credit_ledger
    balance_cents = _balance(db, str(user_id))

    # Get Nova balance from DriverWallet
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user_id).first()
    nova_balance = wallet.nova_balance if wallet else 0

    # Get transaction history
    transactions = []
    try:
        ledger_entries = (
            db.query(CreditLedger)
            .filter(CreditLedger.user_ref == str(user_id))
            .order_by(CreditLedger.id.desc())
            .limit(50)
            .all()
        )

        transactions = [
            {
                "id": entry.id,
                "cents": entry.cents,
                "reason": entry.reason,
                "meta": entry.meta or {},
                "created_at": entry.created_at.isoformat() if entry.created_at else "",
            }
            for entry in ledger_entries
        ]
    except Exception as e:
        logger.warning(f"Could not fetch transaction history: {e}")

    return UserWalletResponse(
        user_id=user_id,
        balance_cents=balance_cents,
        nova_balance=nova_balance,
        transactions=transactions,
    )


@router.post("/users/{user_id}/wallet/adjust")
def adjust_user_wallet(
    user_id: int = Path(..., description="User ID"),
    request: WalletAdjustRequest = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Manually adjust user wallet balance (creates ledger entry + audit log).

    P1-2: Admin API endpoint for manual wallet adjustments.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
        )

    # Get balance before adjustment
    before_balance = _balance(db, str(user_id))

    # Add ledger entry
    new_balance = _add_ledger(
        db,
        str(user_id),
        request.amount_cents,
        "ADMIN_ADJUST",
        {"reason": request.reason, "admin_id": admin.id},
    )

    # P1-1: Admin audit log
    log_wallet_mutation(
        db=db,
        actor_id=admin.id,
        action="admin_adjust",
        user_id=str(user_id),
        before_balance=before_balance,
        after_balance=new_balance,
        amount=request.amount_cents,
        metadata={"reason": request.reason, "admin_id": admin.id},
    )
    db.commit()

    return {
        "success": True,
        "user_id": user_id,
        "amount_cents": request.amount_cents,
        "before_balance_cents": before_balance,
        "after_balance_cents": new_balance,
    }


@router.get("/merchants/search", response_model=dict)
def search_merchants(
    query: Optional[str] = Query(None, description="Search by merchant name or ID"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Search merchants by name or ID.

    P1-2: Admin API endpoint for merchant search.
    """
    q = db.query(DomainMerchant)

    if query:
        q = q.filter(
            or_(DomainMerchant.name.ilike(f"%{query}%"), DomainMerchant.id.ilike(f"%{query}%"))
        )

    merchants = q.order_by(DomainMerchant.created_at.desc()).limit(50).all()

    return {
        "merchants": [
            {
                "id": merchant.id,
                "name": merchant.name,
                "status": merchant.status,
                "zone_slug": merchant.zone_slug,
                "nova_balance": merchant.nova_balance,
                "created_at": merchant.created_at.isoformat() if merchant.created_at else "",
            }
            for merchant in merchants
        ]
    }


@router.get("/merchants/{merchant_id}/status", response_model=MerchantStatusResponse)
def get_merchant_status(
    merchant_id: str = Path(..., description="Merchant ID"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Get merchant status including Square token status and last error.

    P1-2: Admin API endpoint for merchant status.
    """
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Merchant {merchant_id} not found"
        )

    # Check Square connection status
    square_connected = bool(merchant.square_access_token and merchant.square_connected_at)

    # Get last error from recent transactions or payments
    square_last_error = None
    try:
        # Check for failed Stripe payments (if any)
        last_payment = (
            db.query(StripePayment)
            .filter(StripePayment.merchant_id == merchant_id, StripePayment.status == "failed")
            .order_by(StripePayment.created_at.desc())
            .first()
        )

        if last_payment and last_payment.error_message:
            square_last_error = last_payment.error_message
    except Exception:
        pass

    return MerchantStatusResponse(
        merchant_id=merchant.id,
        name=merchant.name,
        status=merchant.status,
        square_connected=square_connected,
        square_last_error=square_last_error,
        nova_balance=merchant.nova_balance,
    )


@router.get(
    "/locations/{location_id}/google-place/candidates", response_model=GooglePlaceCandidatesResponse
)
def get_google_place_candidates(
    location_id: str = Path(..., description="Location ID (merchant ID)"),
    query: Optional[str] = Query(None, description="Search query for Google Places"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Get Google Places candidates for a merchant location.

    P1-2: Admin API endpoint for Google Places mapping.
    """
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == location_id).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Merchant {location_id} not found"
        )

    # Use merchant name and location for search
    search_query = query or merchant.name
    lat = merchant.lat
    lng = merchant.lng

    # Call Google Places API (if configured)
    candidates = []
    try:
        import os

        google_places_api_key = os.getenv("GOOGLE_PLACES_API_KEY")
        if google_places_api_key:
            import httpx

            # Use Places API Text Search
            url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
            params = {
                "query": search_query,
                "location": f"{lat},{lng}",
                "radius": 5000,  # 5km radius
                "key": google_places_api_key,
            }

            response = httpx.get(url, params=params, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                candidates = [
                    {
                        "place_id": result.get("place_id"),
                        "name": result.get("name"),
                        "formatted_address": result.get("formatted_address"),
                        "geometry": result.get("geometry", {}),
                        "rating": result.get("rating"),
                        "types": result.get("types", []),
                    }
                    for result in data.get("results", [])[:10]  # Limit to 10
                ]
        else:
            logger.warning("GOOGLE_PLACES_API_KEY not configured")
    except Exception as e:
        logger.error(f"Error fetching Google Places candidates: {e}")

    return GooglePlaceCandidatesResponse(candidates=candidates)


@router.post("/locations/{location_id}/google-place/resolve")
def resolve_google_place(
    location_id: str = Path(..., description="Location ID (merchant ID)"),
    request: GooglePlaceResolveRequest = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Resolve Google Place ID for a merchant location.

    P1-2: Admin API endpoint for resolving Google Place ID.
    """
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == location_id).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Merchant {location_id} not found"
        )

    # Fetch place details from Google Places API
    place_details = None
    try:
        import os

        google_places_api_key = os.getenv("GOOGLE_PLACES_API_KEY")
        if google_places_api_key:
            import httpx

            url = "https://maps.googleapis.com/maps/api/place/details/json"
            params = {
                "place_id": request.place_id,
                "fields": "place_id,name,formatted_address,geometry,rating,types,website,international_phone_number",
                "key": google_places_api_key,
            }

            response = httpx.get(url, params=params, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "OK":
                    place_details = data.get("result", {})
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GOOGLE_PLACES_API_KEY not configured",
            )
    except Exception as e:
        logger.error(f"Error fetching Google Place details: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch place details: {str(e)}",
        )

    if not place_details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Place ID {request.place_id} not found"
        )

    # Update merchant with Google Place ID
    merchant.google_place_id = request.place_id
    # Optionally update other fields from place_details
    if place_details.get("formatted_address"):
        # Parse address if needed (simplified)
        merchant.addr_line1 = (
            place_details.get("formatted_address", "").split(",")[0]
            if place_details.get("formatted_address")
            else None
        )

    db.commit()

    # P1-1: Admin audit log
    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="admin_resolve_google_place",
        target_type="merchant",
        target_id=location_id,
        before_json={"google_place_id": merchant.google_place_id},
        after_json={"google_place_id": request.place_id},
        metadata={"place_id": request.place_id, "place_name": place_details.get("name")},
    )
    db.commit()

    return {
        "success": True,
        "merchant_id": location_id,
        "google_place_id": request.place_id,
        "place_details": place_details,
    }


# Exclusive Management Endpoints
@router.get("/exclusives")
def list_all_exclusives(
    merchant_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    List all exclusives (optionally filtered by merchant_id).
    """
    import json

    from app.models.while_you_charge import MerchantPerk

    query = db.query(MerchantPerk)
    if merchant_id:
        query = query.filter(MerchantPerk.merchant_id == merchant_id)

    perks = query.all()
    exclusives = []

    for perk in perks:
        try:
            metadata = json.loads(perk.description or "{}")
            if metadata.get("is_exclusive"):
                exclusives.append(
                    {
                        "id": str(perk.id),
                        "merchant_id": perk.merchant_id,
                        "title": perk.title,
                        "description": metadata.get("description") or perk.description,
                        "daily_cap": metadata.get("daily_cap"),
                        "session_cap": metadata.get("session_cap"),
                        "eligibility": metadata.get("eligibility", "charging_only"),
                        "is_active": perk.is_active,
                        "created_at": perk.created_at.isoformat(),
                        "updated_at": perk.updated_at.isoformat(),
                    }
                )
        except Exception as e:
            logger.debug(f"Error processing perk {perk.id}: {e}")
            continue

    # Enrich with merchant names
    from app.models.while_you_charge import Merchant

    merchant_ids = list(set(e["merchant_id"] for e in exclusives if e.get("merchant_id")))
    merchants_map = {}
    if merchant_ids:
        merchants = db.query(Merchant).filter(Merchant.id.in_(merchant_ids)).all()
        merchants_map = {m.id: m.name for m in merchants}

    # Count today's activations per merchant (since ExclusiveSession doesn't have perk_id)
    from datetime import datetime, timezone

    from app.models.exclusive_session import ExclusiveSession

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    activation_counts = {}
    try:
        # Count sessions by merchant_id activated today
        active_sessions = (
            db.query(ExclusiveSession.merchant_id, func.count(ExclusiveSession.id))
            .filter(ExclusiveSession.activated_at >= today_start)
            .group_by(ExclusiveSession.merchant_id)
            .all()
        )
        for merchant_id, count in active_sessions:
            if merchant_id:
                activation_counts[merchant_id] = count
    except Exception:
        # If query fails, leave counts as 0
        pass

    # Enrich exclusives with merchant_name and activations_today
    for ex in exclusives:
        ex["merchant_name"] = merchants_map.get(ex["merchant_id"], "Unknown")
        # Use merchant-level activation count as approximation
        ex["activations_today"] = activation_counts.get(ex["merchant_id"], 0)
        ex["activations_this_month"] = 0  # placeholder
        ex["nova_reward"] = 0  # placeholder

    return {
        "exclusives": exclusives,
        "total": len(exclusives),
        "limit": len(exclusives),
        "offset": 0,
    }


class ToggleExclusiveRequest(BaseModel):
    reason: str = ""


@router.post("/exclusives/{exclusive_id}/toggle")
def toggle_exclusive_flag(
    http_request: Request,
    exclusive_id: str = Path(...),
    enabled: Optional[bool] = Query(
        None, description="Enable or disable (optional, toggles if not provided)"
    ),
    body: Optional[ToggleExclusiveRequest] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Admin toggle exclusive flag (enable/disable).
    Accepts both query param (?enabled=bool) and JSON body ({reason}).
    If enabled query param is not provided, toggles current state.
    """
    from app.models.while_you_charge import MerchantPerk

    perk = db.query(MerchantPerk).filter(MerchantPerk.id == int(exclusive_id)).first()
    if not perk:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exclusive not found")

    # Determine new state: use query param if provided, otherwise toggle
    if enabled is not None:
        new_state = enabled
    else:
        new_state = not perk.is_active

    prev_state = perk.is_active
    perk.is_active = new_state
    db.commit()

    # Store reason in metadata if provided
    reason = body.reason if body and body.reason else ""
    metadata = {"reason": reason} if reason else {}

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="admin_toggle_exclusive",
        target_type="exclusive",
        target_id=exclusive_id,
        before_json={"is_active": prev_state},
        after_json={"is_active": new_state, **metadata},
    )
    db.commit()

    # Analytics: Capture admin exclusive toggle
    request_id = getattr(http_request.state, "request_id", None)
    analytics = get_analytics_client()
    analytics.capture(
        event="server.admin.exclusive.toggle",
        distinct_id=admin.public_id,
        request_id=request_id,
        user_id=admin.public_id,
        ip=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        properties={
            "exclusive_id": exclusive_id,
            "enabled": enabled,
        },
    )

    return {"ok": True, "is_active": enabled}


# Demo Location Override
class DemoLocationRequest(BaseModel):
    lat: float
    lng: float
    charger_id: Optional[str] = None


@router.post("/demo/location")
def set_demo_location(
    request: DemoLocationRequest,
    http_request: Request,
    *,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Set static demo driver location (for demos/testing).

    Security requirements:
    - Requires admin authentication (enforced by require_admin)
    - Requires DEMO_STATIC_DRIVER_ENABLED=true (disabled by default in prod)
    - All actions are audited

    Production safety: This endpoint is disabled unless explicitly enabled via env var.
    """
    import os

    from app.config import settings

    # Check if demo mode is enabled (must be explicitly set to "true")
    demo_enabled = os.getenv("DEMO_STATIC_DRIVER_ENABLED", "false").lower() == "true"

    # In production, also check ENV setting
    if settings.env == "production" and not demo_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo static driver mode is disabled in production",
        )

    if not demo_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo static driver mode is not enabled. Set DEMO_STATIC_DRIVER_ENABLED=true to enable.",
        )

    # Store in database table (more secure than env vars)
    # For MVP, we'll use a simple table or cache
    # In production, use Redis or database table with TTL

    # Store demo location in audit metadata (for now)
    # TODO: Create dedicated DemoLocation table with TTL
    demo_location_key = f"demo_location_{admin.id}"

    # Log admin action BEFORE setting location (audit trail)
    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="admin_set_demo_location",
        target_type="demo",
        target_id=demo_location_key,
        before_json={},  # Could store previous location if exists
        after_json={"lat": request.lat, "lng": request.lng, "charger_id": request.charger_id},
        metadata={
            "lat": request.lat,
            "lng": request.lng,
            "charger_id": request.charger_id,
            "enabled": demo_enabled,
            "env": settings.env,
        },
    )
    db.commit()

    # Store in environment for runtime access (temporary - should use DB/Redis)
    # This is acceptable for MVP but should be replaced with proper storage
    os.environ["DEMO_STATIC_LAT"] = str(request.lat)
    os.environ["DEMO_STATIC_LNG"] = str(request.lng)
    if request.charger_id:
        os.environ["DEMO_STATIC_CHARGER_ID"] = request.charger_id

    logger.info(
        f"Demo location set by admin {admin.id}: lat={request.lat}, lng={request.lng}, charger_id={request.charger_id}"
    )

    # Analytics: Capture demo location override
    request_id = getattr(http_request.state, "request_id", None)
    analytics = get_analytics_client()
    analytics.capture(
        event="server.admin.demo_location.override",
        distinct_id=admin.public_id,
        request_id=request_id,
        user_id=admin.public_id,
        ip=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        properties={
            "latitude": request.lat,
            "longitude": request.lng,
            "charger_id": request.charger_id,
        },
    )

    return {
        "ok": True,
        "lat": request.lat,
        "lng": request.lng,
        "charger_id": request.charger_id,
        "set_by": admin.id,
        "set_at": datetime.utcnow().isoformat(),
    }


# Audit Log Viewer
@router.get("/audit")
def get_audit_logs(
    http_request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Get audit logs (basic viewer).
    """
    from app.models_extra import AdminAuditLog

    query = db.query(AdminAuditLog)

    if action:
        query = query.filter(AdminAuditLog.action == action)
    if target_type:
        query = query.filter(AdminAuditLog.target_type == target_type)

    logs = query.order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset).all()

    # Analytics: Capture audit log view
    request_id = getattr(http_request.state, "request_id", None)
    analytics = get_analytics_client()
    analytics.capture(
        event="server.admin.audit_log.view",
        distinct_id=admin.public_id,
        request_id=request_id,
        user_id=admin.public_id,
        ip=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        properties={
            "filter": action or target_type or None,
        },
    )

    return {
        "logs": [
            {
                "id": log.id,
                "actor_id": log.actor_id,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
                "metadata": log.metadata if hasattr(log, "metadata") else {},
            }
            for log in logs
        ],
        "total": query.count(),
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/active")
def get_active_sessions(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Get all active exclusive sessions."""
    from datetime import datetime, timezone

    from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus

    now = datetime.now(timezone.utc)
    sessions = (
        db.query(ExclusiveSession)
        .filter(
            ExclusiveSession.status == ExclusiveSessionStatus.ACTIVE,
            ExclusiveSession.expires_at > now,
        )
        .all()
    )

    result = []
    for s in sessions:
        remaining = (s.expires_at - now).total_seconds() / 60 if s.expires_at else 0
        result.append(
            {
                "id": str(s.id),
                "driver_id": s.driver_id,
                "merchant_id": s.merchant_id,
                "merchant_name": None,  # enrich later if needed
                "charger_id": s.charger_id,
                "charger_name": None,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "activated_at": s.activated_at.isoformat() if s.activated_at else None,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "time_remaining_minutes": round(max(0, remaining), 1),
            }
        )

    return {"sessions": result, "total_active": len(result)}


class ForceCloseRequest(BaseModel):
    location_id: str
    reason: str = Field(..., min_length=10)


@router.post("/sessions/force-close")
def force_close_sessions(
    request: ForceCloseRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """Force close all active sessions at a specific charger location."""
    from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus

    sessions = (
        db.query(ExclusiveSession)
        .filter(
            ExclusiveSession.status == ExclusiveSessionStatus.ACTIVE,
            ExclusiveSession.charger_id == request.location_id,
        )
        .all()
    )

    count = 0
    for s in sessions:
        s.status = ExclusiveSessionStatus.CANCELED
        count += 1

    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="force_close_sessions",
        target_type="location",
        target_id=request.location_id,
        after_json={"sessions_closed": count, "reason": request.reason},
    )
    db.commit()

    return {
        "location_id": request.location_id,
        "sessions_closed": count,
        "closed_by": admin.public_id,
        "reason": request.reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class EmergencyPauseRequest(BaseModel):
    action: str  # "activate" or "deactivate"
    reason: str = Field(..., min_length=10)
    confirmation: str


@router.post("/overrides/emergency-pause")
def emergency_pause(
    request: EmergencyPauseRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Emergency pause/unpause all exclusives."""
    if request.action == "activate" and request.confirmation != "CONFIRM-EMERGENCY-PAUSE":
        raise HTTPException(status_code=400, detail="Invalid confirmation token")

    from app.models.while_you_charge import MerchantPerk

    if request.action == "activate":
        updated = (
            db.query(MerchantPerk)
            .filter(MerchantPerk.is_active == True)
            .update({"is_active": False})
        )
        db.commit()
    else:
        updated = 0  # deactivation does not auto-reactivate; manual per-exclusive

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action=f"emergency_pause_{request.action}",
        target_type="system",
        target_id="all",
        after_json={"reason": request.reason, "exclusives_affected": updated},
    )
    db.commit()

    return {
        "action": request.action,
        "activated_by": admin.public_id,
        "reason": request.reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/logs")
def get_logs(
    http_request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get audit logs with enriched schema matching frontend expectations."""
    from app.models_extra import AdminAuditLog

    query = db.query(AdminAuditLog)

    if type:
        query = query.filter(AdminAuditLog.action.contains(type))
    if search:
        query = query.filter(
            AdminAuditLog.action.contains(search) | AdminAuditLog.target_id.contains(search)
        )

    total = query.count()
    logs = query.order_by(AdminAuditLog.created_at.desc()).limit(limit).offset(offset).all()

    # Enrich with user email lookup
    actor_ids = list(set(log.actor_id for log in logs if log.actor_id))
    users = (
        {u.id: u for u in db.query(User).filter(User.id.in_(actor_ids)).all()} if actor_ids else {}
    )

    return {
        "logs": [
            {
                "id": str(log.id),
                "operator_id": log.actor_id,
                "operator_email": (
                    users.get(log.actor_id).email if users.get(log.actor_id) else None
                ),
                "action_type": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "reason": (log.metadata_json or {}).get("reason") if log.metadata_json else None,
                "ip_address": (log.metadata_json or {}).get("ip") if log.metadata_json else None,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/merchants/{merchant_id}/pause")
def pause_merchant(
    merchant_id: str = Path(...),
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Pause a merchant."""
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    prev = merchant.status
    merchant.status = "paused"
    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="pause_merchant",
        target_type="merchant",
        target_id=merchant_id,
        before_json={"status": prev},
        after_json={"status": "paused", "reason": reason},
    )
    db.commit()

    return {
        "merchant_id": merchant_id,
        "action": "pause",
        "previous_status": prev,
        "new_status": "paused",
        "reason": reason,
    }


@router.post("/merchants/{merchant_id}/resume")
def resume_merchant(
    merchant_id: str = Path(...),
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Resume a merchant."""
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    prev = merchant.status
    merchant.status = "active"
    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="resume_merchant",
        target_type="merchant",
        target_id=merchant_id,
        before_json={"status": prev},
        after_json={"status": "active", "reason": reason},
    )
    db.commit()

    return {
        "merchant_id": merchant_id,
        "action": "resume",
        "previous_status": prev,
        "new_status": "active",
        "reason": reason,
    }


@router.post("/merchants/{merchant_id}/ban")
def ban_merchant(
    merchant_id: str = Path(...),
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Ban a merchant."""
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    prev = merchant.status
    merchant.status = "banned"
    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="ban_merchant",
        target_type="merchant",
        target_id=merchant_id,
        before_json={"status": prev},
        after_json={"status": "banned", "reason": reason},
    )
    db.commit()

    return {
        "merchant_id": merchant_id,
        "action": "ban",
        "previous_status": prev,
        "new_status": "banned",
        "reason": reason,
    }


@router.post("/merchants/{merchant_id}/verify")
def verify_merchant(
    merchant_id: str = Path(...),
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Verify a merchant."""
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    prev = merchant.status
    merchant.status = "verified"
    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="verify_merchant",
        target_type="merchant",
        target_id=merchant_id,
        before_json={"status": prev},
        after_json={"status": "verified", "reason": reason},
    )
    db.commit()

    return {
        "merchant_id": merchant_id,
        "action": "verify",
        "previous_status": prev,
        "new_status": "verified",
        "reason": reason,
    }


@router.post("/exclusives/{exclusive_id}/ban")
def ban_exclusive(
    exclusive_id: str = Path(...),
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Permanently disable an exclusive."""
    from app.models.while_you_charge import MerchantPerk

    exclusive = db.query(MerchantPerk).filter(MerchantPerk.id == int(exclusive_id)).first()
    if not exclusive:
        raise HTTPException(status_code=404, detail="Exclusive not found")

    exclusive.is_active = False
    db.commit()

    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="ban_exclusive",
        target_type="exclusive",
        target_id=exclusive_id,
        after_json={"is_active": False, "reason": reason},
    )
    db.commit()

    return {"exclusive_id": exclusive_id, "action": "ban", "reason": reason}


@router.post("/merchants/{merchant_id}/send-portal-link")
def send_portal_link(
    merchant_id: str = Path(...),
    email: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Send portal claim link to merchant email."""
    from app.core.config import settings

    portal_url = f"{settings.FRONTEND_URL}/merchant/claim/{merchant_id}"
    # In production, send email via existing email service.
    # For now, log the action and return the link.
    log_admin_action(
        db=db,
        actor_id=admin.id,
        action="send_portal_link",
        target_type="merchant",
        target_id=merchant_id,
        after_json={"email": email, "portal_url": portal_url},
    )
    db.commit()

    return {"success": True}


class DeploymentTriggerRequest(BaseModel):
    target: Literal["backend", "driver", "admin", "merchant"]
    ref: str = "main"


@router.post("/deployments/trigger")
async def trigger_deployment(
    request: DeploymentTriggerRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Trigger GitHub Actions deployment workflow."""
    GITHUB_TOKEN = os.getenv("GITHUB_DEPLOY_TOKEN")
    GITHUB_REPO = os.getenv("GITHUB_REPO", "jameskirk/nerava")  # Update with your repo

    if not GITHUB_TOKEN:
        raise HTTPException(status_code=500, detail="GitHub token not configured")

    # Map target to workflow file
    workflows = {
        "backend": "deploy-backend.yml",
        "driver": "deploy-driver.yml",
        "admin": "deploy-admin.yml",
        "merchant": "deploy-merchant.yml",
    }

    workflow = workflows.get(request.target)
    if not workflow:
        raise HTTPException(status_code=400, detail=f"Invalid target: {request.target}")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow}/dispatches",
                headers={
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                },
                json={"ref": request.ref},
                timeout=10.0,
            )

            if response.status_code != 204:
                error_text = response.text
                logger.error(f"GitHub API error: {response.status_code} - {error_text}")
                raise HTTPException(
                    status_code=500,
                    detail=f"GitHub API error: {response.status_code} - {error_text}",
                )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="GitHub API timeout")
        except Exception as e:
            logger.error(f"Failed to trigger deployment: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to trigger deployment: {str(e)}")

    # Log admin action
    log_admin_action(
        db=db,
        actor_id=current_user.id,
        action="trigger_deployment",
        target_type="deployment",
        target_id=request.target,
        metadata={"workflow": workflow, "ref": request.ref},
    )

    return {"status": "triggered", "workflow": workflow, "target": request.target}


class DemoSimulateRequest(BaseModel):
    driver_phone: str
    merchant_id: str
    charger_id: str


@router.post("/system/pause")
def pause_system(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Pause the system (kill switch).

    Sets Redis flag to pause all non-admin endpoints.
    Returns 503 for non-admin endpoints until resumed.
    """
    import redis

    from app.config import settings

    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.set("system:paused", "1")

        logger.warning(f"System paused by admin {admin.id}")

        return {"ok": True, "message": "System paused. Non-admin endpoints will return 503."}
    except Exception as e:
        logger.error(f"Failed to pause system: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to pause system: {str(e)}",
        )


@router.post("/system/resume")
def resume_system(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Resume the system (disable kill switch).

    Removes Redis flag to allow all endpoints again.
    """
    import redis

    from app.config import settings

    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        redis_client.delete("system:paused")

        logger.info(f"System resumed by admin {admin.id}")

        return {"ok": True, "message": "System resumed. All endpoints are active."}
    except Exception as e:
        logger.error(f"Failed to resume system: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume system: {str(e)}",
        )


@router.post("/internal/demo/simulate-verified-visit")
async def simulate_verified_visit(
    request: DemoSimulateRequest,
    x_internal_secret: str = Header(..., alias="X-Internal-Secret"),
    db: Session = Depends(get_db),
):
    """
    DEV ONLY: Simulate a verified visit for demo purposes.
    Creates ExclusiveSession + RewardEvent.
    """
    INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
    if not INTERNAL_SECRET or x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret")

    from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus
    from app.models_extra import RewardEvent

    # Find or create driver
    driver = db.query(User).filter(User.phone == request.driver_phone).first()
    if not driver:
        driver = User(
            phone=request.driver_phone,
            auth_provider="phone",
            role_flags="driver",
        )
        db.add(driver)
        db.flush()

    # Create exclusive session
    session = ExclusiveSession(
        id=uuid.uuid4(),
        driver_id=driver.id,
        merchant_id=request.merchant_id,
        charger_id=request.charger_id,
        status=ExclusiveSessionStatus.COMPLETED,
        activated_at=datetime.utcnow() - timedelta(minutes=15),
        expires_at=datetime.utcnow() + timedelta(hours=1),  # Set expires_at
        completed_at=datetime.utcnow(),
    )
    db.add(session)

    # Create reward event
    reward = RewardEvent(
        user_id=driver.id,
        source="MERCHANT",
        gross_cents=0,
        community_cents=0,
        net_cents=0,
        meta={"demo": True, "merchant_id": request.merchant_id},
    )
    db.add(reward)

    db.commit()

    return {
        "session_id": str(session.id),
        "driver_id": driver.public_id if hasattr(driver, "public_id") else str(driver.id),
        "status": "simulated",
    }


# ==============================================================================
# Bulk Seed Jobs (Charger + Merchant seeding)
# ==============================================================================

import threading
from datetime import timezone as tz

_seed_jobs: dict[str, dict] = {}  # job_id -> {type, status, started_at, progress, result, error}


def _run_seed_chargers_job(job_id: str, states: Optional[List[str]]):
    """Background thread for charger seeding."""
    import asyncio

    from scripts.seed_chargers_bulk import seed_chargers

    from app.db import SessionLocal

    _seed_jobs[job_id]["status"] = "running"
    db = SessionLocal()
    try:

        def on_progress(state, fetched, total):
            _seed_jobs[job_id]["progress"] = {
                "current_state": state,
                "total_fetched": fetched,
                "total_states": total,
            }

        result = asyncio.run(seed_chargers(db, states=states, progress_callback=on_progress))
        _seed_jobs[job_id]["status"] = "completed"
        _seed_jobs[job_id]["result"] = result
        _seed_jobs[job_id]["completed_at"] = datetime.now(tz.utc).isoformat()
    except Exception as e:
        logger.error(f"Seed chargers job {job_id} failed: {e}")
        _seed_jobs[job_id]["status"] = "failed"
        _seed_jobs[job_id]["error"] = str(e)
    finally:
        db.close()


def _run_seed_merchants_job(job_id: str, max_cells: Optional[int]):
    """Background thread for merchant seeding."""
    import asyncio

    from scripts.seed_merchants_free import seed_merchants

    from app.db import SessionLocal

    _seed_jobs[job_id]["status"] = "running"
    db = SessionLocal()
    try:

        def on_progress(done, total):
            _seed_jobs[job_id]["progress"] = {
                "cells_done": done,
                "total_cells": total,
            }

        result = asyncio.run(seed_merchants(db, max_cells=max_cells, progress_callback=on_progress))
        _seed_jobs[job_id]["status"] = "completed"
        _seed_jobs[job_id]["result"] = result
        _seed_jobs[job_id]["completed_at"] = datetime.now(tz.utc).isoformat()
    except Exception as e:
        logger.error(f"Seed merchants job {job_id} failed: {e}")
        _seed_jobs[job_id]["status"] = "failed"
        _seed_jobs[job_id]["error"] = str(e)
    finally:
        db.close()


class SeedChargersRequest2(BaseModel):
    states: Optional[List[str]] = Field(
        None, description="State codes to seed (default: all 50 + DC)"
    )


class SeedMerchantsRequest(BaseModel):
    max_cells: Optional[int] = Field(None, description="Max grid cells to process (None = all)")


@router.post("/seed/chargers")
def start_charger_seed(
    request: SeedChargersRequest2 = Body(default=SeedChargersRequest2()),
    admin: User = Depends(require_admin),
):
    """Start a background job to seed chargers from NREL AFDC."""
    # Check for already running charger seed
    for jid, job in _seed_jobs.items():
        if job["type"] == "chargers" and job["status"] == "running":
            return {
                "job_id": jid,
                "status": "already_running",
                "message": "A charger seed job is already running",
            }

    job_id = f"charger_seed_{uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": "chargers",
        "status": "starting",
        "started_at": datetime.now(tz.utc).isoformat(),
        "started_by": admin.id,
        "progress": {},
        "result": None,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_seed_chargers_job,
        args=(job_id, request.states),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "status": "started"}


@router.post("/seed/merchants")
def start_merchant_seed(
    request: SeedMerchantsRequest = Body(default=SeedMerchantsRequest()),
    admin: User = Depends(require_admin),
):
    """Start a background job to map merchants using Overpass API."""
    for jid, job in _seed_jobs.items():
        if job["type"] == "merchants" and job["status"] == "running":
            return {
                "job_id": jid,
                "status": "already_running",
                "message": "A merchant seed job is already running",
            }

    job_id = f"merchant_seed_{uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": "merchants",
        "status": "starting",
        "started_at": datetime.now(tz.utc).isoformat(),
        "started_by": admin.id,
        "progress": {},
        "result": None,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_seed_merchants_job,
        args=(job_id, request.max_cells),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "status": "started"}


@router.get("/seed/status")
def get_seed_status(
    admin: User = Depends(require_admin),
):
    """Get status of all seed jobs."""
    return {"jobs": _seed_jobs}


# --- Seed-key authenticated versions (no admin JWT needed) ---


@router.post("/seed-key/chargers")
def start_charger_seed_key(
    request: SeedChargersRequest2 = Body(default=SeedChargersRequest2()),
    x_seed_key: Optional[str] = Header(None),
):
    """Start charger seed via seed key (no JWT needed)."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    for jid, job in _seed_jobs.items():
        if job["type"] == "chargers" and job["status"] == "running":
            return {"job_id": jid, "status": "already_running"}
    job_id = f"charger_seed_{uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": "chargers",
        "status": "starting",
        "started_at": datetime.now(tz.utc).isoformat(),
        "started_by": 0,
        "progress": {},
        "result": None,
        "error": None,
    }
    threading.Thread(
        target=_run_seed_chargers_job, args=(job_id, request.states), daemon=True
    ).start()
    return {"job_id": job_id, "status": "started"}


@router.post("/seed-key/merchants")
def start_merchant_seed_key(
    request: SeedMerchantsRequest = Body(default=SeedMerchantsRequest()),
    x_seed_key: Optional[str] = Header(None),
):
    """Start merchant seed via seed key (no JWT needed)."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    for jid, job in _seed_jobs.items():
        if job["type"] == "merchants" and job["status"] == "running":
            return {"job_id": jid, "status": "already_running"}
    job_id = f"merchant_seed_{uuid.uuid4().hex[:8]}"
    _seed_jobs[job_id] = {
        "type": "merchants",
        "status": "starting",
        "started_at": datetime.now(tz.utc).isoformat(),
        "started_by": 0,
        "progress": {},
        "result": None,
        "error": None,
    }
    threading.Thread(
        target=_run_seed_merchants_job, args=(job_id, request.max_cells), daemon=True
    ).start()
    return {"job_id": job_id, "status": "started"}


def _run_seed_grid_job(job_id: str, states: Optional[List[str]], batch_size: int):
    """Background thread for grid-based charger seeding."""
    import asyncio

    from scripts.seed_chargers_grid import seed_chargers_grid

    from app.db import SessionLocal

    def on_progress(metro_name, total_unique, total_metros):
        _seed_jobs[job_id]["progress"] = {
            "current_metro": metro_name,
            "total_unique": total_unique,
            "total_metros": total_metros,
        }

    db = SessionLocal()
    try:
        result = asyncio.run(
            seed_chargers_grid(
                db, states=states, batch_size=batch_size, progress_callback=on_progress
            )
        )
        _seed_jobs[job_id]["status"] = "completed"
        _seed_jobs[job_id]["result"] = result
    except Exception as e:
        _seed_jobs[job_id]["status"] = "failed"
        _seed_jobs[job_id]["error"] = str(e)
        logger.error(f"Grid seed job {job_id} failed: {e}")
    finally:
        db.close()


@router.post("/seed-key/chargers-grid")
def start_grid_seed_key(
    states: Optional[str] = None,
    batch_size: int = 0,
    reset: bool = False,
    x_seed_key: Optional[str] = Header(None),
):
    """Start grid-based charger seed (covers all US metros). No JWT needed."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    # Check for running grid job
    for jid, job in _seed_jobs.items():
        if job["type"] == "chargers_grid" and job["status"] == "running":
            return {"job_id": jid, "status": "already_running", "progress": job.get("progress")}
    if reset:
        import os

        progress_file = "/tmp/nrel_grid_progress.json"
        if os.path.exists(progress_file):
            os.remove(progress_file)
    job_id = f"grid_seed_{uuid.uuid4().hex[:8]}"
    state_list = states.split(",") if states else None
    _seed_jobs[job_id] = {
        "type": "chargers_grid",
        "status": "running",
        "started_at": datetime.now(tz.utc).isoformat(),
        "started_by": 0,
        "progress": {},
        "result": None,
        "error": None,
    }
    threading.Thread(
        target=_run_seed_grid_job, args=(job_id, state_list, batch_size), daemon=True
    ).start()
    return {"job_id": job_id, "status": "started"}


@router.get("/seed-key/status")
def get_seed_status_key(
    x_seed_key: Optional[str] = Header(None),
):
    """Get seed job status via seed key."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {"jobs": _seed_jobs}


@router.post("/db-query")
def admin_db_query(
    query: str = Body(..., embed=True),
    x_seed_key: Optional[str] = Header(None, alias="X-Seed-Key"),
    db: Session = Depends(get_db),
):
    """Execute a read-only SQL query against the production DB. Protected by seed key.
    Only SELECT queries are allowed — no INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Block write operations
    q = query.strip().upper()
    blocked = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "EXEC",
    ]
    for word in blocked:
        if q.startswith(word) or f" {word} " in q or f";{word}" in q:
            raise HTTPException(status_code=400, detail=f"Write operations not allowed: {word}")

    if not q.startswith("SELECT") and not q.startswith("WITH") and not q.startswith("EXPLAIN"):
        raise HTTPException(status_code=400, detail="Only SELECT/WITH/EXPLAIN queries allowed")

    from sqlalchemy import text

    try:
        result = db.execute(text(query))
        columns = list(result.keys()) if result.returns_rows else []
        rows = [dict(zip(columns, row)) for row in result.fetchall()] if result.returns_rows else []
        return {
            "columns": columns,
            "rows": rows[:1000],
            "row_count": len(rows),
            "truncated": len(rows) > 1000,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Query error: {str(e)}")


# ─── P0 Fix Endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/force-close")
def admin_force_close_session(
    session_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Force-close a zombie session. Sets quality_score=0, ended_reason=admin_force_close."""
    from app.models.session_event import SessionEvent

    session = db.query(SessionEvent).filter(SessionEvent.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.session_end:
        return {"ok": True, "action": "already_ended", "session_id": session_id}
    session.session_end = datetime.utcnow()
    session.duration_minutes = (
        int((session.session_end - session.session_start).total_seconds() / 60)
        if session.session_start
        else 0
    )
    session.quality_score = 0
    session.ended_reason = "admin_force_close"
    session.updated_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "action": "force_closed",
        "session_id": session_id,
        "duration_minutes": session.duration_minutes,
    }


@router.post("/payouts/{payout_id}/force-fail")
def admin_force_fail_payout(
    payout_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Force-fail a stuck payout and restore the driver's wallet balance."""
    from app.models.driver_wallet import DriverWallet, Payout, WalletLedger
    from app.services.payout_service import calculate_withdrawal_fee

    payout = db.query(Payout).filter(Payout.id == payout_id).first()
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")
    if payout.status in ("paid", "failed"):
        return {"ok": True, "action": "already_" + payout.status, "payout_id": payout_id}

    wallet = db.query(DriverWallet).filter(DriverWallet.id == payout.wallet_id).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")

    fee_cents = calculate_withdrawal_fee(payout.amount_cents)
    payout.status = "failed"
    payout.failure_reason = "admin_force_fail_reconciliation"
    payout.updated_at = datetime.utcnow()
    wallet.pending_balance_cents -= payout.amount_cents
    wallet.balance_cents += payout.amount_cents + fee_cents
    wallet.updated_at = datetime.utcnow()

    # Create reversal ledger entry
    reversal = WalletLedger(
        id=str(uuid.uuid4()),
        wallet_id=wallet.id,
        driver_id=payout.driver_id,
        amount_cents=payout.amount_cents + fee_cents,
        balance_after_cents=wallet.balance_cents,
        transaction_type="reversal",
        reference_type="payout_force_fail",
        reference_id=payout.id,
        description=f"Admin force-fail reversal for payout {payout_id}",
    )
    db.add(reversal)
    db.commit()
    return {
        "ok": True,
        "action": "force_failed",
        "payout_id": payout_id,
        "restored_cents": payout.amount_cents + fee_cents,
        "new_balance": wallet.balance_cents,
    }


@router.post("/campaigns/reconcile")
def admin_reconcile_campaigns(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Reconcile all campaign spent_cents and sessions_granted from actual grants."""
    from sqlalchemy import func

    from app.models.campaign import Campaign
    from app.models.session_event import IncentiveGrant

    campaigns = db.query(Campaign).all()
    fixed = []
    for c in campaigns:
        actual_spent = (
            db.query(func.coalesce(func.sum(IncentiveGrant.amount_cents), 0))
            .filter(IncentiveGrant.campaign_id == c.id)
            .scalar()
        )
        actual_count = (
            db.query(func.count(IncentiveGrant.id))
            .filter(IncentiveGrant.campaign_id == c.id)
            .scalar()
        )

        if c.spent_cents != actual_spent or c.sessions_granted != actual_count:
            old_spent = c.spent_cents
            old_count = c.sessions_granted
            c.spent_cents = actual_spent
            c.sessions_granted = actual_count
            c.updated_at = datetime.utcnow()
            # Auto-exhaust if over budget
            if c.spent_cents >= c.budget_cents and c.status == "active":
                c.status = "exhausted"
            fixed.append(
                {
                    "campaign_id": str(c.id),
                    "name": c.name,
                    "old_spent": old_spent,
                    "new_spent": actual_spent,
                    "old_sessions": old_count,
                    "new_sessions": actual_count,
                    "status": c.status,
                }
            )

    db.commit()
    return {"ok": True, "campaigns_fixed": len(fixed), "details": fixed}


@router.post("/wallets/reconcile-nova")
def admin_reconcile_nova(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Reconcile nova_balance from nova_transactions for all wallets."""
    from sqlalchemy import text

    from app.models.driver_wallet import DriverWallet

    # Check if nova_transactions table exists and has data
    try:
        result = db.execute(
            text(
                "SELECT driver_id, SUM(CASE WHEN type IN ('driver_earn','admin_grant') THEN amount ELSE -amount END) as balance "
                "FROM nova_transactions GROUP BY driver_id"
            )
        )
        nova_balances = {row[0]: int(row[1] or 0) for row in result}
    except Exception:
        nova_balances = {}

    wallets = db.query(DriverWallet).all()
    fixed = []
    for w in wallets:
        expected = nova_balances.get(w.driver_id, 0)
        if w.nova_balance != expected:
            old = w.nova_balance
            w.nova_balance = max(0, expected)
            w.updated_at = datetime.utcnow()
            fixed.append({"driver_id": w.driver_id, "old_nova": old, "new_nova": w.nova_balance})

    db.commit()
    return {"ok": True, "wallets_fixed": len(fixed), "details": fixed}


@router.get("/seed-key/stats")
def get_seed_stats_key(
    x_seed_key: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Get charger/merchant counts via seed key."""
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    from app.models.while_you_charge import Charger, ChargerMerchant, Merchant

    charger_count = db.query(func.count(Charger.id)).scalar() or 0
    merchant_count = db.query(func.count(Merchant.id)).scalar() or 0
    junction_count = db.query(func.count(ChargerMerchant.id)).scalar() or 0
    return {"chargers": charger_count, "merchants": merchant_count, "junctions": junction_count}


@router.get("/seed/stats")
def get_seed_stats(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get current charger + merchant counts."""
    from sqlalchemy import func

    from app.models.while_you_charge import Charger, ChargerMerchant, Merchant

    charger_count = db.query(func.count(Charger.id)).scalar() or 0
    merchant_count = db.query(func.count(Merchant.id)).scalar() or 0
    junction_count = db.query(func.count(ChargerMerchant.id)).scalar() or 0

    # Get last updated timestamps
    last_charger = db.query(func.max(Charger.updated_at)).scalar()
    last_merchant = db.query(func.max(Merchant.updated_at)).scalar()

    return {
        "charger_count": charger_count,
        "merchant_count": merchant_count,
        "junction_count": junction_count,
        "last_charger_update": last_charger.isoformat() if last_charger else None,
        "last_merchant_update": last_merchant.isoformat() if last_merchant else None,
    }


@router.get("/otp/diagnostics")
async def otp_diagnostics(
    admin=Depends(require_admin),
):
    """
    OTP system diagnostics — checks Twilio credentials, Verify service, and rate-limit state.
    Admin-only endpoint for debugging OTP issues in production.
    """
    import logging

    from app.core.config import settings as core_settings
    from app.services.auth import get_otp_provider, get_rate_limit_service

    diag_logger = logging.getLogger("nerava.otp_diag")

    result = {
        "otp_provider": core_settings.OTP_PROVIDER,
        "env": core_settings.ENV,
        "twilio_account_sid_set": bool(core_settings.TWILIO_ACCOUNT_SID),
        "twilio_auth_token_set": bool(core_settings.TWILIO_AUTH_TOKEN),
        "twilio_verify_service_sid_set": bool(core_settings.TWILIO_VERIFY_SERVICE_SID),
        "twilio_verify_service_sid_prefix": (
            core_settings.TWILIO_VERIFY_SERVICE_SID[:8] + "..."
            if core_settings.TWILIO_VERIFY_SERVICE_SID
            else "NOT SET"
        ),
        "twilio_timeout_seconds": core_settings.TWILIO_TIMEOUT_SECONDS,
        "provider_instantiation": "unknown",
        "twilio_verify_service_check": "unknown",
    }

    # Try to instantiate the provider
    try:
        provider = get_otp_provider(None)
        result["provider_instantiation"] = f"ok ({provider.__class__.__name__})"
    except Exception as e:
        result["provider_instantiation"] = f"FAILED: {str(e)}"
        diag_logger.error(f"[OTP Diagnostics] Provider instantiation failed: {e}")

    # If Twilio Verify, check the service exists
    if core_settings.OTP_PROVIDER == "twilio_verify" and core_settings.TWILIO_VERIFY_SERVICE_SID:
        try:
            import asyncio

            from twilio.http.http_client import TwilioHttpClient
            from twilio.rest import Client

            custom_http_client = TwilioHttpClient()
            custom_http_client.timeout = 10

            client = Client(
                core_settings.TWILIO_ACCOUNT_SID,
                core_settings.TWILIO_AUTH_TOKEN,
                http_client=custom_http_client,
            )

            def _fetch_service():
                return client.verify.v2.services(core_settings.TWILIO_VERIFY_SERVICE_SID).fetch()

            service = await asyncio.wait_for(
                asyncio.to_thread(_fetch_service),
                timeout=15,
            )
            result["twilio_verify_service_check"] = (
                f"ok (friendly_name={service.friendly_name}, sid={service.sid})"
            )
        except Exception as e:
            result["twilio_verify_service_check"] = f"FAILED: {str(e)}"
            diag_logger.error(f"[OTP Diagnostics] Twilio Verify service check failed: {e}")

    # Check rate-limit state
    try:
        rl_service = get_rate_limit_service()
        result["rate_limit_backend"] = rl_service.__class__.__name__
    except Exception as e:
        result["rate_limit_backend"] = f"FAILED: {str(e)}"

    return result


# ==============================================================================
# Charger Seeding Endpoints
# ==============================================================================


class SeedChargersRequest(BaseModel):
    charger_ids: Optional[List[str]] = Field(
        None, description="List of charger IDs to seed. If empty, seeds all 5 Austin chargers."
    )


class SeedChargersResponse(BaseModel):
    success: bool
    chargers_created: int
    chargers_updated: int
    merchants_created: int
    merchants_updated: int
    links_created: int
    errors: List[str]


@router.get("/seed-chargers/available")
async def get_available_chargers(admin: User = Depends(require_admin)):
    """
    Get list of chargers available for seeding.

    Returns the 5 Austin chargers that can be seeded.
    """
    from app.services.charger_seeder import ChargerSeederService

    # Just use the static list, no DB needed
    seeder = ChargerSeederService(None)
    return {"chargers": seeder.get_charger_list()}


@router.post("/seed-chargers", response_model=SeedChargersResponse)
async def seed_chargers(
    request: SeedChargersRequest = Body(default=SeedChargersRequest()),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Seed chargers with nearby merchants from Google Places API.

    This endpoint:
    1. Creates/updates charger records for the 5 Austin Tesla Superchargers
    2. Fetches nearby restaurants/cafes from Google Places API
    3. Creates merchant records with ratings, categories, etc.
    4. Links merchants to chargers with distance and walk time
    5. Sets the primary merchant for each charger

    Requires GOOGLE_PLACES_API_KEY environment variable to be set.

    Idempotent: Safe to run multiple times.
    """
    from app.core.config import settings
    from app.services.charger_seeder import ChargerSeederService

    # Check API key
    if not settings.GOOGLE_PLACES_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_PLACES_API_KEY not configured",
        )

    logger.info(f"Admin {admin.id} triggered charger seeding")

    seeder = ChargerSeederService(db)

    try:
        results = await seeder.seed_all_chargers(request.charger_ids)

        logger.info(f"Charger seeding complete: {results}")

        return SeedChargersResponse(
            success=len(results["errors"]) == 0,
            chargers_created=results["chargers_created"],
            chargers_updated=results["chargers_updated"],
            merchants_created=results["merchants_created"],
            merchants_updated=results["merchants_updated"],
            links_created=results["links_created"],
            errors=results["errors"],
        )
    except Exception as e:
        logger.error(f"Charger seeding failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Seeding failed: {str(e)}"
        )


@router.get("/chargers")
async def list_chargers(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    List all chargers in the database with merchant counts.
    """
    from sqlalchemy import func

    from app.models.while_you_charge import Charger, ChargerMerchant

    # Query chargers with merchant count
    chargers_with_counts = (
        db.query(Charger, func.count(ChargerMerchant.merchant_id).label("merchant_count"))
        .outerjoin(ChargerMerchant, Charger.id == ChargerMerchant.charger_id)
        .group_by(Charger.id)
        .all()
    )

    return {
        "chargers": [
            {
                "id": charger.id,
                "name": charger.name,
                "address": charger.address,
                "lat": charger.lat,
                "lng": charger.lng,
                "network": charger.network_name,
                "power_kw": charger.power_kw,
                "status": charger.status,
                "merchant_count": count,
            }
            for charger, count in chargers_with_counts
        ]
    }


class NrelSeedRequest(BaseModel):
    states: Optional[List[str]] = None  # None = all 50 states + DC


@router.post("/chargers/seed-nrel")
async def seed_chargers_nrel(
    request: NrelSeedRequest = Body(default=NrelSeedRequest()),
    x_seed_key: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Seed chargers from NREL AFDC API (all US public EV chargers).
    Upserts by external_id so safe to re-run. Takes 15-30 min for all states.
    Auth: X-Seed-Key header matching JWT_SECRET.
    """
    from app.core.config import settings as cfg

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    from scripts.seed_chargers_bulk import seed_chargers

    logger.info(f"Seed-key triggered NREL charger seed, states={request.states}")

    try:
        result = await seed_chargers(db, states=request.states)
        logger.info(f"NREL seed complete: {result}")
        return {
            "success": len(result["errors"]) == 0,
            "total_fetched": result["total_fetched"],
            "inserted": result["inserted"],
            "updated": result["updated"],
            "skipped": result["skipped"],
            "states_processed": result["states_processed"],
            "errors": result["errors"],
        }
    except Exception as e:
        logger.error(f"NREL seed failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"NREL seed failed: {str(e)}",
        )


class ChargerUpsertItem(BaseModel):
    id: str
    external_id: Optional[str] = None
    name: str
    network_name: Optional[str] = None
    lat: float
    lng: float
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    connector_types: Optional[list] = None
    power_kw: Optional[float] = None
    is_public: bool = True
    status: str = "available"
    logo_url: Optional[str] = None


class ChargerBulkUpsertRequest(BaseModel):
    chargers: List[ChargerUpsertItem]


@router.post("/chargers/bulk-upsert")
async def bulk_upsert_chargers(
    request: ChargerBulkUpsertRequest,
    x_seed_key: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    Bulk upsert chargers by ID. If charger exists, updates fields; if not, inserts.
    Auth: X-Seed-Key header matching JWT_SECRET.
    """
    from app.core.config import settings as cfg
    from app.models.while_you_charge import Charger

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    inserted = 0
    updated = 0
    for item in request.chargers:
        existing = db.query(Charger).filter(Charger.id == item.id).first()
        if existing:
            for field, value in item.model_dump(exclude_none=True).items():
                setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            updated += 1
        else:
            charger = Charger(**item.model_dump())
            charger.updated_at = datetime.utcnow()
            charger.created_at = datetime.utcnow()
            db.add(charger)
            inserted += 1

    db.commit()
    return {"inserted": inserted, "updated": updated, "total": len(request.chargers)}


@router.get("/debug/sessions/{driver_id}")
async def debug_driver_sessions(
    driver_id: int,
    x_seed_key: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Temporary debug endpoint to inspect session trail data."""
    from app.core.config import settings as cfg
    from app.models.session_event import SessionEvent
    from app.services.geo import haversine_m

    if not x_seed_key or x_seed_key != cfg.JWT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    sessions = (
        db.query(SessionEvent)
        .filter(SessionEvent.driver_user_id == driver_id)
        .order_by(SessionEvent.session_start.desc())
        .limit(20)
        .all()
    )

    results = []
    for s in sessions:
        meta = s.session_metadata or {}
        trail = meta.get("location_trail", [])

        # Calculate distance from each trail point to the charger (session lat/lng)
        trail_with_distance = []
        for pt in trail:
            dist = None
            if s.lat and s.lng and pt.get("lat") and pt.get("lng"):
                dist = round(haversine_m(pt["lat"], pt["lng"], s.lat, s.lng), 1)
            trail_with_distance.append(
                {
                    "lat": pt.get("lat"),
                    "lng": pt.get("lng"),
                    "ts": pt.get("ts"),
                    "distance_to_charger_m": dist,
                }
            )

        results.append(
            {
                "session_id": s.id,
                "session_start": str(s.session_start) if s.session_start else None,
                "session_end": str(s.session_end) if s.session_end else None,
                "charger_id": s.charger_id,
                "charger_network": s.charger_network,
                "tesla_lat": s.lat,
                "tesla_lng": s.lng,
                "device_lat": meta.get("device_lat"),
                "device_lng": meta.get("device_lng"),
                "kwh_delivered": s.kwh_delivered,
                "duration_minutes": s.duration_minutes,
                "trail_points": len(trail),
                "location_trail": trail_with_distance,
            }
        )

    return {"driver_id": driver_id, "session_count": len(results), "sessions": results}


# ---------------------------------------------------------------------------
# Seed a charger (admin-only)
# ---------------------------------------------------------------------------


class SeedChargerRequest(BaseModel):
    charger_id: str
    name: str
    lat: float
    lng: float
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    network_name: Optional[str] = "Tesla"
    connector_types: Optional[str] = "Tesla"
    power_kw: Optional[float] = 250.0
    num_evse: Optional[int] = None


@router.post("/seed-charger")
def admin_seed_charger(
    request: SeedChargerRequest,
    db: Session = Depends(get_db),
    x_bootstrap_key: Optional[str] = Header(None, alias="X-Bootstrap-Key"),
):
    """Create or update a charger. Protected by BOOTSTRAP_KEY."""
    bootstrap_key = os.getenv("BOOTSTRAP_KEY") or os.getenv("JWT_SECRET")
    if not x_bootstrap_key or x_bootstrap_key != bootstrap_key:
        raise HTTPException(status_code=401, detail="Invalid X-Bootstrap-Key")

    from app.models.while_you_charge import Charger

    charger = db.query(Charger).filter(Charger.id == request.charger_id).first()
    if charger:
        charger.name = request.name
        charger.lat = request.lat
        charger.lng = request.lng
        charger.address = request.address
        charger.network_name = request.network_name
        charger.updated_at = datetime.utcnow()
        action = "updated"
    else:
        charger = Charger(
            id=request.charger_id,
            name=request.name,
            lat=request.lat,
            lng=request.lng,
            address=request.address,
            city=request.city,
            state=request.state,
            zip_code=request.zip_code,
            network_name=request.network_name,
            connector_types=request.connector_types,
            power_kw=request.power_kw,
            num_evse=request.num_evse,
            is_public=True,
            status="operational",
        )
        db.add(charger)
        action = "created"

    db.commit()
    return {"ok": True, "charger_id": request.charger_id, "action": action}


# Seed a merchant near a charger (admin-only)
# ---------------------------------------------------------------------------


class SeedMerchantRequest(BaseModel):
    charger_id: str
    merchant_name: str
    merchant_id: Optional[str] = None
    category: str = "restaurant"
    primary_category: str = "food"
    lat: float
    lng: float
    address: Optional[str] = None
    short_code: Optional[str] = None
    place_id: Optional[str] = None
    distance_m: float = 50
    walk_duration_s: int = 60
    rating: float = 4.5
    website: Optional[str] = None
    photo_url: Optional[str] = None
    perk_title: Optional[str] = None
    perk_description: Optional[str] = None
    perk_nova_reward: Optional[int] = None


@router.post("/seed-merchant")
def admin_seed_merchant(
    request: SeedMerchantRequest,
    db: Session = Depends(get_db),
    x_bootstrap_key: Optional[str] = Header(None, alias="X-Bootstrap-Key"),
):
    # Accept either admin JWT or bootstrap key
    bootstrap_key = os.getenv("BOOTSTRAP_KEY") or os.getenv("JWT_SECRET")
    if not x_bootstrap_key or x_bootstrap_key != bootstrap_key:
        raise HTTPException(status_code=401, detail="Invalid X-Bootstrap-Key")
    """
    Create a merchant and link it to an existing charger.
    Idempotent — updates if merchant_id already exists.
    """
    from app.models.while_you_charge import Charger, ChargerMerchant, Merchant, MerchantPerk

    # Verify charger exists
    charger = db.query(Charger).filter(Charger.id == request.charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail=f"Charger {request.charger_id} not found")

    merchant_id = request.merchant_id or f"m_{request.merchant_name.lower().replace(' ', '_')}"

    # Upsert merchant
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if merchant:
        merchant.name = request.merchant_name
        merchant.lat = request.lat
        merchant.lng = request.lng
        merchant.address = request.address
        merchant.category = request.category
        merchant.primary_category = request.primary_category
        merchant.rating = request.rating
        merchant.website = request.website
        merchant.place_id = request.place_id
        merchant.short_code = request.short_code
        if request.photo_url:
            merchant.photo_url = request.photo_url
            merchant.primary_photo_url = request.photo_url
        merchant.updated_at = datetime.utcnow()
    else:
        merchant = Merchant(
            id=merchant_id,
            name=request.merchant_name,
            category=request.category,
            primary_category=request.primary_category,
            lat=request.lat,
            lng=request.lng,
            address=request.address,
            short_code=request.short_code,
            place_id=request.place_id,
            region_code="ATX",
            rating=request.rating,
            website=request.website,
            nearest_charger_id=request.charger_id,
            nearest_charger_distance_m=int(request.distance_m),
        )
        db.add(merchant)

    # Upsert charger-merchant link
    link = (
        db.query(ChargerMerchant)
        .filter(
            ChargerMerchant.charger_id == request.charger_id,
            ChargerMerchant.merchant_id == merchant_id,
        )
        .first()
    )
    if link:
        link.distance_m = request.distance_m
        link.walk_duration_s = request.walk_duration_s
        link.is_primary = True
        if request.perk_title:
            link.exclusive_title = request.perk_title
            link.exclusive_description = request.perk_description
        link.updated_at = datetime.utcnow()
    else:
        link = ChargerMerchant(
            charger_id=request.charger_id,
            merchant_id=merchant_id,
            distance_m=request.distance_m,
            walk_duration_s=request.walk_duration_s,
            is_primary=True,
            exclusive_title=request.perk_title,
            exclusive_description=request.perk_description,
        )
        db.add(link)

    # Add perk if specified
    if request.perk_title:
        existing_perk = (
            db.query(MerchantPerk)
            .filter(
                MerchantPerk.merchant_id == merchant_id,
            )
            .first()
        )
        if not existing_perk:
            perk = MerchantPerk(
                merchant_id=merchant_id,
                title=request.perk_title,
                description=request.perk_description or "",
                nova_reward=request.perk_nova_reward or 10,
            )
            db.add(perk)

    db.commit()

    return {
        "ok": True,
        "merchant_id": merchant_id,
        "charger_id": request.charger_id,
        "message": f"Merchant '{request.merchant_name}' linked to charger '{charger.name}'",
    }
