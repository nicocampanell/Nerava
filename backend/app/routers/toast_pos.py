"""
Toast POS Integration Router

Provides endpoints for merchants to connect their Toast POS account,
view connection status, retrieve average order value, and disconnect.

All endpoints require merchant_admin role authentication.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies.domain import require_merchant_admin
from app.models import User
from app.services import toast_pos_service
from app.services.merchant_onboarding_service import create_or_get_merchant_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchant/pos", tags=["merchant_pos"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ConnectResponse(BaseModel):
    auth_url: str
    state: str


class CallbackRequest(BaseModel):
    code: str
    state: str


class CallbackResponse(BaseModel):
    connected: bool
    restaurant_name: str = ""
    restaurant_guid: str = ""


class StatusResponse(BaseModel):
    connected: bool
    provider: str = ""
    restaurant_name: Optional[str] = None
    restaurant_guid: Optional[str] = None
    aov_cents: Optional[int] = None
    order_count: Optional[int] = None


class AOVResponse(BaseModel):
    aov_cents: int
    order_count: int
    period_days: int
    source: str


class DisconnectResponse(BaseModel):
    disconnected: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/toast/connect", response_model=ConnectResponse, summary="Get Toast OAuth URL")
async def toast_connect(
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Returns an OAuth authorization URL for connecting a Toast POS account.
    The frontend should redirect the merchant to `auth_url`.
    """
    merchant_account = create_or_get_merchant_account(db, user.id)
    redirect_uri = f"{settings.MERCHANT_PORTAL_URL}/toast/callback"

    result = toast_pos_service.get_auth_url(
        db=db,
        merchant_account_id=str(merchant_account.id),
        redirect_uri=redirect_uri,
    )

    return ConnectResponse(auth_url=result["auth_url"], state=result["state"])


@router.post("/toast/callback", response_model=CallbackResponse, summary="Toast OAuth callback")
async def toast_callback(
    body: CallbackRequest,
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Exchange the OAuth authorization code for tokens and store encrypted credentials.
    Called by the frontend after the merchant completes Toast OAuth.
    """
    redirect_uri = f"{settings.MERCHANT_PORTAL_URL}/toast/callback"

    # Pass merchant_account_id for mock mode (state may not survive multi-instance)
    merchant_account = create_or_get_merchant_account(db, user.id)

    try:
        result = await toast_pos_service.exchange_code(
            db=db,
            code=body.code,
            state=body.state,
            redirect_uri=redirect_uri,
            merchant_account_id_override=str(merchant_account.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Toast callback error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to connect Toast account",
        )

    return CallbackResponse(
        connected=result.get("connected", False),
        restaurant_name=result.get("restaurant_name", ""),
        restaurant_guid=result.get("restaurant_guid", ""),
    )


@router.get("/status", response_model=StatusResponse, summary="POS connection status")
async def pos_status(
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Returns the current POS connection status, restaurant info, and AOV if available.
    """
    merchant_account = create_or_get_merchant_account(db, user.id)
    account_id = str(merchant_account.id)

    # Check for Toast connection
    info = await toast_pos_service.get_restaurant_info(db, account_id)
    if not info:
        return StatusResponse(connected=False)

    # Try to get AOV (non-blocking — if it fails, just omit)
    aov_data = None
    try:
        aov_data = await toast_pos_service.calculate_aov(db, account_id)
    except Exception as e:
        logger.warning(f"AOV calculation failed for merchant {account_id}: {e}")

    return StatusResponse(
        connected=True,
        provider="toast",
        restaurant_name=info.get("name"),
        restaurant_guid=info.get("restaurant_guid"),
        aov_cents=aov_data.get("aov_cents") if aov_data else None,
        order_count=aov_data.get("order_count") if aov_data else None,
    )


@router.post("/toast/disconnect", response_model=DisconnectResponse, summary="Disconnect Toast")
async def toast_disconnect(
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Remove stored Toast credentials for the merchant.
    """
    merchant_account = create_or_get_merchant_account(db, user.id)
    removed = toast_pos_service.disconnect(db, str(merchant_account.id))
    return DisconnectResponse(disconnected=removed)


@router.get("/toast/aov", response_model=AOVResponse, summary="Get average order value")
async def toast_aov(
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Fetch and calculate average order value from Toast order data.
    Returns AOV in cents, order count, and period.
    """
    merchant_account = create_or_get_merchant_account(db, user.id)
    account_id = str(merchant_account.id)

    try:
        result = await toast_pos_service.calculate_aov(db, account_id)
    except Exception as e:
        logger.error(f"Toast AOV error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to calculate AOV",
        )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Toast order data available. Ensure Toast POS is connected.",
        )

    return AOVResponse(**result)
