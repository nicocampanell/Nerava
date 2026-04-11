"""
Checkout Router - QR-based and discovery-based Nova redemption
Handles driver checkout flow at merchants.
"""

import logging
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.env import is_local_env
from ..db import get_db
from ..dependencies_driver import get_current_driver, get_current_driver_optional
from ..models import User
from ..models.domain import DomainMerchant, DriverWallet, MerchantRedemption, MerchantReward
from ..models.while_you_charge import Charger, ChargerCluster, ChargerMerchant, Merchant
from ..services.audit import log_wallet_mutation
from ..services.merchant_fee import record_merchant_fee
from ..services.nova_service import NovaService
from ..services.qr_service import resolve_merchant_qr_token
from ..services.square_orders import (
    SquareError,
    SquareNotConnectedError,
    SquareOrderTotalUnavailableError,
    get_order_total_cents,
    search_recent_orders,
)
from ..services.wallet_activity import mark_wallet_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/checkout", tags=["checkout"])


def resolve_cluster_from_merchant(db: Session, merchant: DomainMerchant) -> Optional[str]:
    """
    Resolve cluster_id from DomainMerchant.

    Strategy:
    1. Try to find Merchant by matching name or location
    2. Find ChargerMerchant for that Merchant
    3. Find ChargerCluster for that Charger

    For party flow, also try direct lookup by zone_slug -> cluster name.

    Args:
        db: Database session
        merchant: DomainMerchant instance

    Returns:
        cluster_id (str) or None if not found
    """
    # Strategy 1: For party flow, try zone_slug -> cluster name
    if merchant.zone_slug:
        cluster = db.query(ChargerCluster).filter(ChargerCluster.name == merchant.zone_slug).first()
        if cluster:
            return str(cluster.id)

    # Strategy 2: Find Merchant by name or location match
    merchant_record = db.query(Merchant).filter(Merchant.name == merchant.name).first()

    if not merchant_record and merchant.lat and merchant.lng:
        # Try location-based match (within 50m)
        from app.services.geo import haversine_m

        all_merchants = db.query(Merchant).all()
        for m in all_merchants:
            if m.lat and m.lng:
                distance = haversine_m(merchant.lat, merchant.lng, m.lat, m.lng)
                if distance < 50:  # Within 50 meters
                    merchant_record = m
                    break

    if merchant_record:
        # Find ChargerMerchant for this Merchant
        charger_link = (
            db.query(ChargerMerchant)
            .filter(ChargerMerchant.merchant_id == merchant_record.id)
            .first()
        )

        if charger_link:
            # Find ChargerCluster for this Charger
            cluster = (
                db.query(ChargerCluster)
                .filter(ChargerCluster.charger_id == charger_link.charger_id)
                .first()
            )

            if cluster:
                return str(cluster.id)

    return None


class ChargerInfo(BaseModel):
    """Charger information for QR response"""

    address: str
    lat: float
    lng: float
    charger_radius_m: int
    merchant_radius_m: int


class CheckoutQrResponse(BaseModel):
    """Response for QR checkout lookup"""

    ok: bool
    route: str
    cluster_id: Optional[str] = None
    charger: Optional[ChargerInfo] = None


class RedeemRequest(BaseModel):
    """Request to redeem Nova at checkout"""

    qr_token: str
    order_total_cents: int
    square_order_id: Optional[str] = None  # Optional Square order ID
    idempotency_key: Optional[str] = (
        None  # P1-F: UUID for idempotency (required for non-Square redemptions)
    )


class RedeemResponse(BaseModel):
    """Response from Nova redemption"""

    success: bool
    merchant_id: str
    discount_cents: int
    order_total_cents: int
    nova_spent_cents: int
    remaining_nova_cents: int
    message: str
    redemption_id: str
    square_order_id: Optional[str] = None  # Square order ID if provided
    merchant_fee_cents: Optional[int] = None  # Merchant fee for this redemption


class RedeemRewardRequest(BaseModel):
    """Request to redeem a predefined merchant reward"""

    reward_id: str


class RedeemRewardResponse(BaseModel):
    """Response from reward redemption"""

    status: str
    nova_redeemed: int
    reward: str
    redemption_id: str
    remaining_nova_cents: int


@router.get("/qr/{token}", response_model=CheckoutQrResponse)
async def checkout_qr(
    token: str,
    http_request: Request,
    user: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    QR code scan endpoint - returns deep-link target for driver app.

    This endpoint is called when a driver scans a merchant QR code.
    It resolves the QR token to a cluster and returns routing information.

    Args:
        token: QR token from merchant sign
        user: Optional authenticated driver (for analytics)
        db: Database session

    Returns:
        JSON response with route, cluster_id, and charger info
    """
    # Resolve QR token to merchant
    merchant = resolve_merchant_qr_token(db, token)
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "INVALID_QR_TOKEN",
                "message": "QR token not found or merchant not active",
            },
        )

    # Resolve cluster_id from merchant
    cluster_id = resolve_cluster_from_merchant(db, merchant)

    # PostHog: Fire qr_scanned event
    import hashlib

    from app.services.analytics import get_analytics_client

    analytics = get_analytics_client()
    request_id = (
        getattr(http_request.state, "request_id", None) if hasattr(http_request, "state") else None
    )

    # Hash QR token for privacy
    qr_token_hashed = hashlib.sha256(token.encode()).hexdigest()[:16]

    distinct_id = user.public_id if user else "anonymous"
    analytics.capture(
        event="qr_scanned",
        distinct_id=distinct_id,
        request_id=request_id,
        user_id=user.public_id if user else None,
        merchant_id=merchant.id,
        ip=http_request.client.host if http_request.client else None,
        user_agent=(
            http_request.headers.get("user-agent") if hasattr(http_request, "headers") else None
        ),
        properties={
            "qr_token": qr_token_hashed,
            "source": "charger_sign",
            "cluster_id": cluster_id,
            "merchant_id": merchant.id,
        },
    )

    # Build route
    if cluster_id:
        route = f"/app/party?cluster_id={cluster_id}"
    else:
        # Fallback: route to app without cluster_id if cluster not found
        logger.warning(
            f"Could not resolve cluster_id for merchant {merchant.id}, routing to app without cluster"
        )
        route = "/app/party"

    # Get charger info from cluster if available
    charger_info = None
    if cluster_id:
        cluster = db.query(ChargerCluster).filter(ChargerCluster.id == cluster_id).first()
        if cluster:
            charger = db.query(Charger).filter(Charger.id == cluster.charger_id).first()
            if charger:
                charger_info = ChargerInfo(
                    address=charger.address or "",
                    lat=cluster.charger_lat or charger.lat or 0.0,
                    lng=cluster.charger_lng or charger.lng or 0.0,
                    charger_radius_m=cluster.charger_radius_m or 400,
                    merchant_radius_m=cluster.merchant_radius_m or 40,
                )

    return CheckoutQrResponse(ok=True, route=route, cluster_id=cluster_id, charger=charger_info)


class OrdersResponse(BaseModel):
    """Response for recent orders lookup"""

    merchant_id: str
    merchant_name: str
    perk: dict
    orders: List[dict]


@router.get("/orders", response_model=OrdersResponse)
async def list_recent_orders(
    token: str = Query(..., description="QR token"),
    minutes: int = Query(10, description="Minutes to look back"),
    db: Session = Depends(get_db),
):
    """
    List recent Square orders for a merchant.

    Args:
        token: QR token
        minutes: Number of minutes to look back (default: 10)
        db: Database session

    Returns:
        OrdersResponse with merchant info, perk, and recent orders
    """
    # Resolve merchant
    merchant = resolve_merchant_qr_token(db, token)
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "INVALID_QR_TOKEN",
                "message": "QR token not found or merchant not active",
            },
        )

    # Determine perk
    perk_cents = merchant.custom_perk_cents or merchant.recommended_perk_cents or 300
    perk_label = merchant.perk_label or f"{perk_cents} Nova coffee perk"

    # Try to fetch recent orders from Square
    orders = []
    try:
        orders = search_recent_orders(db, merchant, minutes=minutes, limit=20)
    except SquareNotConnectedError:
        # Merchant not connected to Square - return empty orders list
        logger.info(f"Merchant {merchant.id} not connected to Square, returning empty orders")
        orders = []
    except SquareError as e:
        # Log error but don't fail - return empty orders list
        logger.warning(f"Error fetching Square orders: {e}")
        orders = []

    return OrdersResponse(
        merchant_id=merchant.id,
        merchant_name=merchant.name,
        perk={"perk_cents": perk_cents, "label": perk_label},
        orders=orders,
    )


@router.post("/redeem", response_model=RedeemResponse)
async def redeem_nova(
    request: RedeemRequest,
    user: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Redeem Nova at merchant checkout.

    This endpoint:
    1. Resolves merchant via QR token
    2. If square_order_id is provided:
       - Fetches order total from Square
       - Prevents duplicate redemption
       - Records merchant fee
    3. P1-F: Validates idempotency_key for non-Square redemptions
    4. Calculates discount amount
    5. Validates driver has sufficient Nova
    6. Debits Nova from driver wallet
    7. Creates MerchantRedemption record

    Args:
        request: RedeemRequest with qr_token, order_total_cents, optional square_order_id, and optional idempotency_key
        user: Authenticated driver (optional in demo mode - uses user ID 1)

    Returns:
        RedeemResponse with redemption details including merchant_fee_cents

    P1-F Security: For non-Square redemptions, idempotency_key is required to prevent replay attacks.
    If same idempotency_key is reused, returns cached response from previous redemption.
    """
    # In demo mode, allow unauthenticated requests using demo user (ID 1)
    # P0-2: DEMO_MODE only works in local environment (security hardening)
    demo_mode_enabled = os.getenv("DEMO_MODE", "false").lower() == "true"
    demo_mode = demo_mode_enabled and is_local_env()

    if demo_mode_enabled and not is_local_env():
        logger.warning(
            "DEMO_MODE is enabled but environment is not local - DEMO_MODE disabled for security"
        )

    if not user:
        if demo_mode:
            # Use demo user (ID 1) for unauthenticated demo requests
            user = db.query(User).filter(User.id == 1).first()
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"error": "DEMO_USER_NOT_FOUND", "message": "Demo user not configured"},
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "UNAUTHORIZED", "message": "Authentication required"},
            )
    # Resolve merchant
    merchant = resolve_merchant_qr_token(db, request.qr_token)
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "INVALID_QR_TOKEN",
                "message": "QR token not found or merchant not active",
            },
        )

    # P1-F: Validate idempotency_key for non-Square redemptions
    # Square redemptions use square_order_id for idempotency, non-Square need idempotency_key
    if not request.square_order_id:
        if not request.idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "IDEMPOTENCY_KEY_REQUIRED",
                    "message": "idempotency_key is required for non-Square redemptions to prevent replay attacks",
                },
            )

        # Validate UUID format
        try:
            uuid.UUID(request.idempotency_key)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "INVALID_IDEMPOTENCY_KEY",
                    "message": "idempotency_key must be a valid UUID",
                },
            )

        # Check for existing redemption with same idempotency_key
        existing_redemption = (
            db.query(MerchantRedemption)
            .filter(
                MerchantRedemption.merchant_id == merchant.id,
                MerchantRedemption.idempotency_key == request.idempotency_key,
            )
            .first()
        )

        if existing_redemption:
            # Return cached response from previous redemption (early return to avoid processing)
            logger.info(
                f"[P1-F] Idempotent redemption: returning cached result for idempotency_key={request.idempotency_key}",
                extra={
                    "redemption_id": existing_redemption.id,
                    "merchant_id": merchant.id,
                    "idempotency_key": request.idempotency_key,
                },
            )

            # Get current wallet balance for response
            wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
            remaining_nova_cents = wallet.nova_balance if wallet else 0

            # Get merchant fee if it was a Square redemption
            merchant_fee_cents = None
            if existing_redemption.square_order_id:
                from app.services.merchant_fee import get_merchant_fee

                try:
                    fee = get_merchant_fee(db, existing_redemption.id)
                    merchant_fee_cents = fee.amount_cents if fee else None
                except Exception:
                    pass

            return RedeemResponse(
                success=True,
                merchant_id=existing_redemption.merchant_id,
                discount_cents=existing_redemption.discount_cents,
                order_total_cents=existing_redemption.order_total_cents,
                nova_spent_cents=existing_redemption.nova_spent_cents,
                remaining_nova_cents=remaining_nova_cents,
                message="Idempotent redemption: returning cached result",
                redemption_id=existing_redemption.id,
                square_order_id=existing_redemption.square_order_id,
                merchant_fee_cents=merchant_fee_cents,
            )

    # Handle Square order lookup if square_order_id is provided
    order_total_cents = request.order_total_cents
    if request.square_order_id:
        try:
            # Fetch order total from Square
            order_total_cents = get_order_total_cents(db, merchant, request.square_order_id)

            # P0-6: Race condition fix - rely on DB unique constraint instead of pre-check
            # The unique index on (merchant_id, square_order_id) will prevent duplicates
            # We'll catch IntegrityError and return 409 if duplicate
        except SquareNotConnectedError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "SQUARE_NOT_CONNECTED",
                    "message": "Merchant is not connected to Square",
                },
            )
        except SquareOrderTotalUnavailableError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "SQUARE_ORDER_TOTAL_UNAVAILABLE", "message": str(e)},
            )
        except SquareError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": "SQUARE_API_ERROR", "message": str(e)},
            )

    # Validate order_total_cents
    if order_total_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "INVALID_ORDER_TOTAL",
                "message": "Order total must be greater than zero",
            },
        )

    # Determine discount amount
    perk_cents = merchant.custom_perk_cents or merchant.recommended_perk_cents or 300
    # Compute redeem_cents = min(perk_cents, wallet_balance, order_total_cents)

    # Check driver Nova balance
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "WALLET_NOT_FOUND", "message": "Driver wallet not found"},
        )

    # Calculate redeem_cents = min(perk_cents, wallet_balance, order_total_cents)
    redeem_cents = min(perk_cents, wallet.nova_balance, order_total_cents)
    discount_cents = redeem_cents

    if discount_cents <= 0:
        if wallet.nova_balance < perk_cents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "INSUFFICIENT_NOVA",
                    "message": f"Insufficient Nova balance. Has {wallet.nova_balance}, needs {perk_cents}",
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "NO_DISCOUNT_AVAILABLE",
                    "message": "No discount available for this merchant",
                },
            )

    # Get wallet balance before redemption
    wallet_before = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    before_balance = wallet_before.nova_balance if wallet_before else 0

    # Generate redemption_id early so it can be referenced in audit log
    redemption_id = str(uuid.uuid4())

    # Redeem Nova via NovaService
    try:
        result = NovaService.redeem_from_driver(
            db=db,
            driver_id=user.id,
            merchant_id=merchant.id,
            amount=discount_cents,
            metadata={
                "qr_token": request.qr_token,
                "order_total_cents": order_total_cents,
                "square_order_id": request.square_order_id,
                "checkout_type": "qr",
            },
        )

        # P1-1: Admin audit log
        log_wallet_mutation(
            db=db,
            actor_id=user.id,
            action="wallet_redeem",
            user_id=str(user.id),
            before_balance=before_balance,
            after_balance=result["driver_balance"],
            amount=-discount_cents,
            metadata={
                "qr_token": request.qr_token,
                "merchant_id": merchant.id,
                "order_total_cents": order_total_cents,
                "square_order_id": request.square_order_id,
                "checkout_type": "qr",
                "redemption_id": redemption_id,
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "REDEMPTION_FAILED", "message": str(e)},
        )

    # Create MerchantRedemption record (P0-6: race condition fix with IntegrityError handling)
    # P1-F: Include idempotency_key for non-Square redemptions
    redemption = MerchantRedemption(
        id=redemption_id,
        merchant_id=merchant.id,
        driver_user_id=user.id,
        qr_token=request.qr_token,
        square_order_id=request.square_order_id,
        idempotency_key=request.idempotency_key,  # P1-F: Store idempotency_key
        order_total_cents=order_total_cents,
        discount_cents=discount_cents,
        nova_spent_cents=discount_cents,
    )
    try:
        db.add(redemption)
        db.commit()
        db.refresh(redemption)
    except IntegrityError as e:
        db.rollback()
        # P1-F: Check for duplicate idempotency_key or square_order_id
        if request.idempotency_key:
            existing = (
                db.query(MerchantRedemption)
                .filter(
                    MerchantRedemption.merchant_id == merchant.id,
                    MerchantRedemption.idempotency_key == request.idempotency_key,
                )
                .first()
            )
            if existing:
                # Return cached response (race condition: another request completed first)
                # Get current wallet balance for response
                wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
                remaining_nova_cents = wallet.nova_balance if wallet else 0

                return RedeemResponse(
                    success=True,
                    merchant_id=existing.merchant_id,
                    discount_cents=existing.discount_cents,
                    order_total_cents=existing.order_total_cents,
                    nova_spent_cents=existing.nova_spent_cents,
                    remaining_nova_cents=remaining_nova_cents,
                    message="Idempotent redemption: returning cached result",
                    redemption_id=existing.id,
                    square_order_id=existing.square_order_id,
                    merchant_fee_cents=None,
                )
        elif request.square_order_id:
            # Check if it's a duplicate square_order_id (race condition)
            existing = (
                db.query(MerchantRedemption)
                .filter(
                    MerchantRedemption.merchant_id == merchant.id,
                    MerchantRedemption.square_order_id == request.square_order_id,
                )
                .first()
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "ORDER_ALREADY_REDEEMED",
                        "message": "This Square order has already been redeemed",
                    },
                )
        # Re-raise if different IntegrityError
        raise

    # Record merchant fee
    merchant_fee_cents = None
    if request.square_order_id:
        try:
            merchant_fee_cents = record_merchant_fee(
                db, merchant.id, discount_cents, datetime.utcnow()
            )
        except Exception as e:
            logger.error(f"Failed to record merchant fee: {e}", exc_info=True)
            # Don't fail redemption if fee recording fails

    # Mark wallet activity for pass refresh
    mark_wallet_activity(db, user.id)

    # P3: HubSpot tracking (dry run)
    try:
        from app.events.hubspot_adapter import adapt_redemption_event
        from app.services.hubspot import track_event

        hubspot_payload = adapt_redemption_event(
            {
                "user_id": str(user.id),
                "merchant_id": merchant.id,
                "amount_cents": discount_cents,
                "redemption_id": redemption_id,
                "redeemed_at": datetime.utcnow().isoformat(),
            }
        )
        track_event(db, "redemption", hubspot_payload)
    except Exception as e:
        # Don't fail redemption if HubSpot tracking fails
        logger.warning(f"HubSpot tracking failed: {e}")

    db.commit()

    logger.info(
        f"Redemption: driver {user.id} redeemed {discount_cents} Nova at merchant {merchant.id}, "
        f"order total: ${order_total_cents / 100:.2f}, square_order_id: {request.square_order_id}"
    )

    # Emit nova_redeemed and first_redemption_completed events (non-blocking)
    try:
        from app.events.domain import FirstRedemptionCompletedEvent, NovaRedeemedEvent
        from app.events.outbox import store_outbox_event

        # Check if this is the first redemption for this driver
        previous_redemptions = (
            db.query(MerchantRedemption)
            .filter(
                MerchantRedemption.driver_user_id == user.id,
                MerchantRedemption.id != redemption_id,  # Exclude current redemption
            )
            .count()
        )

        is_first_redemption = previous_redemptions == 0

        # Emit nova_redeemed event
        redeem_event = NovaRedeemedEvent(
            user_id=str(user.id),
            amount_cents=discount_cents,
            merchant_id=merchant.id,
            redemption_id=redemption_id,
            new_balance_cents=result["driver_balance"],
            redeemed_at=datetime.utcnow(),
        )
        store_outbox_event(db, redeem_event)

        # Emit first_redemption_completed if this is the first
        if is_first_redemption:
            first_event = FirstRedemptionCompletedEvent(
                user_id=str(user.id),
                redemption_id=redemption_id,
                merchant_id=merchant.id,
                amount_cents=discount_cents,
                completed_at=datetime.utcnow(),
            )
            store_outbox_event(db, first_event)
    except Exception as e:
        logger.warning(f"Failed to emit nova_redeemed event: {e}")

    return RedeemResponse(
        success=True,
        merchant_id=merchant.id,
        discount_cents=discount_cents,
        order_total_cents=order_total_cents,
        nova_spent_cents=discount_cents,
        remaining_nova_cents=result["driver_balance"],
        message="Nova applied. Show this screen to the merchant so they can add the discount in Square.",
        redemption_id=redemption_id,
        square_order_id=request.square_order_id,
        merchant_fee_cents=merchant_fee_cents,
    )


class RedemptionDetailResponse(BaseModel):
    """Redemption detail response for present screen"""

    redemption_id: str
    merchant_name: str
    discount_cents: int
    order_total_cents: int
    created_at: str  # ISO string


@router.post("/redeem-reward", response_model=RedeemRewardResponse)
async def redeem_reward(
    request: RedeemRewardRequest,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Redeem a predefined merchant reward (e.g., 300 Nova for Free Coffee).

    This endpoint handles atomic redemption of predefined rewards:
    1. Validates driver wallet exists
    2. Validates reward exists & active
    3. Ensures driver has sufficient Nova
    4. Deducts Nova from driver wallet
    5. Creates MerchantRedemption record
    6. Updates wallet activity for pass refresh

    Args:
        request: RedeemRewardRequest with reward_id
        user: Authenticated driver
        db: Database session

    Returns:
        RedeemRewardResponse with redemption details
    """
    # Validate reward exists and is active
    reward = (
        db.query(MerchantReward)
        .filter(MerchantReward.id == request.reward_id, MerchantReward.is_active == True)
        .first()
    )

    if not reward:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "REWARD_NOT_FOUND", "message": "Reward not found or inactive"},
        )

    # Get merchant
    merchant = (
        db.query(DomainMerchant)
        .filter(DomainMerchant.id == reward.merchant_id, DomainMerchant.status == "active")
        .first()
    )

    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "MERCHANT_NOT_FOUND", "message": "Merchant not found or inactive"},
        )

    # Check driver wallet exists
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "WALLET_NOT_FOUND", "message": "Driver wallet not found"},
        )

    # Check driver has sufficient Nova
    if wallet.nova_balance < reward.nova_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "INSUFFICIENT_NOVA",
                "message": f"Insufficient Nova balance. Has {wallet.nova_balance}, needs {reward.nova_amount}",
            },
        )

    # Redeem Nova via NovaService
    try:
        result = NovaService.redeem_from_driver(
            db=db,
            driver_id=user.id,
            merchant_id=merchant.id,
            amount=reward.nova_amount,
            metadata={
                "reward_id": reward.id,
                "reward_title": reward.title,
                "checkout_type": "predefined_reward",
            },
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "REDEMPTION_FAILED", "message": str(e)},
        )

    # Create MerchantRedemption record
    redemption_id = str(uuid.uuid4())
    redemption = MerchantRedemption(
        id=redemption_id,
        merchant_id=merchant.id,
        driver_user_id=user.id,
        qr_token=None,  # No QR token for predefined rewards
        reward_id=reward.id,  # Track which reward was redeemed
        order_total_cents=0,  # No order total for predefined rewards
        discount_cents=reward.nova_amount,  # Discount equals reward amount
        nova_spent_cents=reward.nova_amount,
    )
    db.add(redemption)
    db.commit()
    db.refresh(redemption)

    # Mark wallet activity for pass refresh
    mark_wallet_activity(db, user.id)
    db.commit()

    logger.info(
        f"Reward redemption: driver {user.id} redeemed {reward.nova_amount} Nova "
        f"for reward '{reward.title}' at merchant {merchant.id}"
    )

    return RedeemRewardResponse(
        status="SUCCESS",
        nova_redeemed=reward.nova_amount,
        reward=reward.title,
        redemption_id=redemption_id,
        remaining_nova_cents=result["driver_balance"],
    )


@router.get("/redemption/{redemption_id}", response_model=RedemptionDetailResponse)
async def get_redemption_detail(
    redemption_id: str, user: User = Depends(get_current_driver), db: Session = Depends(get_db)
):
    """
    Get redemption detail for present screen.

    Ensures driver owns the redemption (driver_user_id matches).
    """
    redemption = db.query(MerchantRedemption).filter(MerchantRedemption.id == redemption_id).first()

    if not redemption:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "REDEMPTION_NOT_FOUND", "message": "Redemption not found"},
        )

    # Ensure driver owns this redemption
    if redemption.driver_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "REDEMPTION_ACCESS_DENIED",
                "message": "You do not have access to this redemption",
            },
        )

    # Get merchant name
    merchant_name = "Merchant"
    if redemption.merchant:
        merchant_name = redemption.merchant.name
    elif redemption.merchant_id:
        merchant_name = f"Merchant {redemption.merchant_id[:8]}"

    return RedemptionDetailResponse(
        redemption_id=redemption.id,
        merchant_name=merchant_name,
        discount_cents=redemption.discount_cents,
        order_total_cents=redemption.order_total_cents,
        created_at=redemption.created_at.isoformat(),
    )
