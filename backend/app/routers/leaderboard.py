"""Earnings leaderboard router."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.models.driver_wallet import DriverWallet

router = APIRouter(prefix="/v1/leaderboard", tags=["leaderboard"])


class LeaderboardEntry(BaseModel):
    rank: int
    display_name: str
    total_earned_cents: int
    is_current_user: bool


class LeaderboardResponse(BaseModel):
    entries: List[LeaderboardEntry]
    current_user_rank: Optional[int] = None
    current_user_earned_cents: Optional[int] = None


@router.get("", response_model=LeaderboardResponse)
def get_leaderboard(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get earnings leaderboard (top earners by total_earned_cents)."""
    # Top N wallets by earnings
    top_wallets = (
        db.query(DriverWallet, User)
        .join(User, DriverWallet.driver_id == User.id)
        .filter(DriverWallet.total_earned_cents > 0)
        .order_by(desc(DriverWallet.total_earned_cents))
        .limit(limit)
        .all()
    )

    entries = []
    current_user_rank = None

    for i, (wallet, user) in enumerate(top_wallets, 1):
        # Anonymize: show first name + last initial, or "Driver" if no name
        name = _anonymize_name(user.display_name)
        is_me = user.id == current_user.id
        if is_me:
            current_user_rank = i

        entries.append(LeaderboardEntry(
            rank=i,
            display_name=name if not is_me else "You",
            total_earned_cents=wallet.total_earned_cents,
            is_current_user=is_me,
        ))

    # If current user not in top N, find their rank
    current_user_earned = 0
    if current_user_rank is None:
        my_wallet = db.query(DriverWallet).filter(
            DriverWallet.driver_id == current_user.id
        ).first()
        if my_wallet and my_wallet.total_earned_cents > 0:
            current_user_earned = my_wallet.total_earned_cents
            rank = db.query(DriverWallet).filter(
                DriverWallet.total_earned_cents > my_wallet.total_earned_cents
            ).count() + 1
            current_user_rank = rank
        else:
            current_user_earned = 0
    else:
        # Already in the list
        current_user_earned = next(
            (e.total_earned_cents for e in entries if e.is_current_user), 0
        )

    return LeaderboardResponse(
        entries=entries,
        current_user_rank=current_user_rank,
        current_user_earned_cents=current_user_earned,
    )


def _anonymize_name(display_name: Optional[str]) -> str:
    """Convert 'John Smith' to 'John S.' for privacy."""
    if not display_name or not display_name.strip():
        return "Driver"
    parts = display_name.strip().split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."
