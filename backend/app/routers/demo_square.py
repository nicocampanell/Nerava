"""
Demo Square Endpoints

Swagger-driven demo endpoints for creating Square sandbox orders and payments.
Only enabled when DEMO_MODE=true and requires X-Demo-Admin-Key header.
"""
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models.domain import DomainMerchant
from ..services.square_orders import (
    SquareError,
    SquareNotConnectedError,
    _get_square_base_url,
    create_order,
    create_payment_for_order,
    get_square_token_for_merchant,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/demo/square", tags=["demo-square"])


def verify_demo_admin_key(x_demo_admin_key: Optional[str] = Header(None)) -> None:
    """
    Verify demo admin key header.
    
    Raises:
        HTTPException: If DEMO_MODE is not enabled or key is missing/wrong
    """
    if not settings.DEMO_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo mode is not enabled"
        )
    
    if not settings.DEMO_ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DEMO_ADMIN_KEY not configured"
        )
    
    if not x_demo_admin_key or x_demo_admin_key != settings.DEMO_ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Demo-Admin-Key header"
        )


class CreateOrderRequest(BaseModel):
    """Request to create a Square order"""
    merchant_id: str
    amount_cents: int
    name: str = "Coffee"


class CreateOrderResponse(BaseModel):
    """Response from order creation"""
    order_id: str
    total_cents: int
    created_at: str


class CreatePaymentRequest(BaseModel):
    """Request to create a Square payment"""
    merchant_id: str
    order_id: str
    amount_cents: int


class CreatePaymentResponse(BaseModel):
    """Response from payment creation"""
    payment_id: str
    status: str


class VerifyResponse(BaseModel):
    """Response from Square token verification"""
    ok: bool
    location_id: Optional[str] = None
    merchant_name: Optional[str] = None
    error: Optional[str] = None
    message: Optional[str] = None


@router.post("/orders/create", response_model=CreateOrderResponse)
async def create_square_order(
    request: CreateOrderRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_demo_admin_key)
):
    """
    Create a Square order (for Swagger demo).
    
    Requires:
    - DEMO_MODE=true
    - X-Demo-Admin-Key header with valid key
    
    Args:
        request: CreateOrderRequest with merchant_id, amount_cents, and optional name
        db: Database session
        
    Returns:
        CreateOrderResponse with order_id, total_cents, and created_at
    """
    # Get merchant
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.id == request.merchant_id
    ).first()
    
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "MERCHANT_NOT_FOUND",
                "message": f"Merchant {request.merchant_id} not found"
            }
        )
    
    try:
        result = create_order(
            db,
            merchant,
            request.amount_cents,
            request.name
        )
        
        return CreateOrderResponse(
            order_id=result["order_id"],
            total_cents=result["total_cents"],
            created_at=result["created_at"]
        )
    except SquareNotConnectedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "SQUARE_NOT_CONNECTED",
                "message": "Merchant is not connected to Square"
            }
        )
    except SquareError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "SQUARE_API_ERROR",
                "message": str(e)
            }
        )


@router.post("/payments/create", response_model=CreatePaymentResponse)
async def create_square_payment(
    request: CreatePaymentRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_demo_admin_key)
):
    """
    Create a Square payment for an order (for Swagger demo).
    
    Requires:
    - DEMO_MODE=true
    - X-Demo-Admin-Key header with valid key
    
    Args:
        request: CreatePaymentRequest with merchant_id, order_id, and amount_cents
        db: Database session
        
    Returns:
        CreatePaymentResponse with payment_id and status
    """
    # Get merchant
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.id == request.merchant_id
    ).first()
    
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "MERCHANT_NOT_FOUND",
                "message": f"Merchant {request.merchant_id} not found"
            }
        )
    
    try:
        result = create_payment_for_order(
            db,
            merchant,
            request.order_id,
            request.amount_cents
        )
        
        return CreatePaymentResponse(
            payment_id=result["payment_id"],
            status=result["status"]
        )
    except SquareNotConnectedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "SQUARE_NOT_CONNECTED",
                "message": "Merchant is not connected to Square"
            }
        )
    except SquareError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "SQUARE_API_ERROR",
                "message": str(e)
            }
        )


@router.get("/verify", response_model=VerifyResponse)
async def verify_square_token(
    merchant_id: str = Query(..., description="Merchant ID to verify"),
    db: Session = Depends(get_db),
    _: None = Depends(verify_demo_admin_key)
):
    """
    Verify Square token decryption and API access (demo-only).
    
    This endpoint:
    1. Decrypts the merchant's Square access token
    2. Calls Square API GET /v2/locations to verify token works
    3. Returns structured response with verification status
    
    Requires:
    - DEMO_MODE=true
    - X-Demo-Admin-Key header with valid key
    
    Args:
        merchant_id: Merchant ID to verify
        db: Database session
        
    Returns:
        VerifyResponse with ok status, location_id, merchant_name, or error details
    """
    # Get merchant
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.id == merchant_id
    ).first()
    
    if not merchant:
        return VerifyResponse(
            ok=False,
            error="MERCHANT_NOT_FOUND",
            message=f"Merchant {merchant_id} not found"
        )
    
    # Check if merchant has Square token
    if not merchant.square_access_token:
        return VerifyResponse(
            ok=False,
            error="SQUARE_NOT_CONNECTED",
            message="Merchant is not connected to Square"
        )
    
    try:
        # Decrypt token
        access_token = get_square_token_for_merchant(merchant)
        
        # Get Square base URL
        base_url = _get_square_base_url()
        
        # Call Square API to verify token
        locations_url = f"{base_url}/v2/locations"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                locations_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Square-Version": "2024-01-18",
                }
            )
            
            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"Square Locations API failed: {response.status_code} - {error_detail}")
                return VerifyResponse(
                    ok=False,
                    error="SQUARE_API_ERROR",
                    message=f"Square API returned {response.status_code}: {error_detail[:200]}"
                )
            
            data = response.json()
            locations = data.get("locations", [])
            
            if not locations:
                return VerifyResponse(
                    ok=False,
                    error="NO_LOCATIONS",
                    message="No Square locations found for merchant"
                )
            
            # Get first location and merchant name
            location = locations[0]
            location_id = location.get("id")
            merchant_name = merchant.name
            
            logger.info(f"Successfully verified Square token for merchant {merchant_id}, location: {location_id}")
            
            return VerifyResponse(
                ok=True,
                location_id=location_id,
                merchant_name=merchant_name
            )
            
    except SquareNotConnectedError as e:
        return VerifyResponse(
            ok=False,
            error="SQUARE_NOT_CONNECTED",
            message=str(e)
        )
    except Exception as e:
        logger.error(f"Error verifying Square token for merchant {merchant_id}: {e}", exc_info=True)
        return VerifyResponse(
            ok=False,
            error="VERIFICATION_ERROR",
            message=f"Failed to verify token: {str(e)}"
        )


# ============== Demo Orders (works without Square) ==============

class DemoOrder(BaseModel):
    order_id: str
    created_at: str
    total_cents: int
    currency: str = "USD"
    display: str


class DemoOrdersResponse(BaseModel):
    merchant_id: str
    merchant_name: str
    orders: list[DemoOrder]


@router.get("/demo-orders", response_model=DemoOrdersResponse)
async def get_demo_orders(
    token: str = Query(..., description="Merchant QR token"),
    db: Session = Depends(get_db),
    _: None = Depends(verify_demo_admin_key)
):
    """
    Get mock demo orders for testing checkout flow without Square.
    
    This returns hardcoded demo orders that can be used for testing
    the redemption flow when Square is not connected.
    """
    # Find merchant by QR token
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.qr_token == token
    ).first()
    
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "MERCHANT_NOT_FOUND", "message": "Merchant not found for this QR token"}
        )
    
    from datetime import datetime, timedelta
    
    # Generate single demo order with consistent ID for the session
    now = datetime.utcnow()
    demo_orders = [
        DemoOrder(
            order_id="demo-order-001",
            created_at=(now - timedelta(minutes=2)).isoformat() + "Z",
            total_cents=1250,
            display="$12.50 - Coffee & Pastry"
        ),
    ]
    
    return DemoOrdersResponse(
        merchant_id=merchant.id,
        merchant_name=merchant.name,
        orders=demo_orders
    )


# ============== Set Sandbox Token (for testing) ==============

class SetTokenRequest(BaseModel):
    merchant_id: str
    access_token: str
    location_id: str


class SetTokenResponse(BaseModel):
    ok: bool
    message: str


@router.post("/set-sandbox-token", response_model=SetTokenResponse)
async def set_sandbox_token(
    request: SetTokenRequest,
    db: Session = Depends(get_db),
    _: None = Depends(verify_demo_admin_key)
):
    """
    Set Square sandbox access token directly for a merchant.
    
    This bypasses OAuth flow for demo/testing purposes.
    Only works when DEMO_MODE=true.
    
    Get your sandbox access token from:
    https://developer.squareup.com/apps -> Your App -> Credentials -> Sandbox Access Token
    """
    from ..services.token_encryption import encrypt_token
    
    merchant = db.query(DomainMerchant).filter(
        DomainMerchant.id == request.merchant_id
    ).first()
    
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "MERCHANT_NOT_FOUND", "message": f"Merchant {request.merchant_id} not found"}
        )
    
    # Encrypt and save the token
    merchant.square_access_token = encrypt_token(request.access_token)
    merchant.square_location_id = request.location_id
    db.commit()
    
    logger.info(f"Set sandbox token for merchant {merchant.id}, location: {request.location_id}")
    
    return SetTokenResponse(
        ok=True,
        message=f"Sandbox token set for merchant {merchant.name}"
    )

