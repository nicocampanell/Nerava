from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.payouts import settle_unpaid_follower_shares

router = APIRouter(prefix="/v1/admin", tags=["admin"])

@router.post("/settle")
async def settle(limit: int = 500, db: Session = Depends(get_db)):
    """Settle unpaid follower shares by crediting wallets."""
    n = settle_unpaid_follower_shares(db, limit=limit)
    return {"settled": n}
