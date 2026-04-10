from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.merchant_analytics import merchant_summary

router = APIRouter(prefix="/v1/merchant", tags=["merchant"])

@router.get("/insights")
async def get_merchant_insights(
    merchant_id: Optional[int] = Query(None, description="Merchant ID (optional)"),
    period: str = Query("month", description="Time period: month, week, or day"),
    db: Session = Depends(get_db)
):
    """Get merchant analytics insights (legacy endpoint - uses merchant_summary)."""
    if not merchant_id:
        raise HTTPException(status_code=400, detail="merchant_id required")
    
    try:
        summary = merchant_summary(db, merchant_id)
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get insights: {str(e)}")

@router.get("/insights/top")
async def get_top_merchants_endpoint(
    limit: int = Query(10, description="Number of top merchants to return"),
    period: str = Query("month", description="Time period: month, week, or day"),
    db: Session = Depends(get_db)
):
    """Get top performing merchants (stub - returns empty list)."""
    return {
        'period': period,
        'limit': limit,
        'merchants': []
    }

@router.get("/dashboard")
async def get_merchant_dashboard(
    merchant_id: int = Query(..., description="Merchant ID"),
    db: Session = Depends(get_db)
):
    """Get comprehensive dashboard data for a merchant (uses merchant_summary)."""
    try:
        summary = merchant_summary(db, merchant_id)
        return {
            'merchant_id': merchant_id,
            'summary': summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard: {str(e)}")
