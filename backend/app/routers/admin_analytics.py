"""
Admin Analytics Router — Time-series endpoints for the admin dashboard.

Provides daily aggregations for sessions, drivers, revenue, and campaigns.
All endpoints require admin auth and accept a `days` query parameter.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import require_admin
from app.models import User
from app.models.campaign import Campaign
from app.models.driver_wallet import Payout
from app.models.session_event import IncentiveGrant, SessionEvent

router = APIRouter(prefix="/v1/admin/analytics", tags=["admin-analytics"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class DailySessionCount(BaseModel):
    date: str
    count: int
    total_kwh: float


class DailyDriverCount(BaseModel):
    date: str
    active_drivers: int
    new_drivers: int


class DailyRevenue(BaseModel):
    date: str
    grants_cents: int
    payouts_cents: int


class CampaignSummary(BaseModel):
    id: str
    name: str
    status: str
    budget_cents: int
    spent_cents: int
    grant_count: int
    created_at: str
    sponsor_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_trunc_expr(col):
    """
    Return a date-truncation expression that works on both PostgreSQL and SQLite.

    PostgreSQL: func.date(column) returns a DATE type.
    SQLite:     func.date(column) also works (returns 'YYYY-MM-DD' text).
    """
    return func.date(col)


def _cutoff(days: int) -> datetime:
    return datetime.utcnow() - timedelta(days=days)


# ---------------------------------------------------------------------------
# 1. Daily session counts
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=List[DailySessionCount])
def analytics_sessions(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Daily session counts and total kWh for a given period."""
    cutoff = _cutoff(days)
    date_col = _date_trunc_expr(SessionEvent.session_start)

    rows = (
        db.query(
            date_col.label("date"),
            func.count(SessionEvent.id).label("count"),
            func.coalesce(func.sum(SessionEvent.kwh_delivered), 0).label("total_kwh"),
        )
        .filter(SessionEvent.session_start >= cutoff)
        .group_by(date_col)
        .order_by(date_col)
        .all()
    )

    return [
        DailySessionCount(
            date=str(row.date),
            count=row.count,
            total_kwh=round(float(row.total_kwh), 2),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 2. Daily active / new driver counts
# ---------------------------------------------------------------------------

@router.get("/drivers", response_model=List[DailyDriverCount])
def analytics_drivers(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Daily active driver count and new-driver count (first session that day)."""
    cutoff = _cutoff(days)
    date_col = _date_trunc_expr(SessionEvent.session_start)

    # Active drivers per day
    active_rows = (
        db.query(
            date_col.label("date"),
            func.count(distinct(SessionEvent.driver_user_id)).label("active_drivers"),
        )
        .filter(SessionEvent.session_start >= cutoff)
        .group_by(date_col)
        .order_by(date_col)
        .all()
    )
    active_map = {str(r.date): r.active_drivers for r in active_rows}

    # First-ever session date for each driver (computed once)
    first_session_subq = (
        db.query(
            SessionEvent.driver_user_id,
            func.min(_date_trunc_expr(SessionEvent.session_start)).label("first_date"),
        )
        .group_by(SessionEvent.driver_user_id)
        .subquery()
    )

    # New drivers per day (their first_date falls in the window)
    new_rows = (
        db.query(
            first_session_subq.c.first_date.label("date"),
            func.count(first_session_subq.c.driver_user_id).label("new_drivers"),
        )
        .filter(first_session_subq.c.first_date >= func.date(cutoff))
        .group_by(first_session_subq.c.first_date)
        .all()
    )
    new_map = {str(r.date): r.new_drivers for r in new_rows}

    # Merge into a single list keyed on date
    all_dates = sorted(set(list(active_map.keys()) + list(new_map.keys())))
    return [
        DailyDriverCount(
            date=d,
            active_drivers=active_map.get(d, 0),
            new_drivers=new_map.get(d, 0),
        )
        for d in all_dates
    ]


# ---------------------------------------------------------------------------
# 3. Daily revenue (grants + payouts)
# ---------------------------------------------------------------------------

@router.get("/revenue", response_model=List[DailyRevenue])
def analytics_revenue(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Daily revenue: campaign grant totals and payout totals."""
    cutoff = _cutoff(days)

    # Grants grouped by day
    grant_date_col = _date_trunc_expr(IncentiveGrant.granted_at)
    grant_rows = (
        db.query(
            grant_date_col.label("date"),
            func.coalesce(func.sum(IncentiveGrant.amount_cents), 0).label("grants_cents"),
        )
        .filter(IncentiveGrant.granted_at >= cutoff)
        .filter(IncentiveGrant.granted_at.isnot(None))
        .group_by(grant_date_col)
        .all()
    )
    grant_map = {str(r.date): int(r.grants_cents) for r in grant_rows}

    # Payouts grouped by day (only completed payouts)
    payout_date_col = _date_trunc_expr(Payout.created_at)
    payout_rows = (
        db.query(
            payout_date_col.label("date"),
            func.coalesce(func.sum(Payout.amount_cents), 0).label("payouts_cents"),
        )
        .filter(Payout.created_at >= cutoff)
        .filter(Payout.status == "paid")
        .group_by(payout_date_col)
        .all()
    )
    payout_map = {str(r.date): int(r.payouts_cents) for r in payout_rows}

    all_dates = sorted(set(list(grant_map.keys()) + list(payout_map.keys())))
    return [
        DailyRevenue(
            date=d,
            grants_cents=grant_map.get(d, 0),
            payouts_cents=payout_map.get(d, 0),
        )
        for d in all_dates
    ]


# ---------------------------------------------------------------------------
# 4. Campaign summary
# ---------------------------------------------------------------------------

@router.get("/campaigns", response_model=List[CampaignSummary])
def analytics_campaigns(
    days: int = Query(default=30, ge=1, le=365),
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Campaign summary with grant counts. Optionally filter by status."""
    cutoff = _cutoff(days)

    # Aggregate grant data per campaign
    grant_agg = (
        db.query(
            IncentiveGrant.campaign_id,
            func.coalesce(func.sum(IncentiveGrant.amount_cents), 0).label("total_granted_cents"),
            func.count(IncentiveGrant.id).label("grant_count"),
        )
        .group_by(IncentiveGrant.campaign_id)
        .subquery()
    )

    query = (
        db.query(
            Campaign,
            func.coalesce(grant_agg.c.total_granted_cents, 0).label("total_granted_cents"),
            func.coalesce(grant_agg.c.grant_count, 0).label("grant_count"),
        )
        .outerjoin(grant_agg, Campaign.id == grant_agg.c.campaign_id)
        .filter(Campaign.created_at >= cutoff)
    )

    if status:
        query = query.filter(Campaign.status == status)

    query = query.order_by(Campaign.created_at.desc())
    rows = query.all()

    return [
        CampaignSummary(
            id=str(campaign.id),
            name=campaign.name,
            status=campaign.status,
            budget_cents=campaign.budget_cents,
            spent_cents=campaign.spent_cents,
            grant_count=grant_count,
            created_at=campaign.created_at.isoformat() if campaign.created_at else "",
            sponsor_name=campaign.sponsor_name or "",
        )
        for campaign, total_granted_cents, grant_count in rows
    ]


# ─── Charger Availability History ────────────────────────────────────────────


@router.get("/availability/{charger_id}")
def get_charger_availability_history(
    charger_id: str,
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get availability snapshots for a charger over the last N hours."""
    from app.models.charger_availability import ChargerAvailabilitySnapshot

    since = datetime.utcnow() - timedelta(hours=hours)
    snapshots = (
        db.query(ChargerAvailabilitySnapshot)
        .filter(
            ChargerAvailabilitySnapshot.charger_id == charger_id,
            ChargerAvailabilitySnapshot.recorded_at >= since,
        )
        .order_by(ChargerAvailabilitySnapshot.recorded_at.desc())
        .all()
    )
    return {
        "charger_id": charger_id,
        "hours": hours,
        "snapshots": [
            {
                "recorded_at": s.recorded_at.isoformat(),
                "total_ports": s.total_ports,
                "available_ports": s.available_ports,
                "occupied_ports": s.occupied_ports,
                "out_of_service_ports": s.out_of_service_ports,
                "connector_details": s.connector_details,
                "source": s.source,
            }
            for s in snapshots
        ],
    }


@router.get("/availability")
def get_all_availability_latest(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get the latest availability snapshot for all monitored chargers."""
    from sqlalchemy import func

    from app.models.charger_availability import ChargerAvailabilitySnapshot

    # Subquery for max recorded_at per charger
    latest_sub = (
        db.query(
            ChargerAvailabilitySnapshot.charger_id,
            func.max(ChargerAvailabilitySnapshot.recorded_at).label("max_ts"),
        )
        .group_by(ChargerAvailabilitySnapshot.charger_id)
        .subquery()
    )
    snapshots = (
        db.query(ChargerAvailabilitySnapshot)
        .join(
            latest_sub,
            (ChargerAvailabilitySnapshot.charger_id == latest_sub.c.charger_id)
            & (ChargerAvailabilitySnapshot.recorded_at == latest_sub.c.max_ts),
        )
        .all()
    )
    return {
        "stations": [
            {
                "charger_id": s.charger_id,
                "recorded_at": s.recorded_at.isoformat(),
                "total_ports": s.total_ports,
                "available_ports": s.available_ports,
                "occupied_ports": s.occupied_ports,
                "out_of_service_ports": s.out_of_service_ports,
                "source": s.source,
            }
            for s in snapshots
        ],
    }
