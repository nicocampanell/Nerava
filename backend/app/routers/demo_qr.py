"""
Demo QR Router

Sandbox-only redirect for printed demo QR codes.

Does NOT affect real QR token logic or production flows.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.domain import DomainMerchant

router = APIRouter(tags=["demo-qr"])


def _is_demo_enabled() -> bool:
    """Check if any demo mode is enabled."""
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
    demo_qr = os.getenv("DEMO_QR_ENABLED", "false").lower() == "true"
    return demo_mode or demo_qr


@router.get("/qr/eggman-demo-checkout")
async def eggman_demo_qr_redirect(db: Session = Depends(get_db)):
    """
    Sandbox-only redirect for the printed Eggman demo QR.

    Behavior:
    - If DEMO_MODE != "true" AND DEMO_QR_ENABLED != "true" -> 404
    - Looks up first Square-connected merchant's QR token from DB
    - Else 302 redirect to /app/checkout.html?token=<token>
    """
    if not _is_demo_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "DEMO_QR_DISABLED",
                "message": "Demo QR redirect is disabled in this environment.",
            },
        )

    # First try env var
    demo_token = os.getenv("DEMO_EGGMAN_QR_TOKEN", "").strip()
    
    # If not set, look up from database (first Square-connected merchant)
    if not demo_token:
        merchant = db.query(DomainMerchant).filter(
            DomainMerchant.square_location_id.isnot(None),
            DomainMerchant.qr_token.isnot(None)
        ).first()
        if merchant and merchant.qr_token:
            demo_token = merchant.qr_token
    
    if not demo_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "DEMO_QR_TOKEN_MISSING",
                "message": "Demo QR token is not configured and no Square merchant found.",
            },
        )

    # Preserve opaque token – this is just a redirect helper
    location = f"/app/checkout.html?token={demo_token}"
    return RedirectResponse(url=location, status_code=status.HTTP_302_FOUND)


