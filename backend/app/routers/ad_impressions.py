"""
Ad Impressions Router

Records driver-side ad impressions and provides merchant-facing stats.
"""
import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.domain import require_merchant_admin
from app.dependencies_domain import get_current_user
from app.models import User
from app.models.ad_impression import AdImpression
from app.services.ad_billing_service import get_impression_stats
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ads", tags=["ads"])


class ImpressionItem(BaseModel):
    merchant_id: str
    impression_type: str  # "carousel" | "featured" | "search"


class BatchImpressionRequest(BaseModel):
    impressions: List[ImpressionItem]


@router.post("/impressions", summary="Record ad impressions (batch)")
async def record_impressions(
    request: BatchImpressionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Batch record impressions from the driver app.
    Auth: driver JWT.
    """
    if not request.impressions:
        return {"recorded": 0}

    # Cap at 50 per batch to prevent abuse
    items = request.impressions[:50]

    for item in items:
        impression = AdImpression(
            id=str(uuid.uuid4()),
            merchant_id=item.merchant_id,
            driver_user_id=current_user.id,
            impression_type=item.impression_type,
            created_at=datetime.utcnow(),
        )
        db.add(impression)

    db.commit()
    return {"recorded": len(items)}


@router.get("/impressions/stats", summary="Get merchant impression stats")
async def get_ad_stats(
    period: str = Query("30d", description="Period: 'week' or '30d'"),
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Get impression statistics for the authenticated merchant.
    Auth: merchant JWT.
    """
    merchant = AuthService.get_user_merchant(db, user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found for user")

    stats = get_impression_stats(db, merchant.id, period)
    return stats
