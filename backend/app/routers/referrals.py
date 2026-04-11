"""Referral system router."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.services.referral_service import (
    get_or_create_code,
    get_referral_stats,
    redeem_referral,
)

router = APIRouter(prefix="/v1/referrals", tags=["referrals"])


class ReferralCodeResponse(BaseModel):
    code: str
    referral_link: str


class ReferralStatsResponse(BaseModel):
    total_referrals: int
    total_earned_cents: int
    pending_count: int


class RedeemRequest(BaseModel):
    code: str


class RedeemResponse(BaseModel):
    ok: bool
    message: str


@router.get("/code", response_model=ReferralCodeResponse)
def get_referral_code(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get or create user's unique referral code."""
    ref_code = get_or_create_code(db, current_user.id)
    return ReferralCodeResponse(
        code=ref_code.code,
        referral_link=f"https://app.nerava.network/join?ref={ref_code.code}",
    )


@router.get("/stats", response_model=ReferralStatsResponse)
def get_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get referral stats for current user."""
    stats = get_referral_stats(db, current_user.id)
    return ReferralStatsResponse(**stats)


@router.post("/redeem", response_model=RedeemResponse)
def redeem_code(
    body: RedeemRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Redeem a referral code."""
    if not body.code.strip():
        raise HTTPException(status_code=400, detail="Referral code is required")

    result = redeem_referral(db, body.code.strip().upper(), current_user.id)
    if result is None:
        raise HTTPException(status_code=400, detail="Invalid or expired referral code")

    return RedeemResponse(
        ok=True,
        message="Referral code applied! You'll both earn $5 after your first charging session.",
    )
