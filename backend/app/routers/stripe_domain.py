"""
Domain Charge Party MVP Stripe Router
Stripe Checkout and webhook handling for Nova purchases
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_domain import require_merchant_admin
from app.services.auth_service import AuthService
from app.services.stripe_service import NOVA_PACKAGES, StripeService

router = APIRouter(prefix="/v1/stripe", tags=["stripe-v1"])


class CreateCheckoutRequest(BaseModel):
    package_id: str


class CreateCheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


@router.post("/create_checkout_session", response_model=CreateCheckoutResponse)
def create_checkout_session(
    request: CreateCheckoutRequest,
    user = Depends(require_merchant_admin),
    db: Session = Depends(get_db)
):
    """Create Stripe Checkout session for Nova purchase"""
    merchant = AuthService.get_user_merchant(db, user.id)
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Merchant not found"
        )
    
    try:
        result = StripeService.create_checkout_session(
            db=db,
            merchant_id=merchant.id,
            package_id=request.package_id
        )
        return CreateCheckoutResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    stripe_signature: str = Header(None, alias="stripe-signature")
):
    """Handle Stripe webhook events"""
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing stripe-signature header"
        )
    
    payload = await request.body()
    
    try:
        result = await StripeService.handle_webhook_async(
            db=db,
            payload=payload,
            signature=stripe_signature,
            webhook_secret=settings.STRIPE_WEBHOOK_SECRET
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/packages")
def list_packages():
    """List available Nova packages"""
    return {
        "packages": [
            {
                "id": package_id,
                "usd_cents": pkg["usd_cents"],
                "nova_amount": pkg["nova_amount"],
                "usd_price": f"${pkg['usd_cents'] / 100:.2f}"
            }
            for package_id, pkg in NOVA_PACKAGES.items()
        ]
    }

