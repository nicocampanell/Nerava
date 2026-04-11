"""
Loyalty Router — punch card programs for merchants and progress for drivers.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.services import loyalty_service

router = APIRouter(prefix="/v1/loyalty", tags=["loyalty"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateCardRequest(BaseModel):
    program_name: str
    visits_required: int
    reward_cents: int = 0
    reward_description: Optional[str] = None
    place_id: Optional[str] = None


class UpdateCardRequest(BaseModel):
    program_name: Optional[str] = None
    visits_required: Optional[int] = None
    reward_cents: Optional[int] = None
    reward_description: Optional[str] = None
    is_active: Optional[bool] = None


class CardResponse(BaseModel):
    id: str
    merchant_id: str
    place_id: Optional[str] = None
    program_name: str
    visits_required: int
    reward_cents: int
    reward_description: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Merchant endpoints (merchant auth)
# ---------------------------------------------------------------------------

def _get_merchant_id() -> str:
    """Helper: resolve merchant_id from token. For now, read from localStorage-backed header."""
    # This will be replaced with proper merchant auth dependency
    pass


@router.post("/cards", response_model=CardResponse)
def create_card(
    body: CreateCardRequest,
    merchant_id: str = Query(..., description="Merchant ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = loyalty_service.create_loyalty_card(
        db,
        merchant_id=merchant_id,
        program_name=body.program_name,
        visits_required=body.visits_required,
        reward_cents=body.reward_cents,
        reward_description=body.reward_description,
        place_id=body.place_id,
    )
    return _card_to_response(card)


@router.get("/cards", response_model=List[CardResponse])
def list_cards(
    merchant_id: str = Query(..., description="Merchant ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cards = loyalty_service.get_loyalty_cards(db, merchant_id)
    return [_card_to_response(c) for c in cards]


@router.patch("/cards/{card_id}", response_model=CardResponse)
def update_card(
    card_id: str,
    body: UpdateCardRequest,
    merchant_id: str = Query(..., description="Merchant ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    card = loyalty_service.update_loyalty_card(
        db, card_id, merchant_id, **body.dict(exclude_unset=True)
    )
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return _card_to_response(card)


@router.get("/customers")
def get_customers(
    merchant_id: str = Query(..., description="Merchant ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return loyalty_service.get_merchant_loyalty_customers(db, merchant_id, limit, offset)


@router.get("/stats")
def get_stats(
    merchant_id: str = Query(..., description="Merchant ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return loyalty_service.get_merchant_loyalty_stats(db, merchant_id)


# ---------------------------------------------------------------------------
# Driver endpoints (driver auth)
# ---------------------------------------------------------------------------

@router.get("/progress")
def get_progress(
    merchant_id: str = Query(..., description="Merchant ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return loyalty_service.get_driver_progress(db, user.id, merchant_id)


@router.post("/rewards/{card_id}/claim")
def claim_reward(
    card_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    progress = loyalty_service.claim_reward(db, user.id, card_id)
    if not progress:
        raise HTTPException(status_code=400, detail="Reward not available for claim")
    return {
        "ok": True,
        "card_id": card_id,
        "reward_claimed": True,
        "reward_claimed_at": progress.reward_claimed_at.isoformat() if progress.reward_claimed_at else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _card_to_response(card) -> dict:
    return {
        "id": card.id,
        "merchant_id": card.merchant_id,
        "place_id": card.place_id,
        "program_name": card.program_name,
        "visits_required": card.visits_required,
        "reward_cents": card.reward_cents,
        "reward_description": card.reward_description,
        "is_active": card.is_active,
        "created_at": card.created_at.isoformat() if card.created_at else None,
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
    }
