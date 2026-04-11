"""
Service for fetching merchant activation counts (today)
"""
from datetime import datetime
from typing import Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus


def get_merchant_activation_counts(db: Session, merchant_id: str) -> Dict[str, int]:
    """
    Get today's activation counts for a merchant.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        
    Returns:
        Dict with 'activations_today' and 'verified_visits_today' counts
    """
    # Get start of today (server timezone - UTC)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Count activations today (sessions created today)
    activations_today = db.query(func.count(ExclusiveSession.id)).filter(
        ExclusiveSession.merchant_id == merchant_id,
        ExclusiveSession.created_at >= today_start
    ).scalar() or 0
    
    # Count verified visits today (sessions completed today)
    verified_visits_today = db.query(func.count(ExclusiveSession.id)).filter(
        ExclusiveSession.merchant_id == merchant_id,
        ExclusiveSession.status == ExclusiveSessionStatus.COMPLETED,
        ExclusiveSession.completed_at >= today_start
    ).scalar() or 0
    
    return {
        "activations_today": int(activations_today),
        "verified_visits_today": int(verified_visits_today)
    }






