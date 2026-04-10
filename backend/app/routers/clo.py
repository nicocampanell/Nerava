"""
Card Linked Offers (CLO) Router - Fidel Integration

Endpoints for card linking, transaction verification, and reward management.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.domain import get_current_user
from ..services.spend_verification_service import SpendVerificationService

router = APIRouter(prefix="/v1/clo", tags=["clo"])


class LinkCardRequest(BaseModel):
    card_number: str  # In production, this would be tokenized via Fidel SDK
    expiry_month: int
    expiry_year: int
    cvv: str
    country_code: str = "USA"


class VerifyTransactionRequest(BaseModel):
    card_id: str
    amount_cents: int
    merchant_id: str
    merchant_name: Optional[str] = None
    merchant_location: Optional[str] = None
    transaction_time: Optional[str] = None
    charging_session_id: Optional[str] = None


class CreateOfferRequest(BaseModel):
    merchant_id: str
    min_spend_cents: int = 0
    reward_cents: int
    reward_percent: Optional[int] = None
    max_reward_cents: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None


@router.get("/cards")
async def get_linked_cards(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get driver's linked cards"""
    try:
        cards = SpendVerificationService.get_linked_cards(db, current_user.id)
        return {"cards": cards}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cards/link")
async def link_card(
    request: LinkCardRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Link a card for CLO tracking"""
    try:
        result = SpendVerificationService.link_card(
            db,
            current_user.id,
            request.card_number,
            request.expiry_month,
            request.expiry_year,
            request.cvv,
            request.country_code,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cards/{card_id}")
async def unlink_card(
    card_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Unlink a card"""
    try:
        result = SpendVerificationService.unlink_card(db, current_user.id, card_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cards/session")
async def get_enrollment_session(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get secure card enrollment session (for Fidel Select SDK)"""
    try:
        session = SpendVerificationService.create_card_enrollment_session(db, current_user.id)
        return session
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions")
async def get_transaction_history(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Get CLO transaction history"""
    try:
        transactions = SpendVerificationService.get_transaction_history(db, current_user.id, limit)
        return {"transactions": transactions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify")
async def verify_transaction(
    request: VerifyTransactionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Manually verify a transaction (for testing/mock mode)"""
    try:
        result = SpendVerificationService.verify_transaction(
            db,
            current_user.id,
            {
                "card_id": request.card_id,
                "amount_cents": request.amount_cents,
                "merchant_id": request.merchant_id,
                "merchant_name": request.merchant_name,
                "merchant_location": request.merchant_location,
                "transaction_time": request.transaction_time,
                "charging_session_id": request.charging_session_id,
            },
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fidel/webhook")
async def handle_fidel_webhook(
    request: Request,
    fidel_signature: Optional[str] = Header(None, alias="X-Fidel-Signature"),
    db: Session = Depends(get_db),
):
    """Handle Fidel webhook events"""
    try:
        payload = await request.json()
        signature = fidel_signature or ""
        result = SpendVerificationService.process_webhook(db, payload, signature)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Admin endpoints
@router.post("/admin/offers")
async def create_offer(
    request: CreateOfferRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a CLO offer for a merchant (admin only)"""
    if not current_user.admin_role:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        result = SpendVerificationService.create_merchant_offer(
            db,
            request.merchant_id,
            request.min_spend_cents,
            request.reward_cents,
            request.reward_percent,
            request.max_reward_cents,
            request.valid_from,
            request.valid_until,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
