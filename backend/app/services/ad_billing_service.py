"""
Ad Billing Service

Tracks and calculates CPM-based billing for Nerava Ads impressions.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ad_impression import AdImpression

logger = logging.getLogger(__name__)

# $5.00 per 1,000 impressions
CPM_RATE_CENTS = 500


def get_impression_count(
    db: Session,
    merchant_id: str,
    start: datetime,
    end: datetime,
) -> int:
    """Get total impression count for a merchant in a date range."""
    return (
        db.query(func.count(AdImpression.id))
        .filter(
            AdImpression.merchant_id == merchant_id,
            AdImpression.created_at >= start,
            AdImpression.created_at <= end,
        )
        .scalar() or 0
    )


def get_impression_stats(
    db: Session,
    merchant_id: str,
    period: str = "30d",
) -> Dict:
    """
    Get impression statistics for a merchant.

    Returns totals, by_day breakdown, and by_type breakdown.
    """
    now = datetime.utcnow()
    if period == "week":
        start = now - timedelta(days=7)
    elif period == "30d":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=30)

    total = get_impression_count(db, merchant_id, start, now)

    # By type
    type_rows = (
        db.query(
            AdImpression.impression_type,
            func.count(AdImpression.id).label("count"),
        )
        .filter(
            AdImpression.merchant_id == merchant_id,
            AdImpression.created_at >= start,
            AdImpression.created_at <= now,
        )
        .group_by(AdImpression.impression_type)
        .all()
    )
    by_type = {row.impression_type: row.count for row in type_rows}

    # By day
    dialect = db.bind.dialect.name if db.bind else "sqlite"
    if dialect == "postgresql":
        day_expr = func.date(AdImpression.created_at)
    else:
        day_expr = func.date(AdImpression.created_at)

    day_rows = (
        db.query(
            day_expr.label("day"),
            func.count(AdImpression.id).label("count"),
        )
        .filter(
            AdImpression.merchant_id == merchant_id,
            AdImpression.created_at >= start,
            AdImpression.created_at <= now,
        )
        .group_by("day")
        .order_by("day")
        .all()
    )
    by_day = [{"date": str(row.day), "count": row.count} for row in day_rows]

    estimated_cpm_charge_cents = calculate_cpm_charge(total)

    return {
        "period": period,
        "total": total,
        "by_type": by_type,
        "by_day": by_day,
        "estimated_cpm_charge_cents": estimated_cpm_charge_cents,
    }


def calculate_cpm_charge(count: int) -> int:
    """Calculate CPM charge in cents. $5.00 per 1,000 impressions."""
    return int((count / 1000) * CPM_RATE_CENTS)
