"""
Merchant Reports API Router

Provides endpoints for merchant reporting functionality and self-service insights.
"""
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer as SQLInteger
from sqlalchemy import and_, cast, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.domain import require_admin, require_merchant_admin
from app.models import User
from app.models.session_event import SessionEvent
from app.models.while_you_charge import Charger
from app.services.auth_service import AuthService
from app.services.merchant_reports import (
    MerchantReport,
    get_merchant_report,
)
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/merchants", tags=["merchant-reports"])
logger = get_logger(__name__)

# K-anonymity threshold: metrics require at least this many unique drivers
K_ANONYMITY_MIN = 5
# Radius in meters for "nearby" charger matching
INSIGHTS_RADIUS_M = 500


def _parse_period(period: str) -> tuple:
    """
    Parse period string into (period_start, period_end) datetime tuple.
    
    Supported periods:
    - "week": Last 7 days (including today)
    - "30d": Last 30 days (including today)
    """
    now = datetime.utcnow()
    
    if period == "week":
        period_start = now - timedelta(days=7)
        period_end = now
    elif period == "30d":
        period_start = now - timedelta(days=30)
        period_end = now
    else:
        raise ValueError(f"Unsupported period: {period}. Use 'week' or '30d'")
    
    return period_start, period_end


@router.get("/{merchant_id}/report", response_model=MerchantReport)
def get_merchant_report_endpoint(
    merchant_id: str,
    period: str = Query("week", description="Reporting period: 'week' or '30d'"),
    avg_ticket_cents: Optional[int] = Query(None, description="Average ticket size in cents (overrides default)"),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Get merchant report for a specific merchant.
    
    Returns aggregated metrics for the specified period:
    - EV visits (verified merchant visits)
    - Unique drivers
    - Total Nova awarded
    - Total rewards in cents
    - Implied revenue (if avg_ticket_cents provided)
    
    P0-B Security: Requires admin role.
    """
    # Parse period
    try:
        period_start, period_end = _parse_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get report
    try:
        report = get_merchant_report(
            db=db,
            merchant_id=merchant_id,
            period_start=period_start,
            period_end=period_end,
            avg_ticket_cents=avg_ticket_cents
        )
        
        if not report:
            raise HTTPException(status_code=404, detail=f"Merchant {merchant_id} not found")
        
        return report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate merchant report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)}")


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two lat/lng points."""
    R = 6371000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return R * c


def _get_nearby_charger_ids(db: Session, merchant_lat: float, merchant_lng: float) -> list[str]:
    """Find charger IDs within INSIGHTS_RADIUS_M of merchant location."""
    # Use a bounding box pre-filter then refine with haversine
    # ~0.005 degrees ≈ 500m at mid-latitudes
    delta = 0.006
    chargers = db.query(Charger.id, Charger.lat, Charger.lng).filter(
        Charger.lat.between(merchant_lat - delta, merchant_lat + delta),
        Charger.lng.between(merchant_lng - delta, merchant_lng + delta),
    ).all()

    nearby_ids = []
    for cid, clat, clng in chargers:
        if _haversine_m(merchant_lat, merchant_lng, clat, clng) <= INSIGHTS_RADIUS_M:
            nearby_ids.append(cid)
    return nearby_ids


def _k_anon(value, unique_drivers: int):
    """Return value if k-anonymity threshold met, else None."""
    return value if unique_drivers >= K_ANONYMITY_MIN else None


@router.get("/me/insights")
def get_merchant_insights(
    period: str = Query("30d", description="Reporting period: 'week' or '30d'"),
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db),
):
    """
    Self-service merchant insights — aggregate EV charging analytics
    for sessions at chargers near the merchant's location.

    Requires merchant_admin JWT. All metrics enforce k-anonymity (min 5 unique drivers).
    """
    # Resolve merchant from JWT user
    merchant = AuthService.get_user_merchant(db, user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found for user")

    try:
        period_start, period_end = _parse_period(period)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Find nearby chargers
    nearby_ids = _get_nearby_charger_ids(db, merchant.lat, merchant.lng)
    if not nearby_ids:
        return {
            "period": period,
            "ev_sessions_nearby": 0,
            "unique_drivers": 0,
            "avg_duration_minutes": None,
            "avg_kwh": None,
            "peak_hours": [],
            "dwell_distribution": None,
            "walk_traffic": None,
        }

    # Query completed sessions at nearby chargers in period
    base_filter = and_(
        SessionEvent.charger_id.in_(nearby_ids),
        SessionEvent.session_start >= period_start,
        SessionEvent.session_start <= period_end,
        SessionEvent.session_end.isnot(None),
    )

    # Core aggregates
    stats = db.query(
        func.count(SessionEvent.id).label("total"),
        func.count(func.distinct(SessionEvent.driver_user_id)).label("unique_drivers"),
        func.avg(SessionEvent.duration_minutes).label("avg_duration"),
        func.avg(SessionEvent.kwh_delivered).label("avg_kwh"),
    ).filter(base_filter).first()

    total_sessions = stats.total or 0
    unique_drivers = stats.unique_drivers or 0
    avg_duration = round(float(stats.avg_duration), 1) if stats.avg_duration else None
    avg_kwh = round(float(stats.avg_kwh), 1) if stats.avg_kwh else None

    # Apply k-anonymity
    avg_duration = _k_anon(avg_duration, unique_drivers)
    avg_kwh = _k_anon(avg_kwh, unique_drivers)

    # Peak hours (group by hour of day)
    peak_hours = []
    if unique_drivers >= K_ANONYMITY_MIN:
        dialect = db.bind.dialect.name if db.bind else "sqlite"
        if dialect == "postgresql":
            hour_expr = func.extract("hour", SessionEvent.session_start)
        else:
            hour_expr = cast(func.strftime("%H", SessionEvent.session_start), SQLInteger)

        hour_rows = db.query(
            hour_expr.label("hour"),
            func.count(SessionEvent.id).label("sessions"),
        ).filter(base_filter).group_by("hour").order_by("hour").all()

        peak_hours = [{"hour": int(r.hour), "sessions": r.sessions} for r in hour_rows]

    # Dwell time distribution
    dwell_distribution = None
    if unique_drivers >= K_ANONYMITY_MIN:
        sessions = db.query(SessionEvent.duration_minutes).filter(
            base_filter,
            SessionEvent.duration_minutes.isnot(None),
        ).all()

        under_15 = sum(1 for (d,) in sessions if d is not None and d < 15)
        m15_30 = sum(1 for (d,) in sessions if d is not None and 15 <= d < 30)
        m30_60 = sum(1 for (d,) in sessions if d is not None and 30 <= d < 60)
        over_60 = sum(1 for (d,) in sessions if d is not None and d >= 60)

        dwell_distribution = {
            "under_15min": under_15,
            "15_30min": m15_30,
            "30_60min": m30_60,
            "over_60min": over_60,
        }

    # Walk traffic from location trails
    walk_traffic = None
    if unique_drivers >= K_ANONYMITY_MIN:
        # Query sessions with metadata containing location trails
        trail_sessions = db.query(
            SessionEvent.session_metadata,
        ).filter(
            base_filter,
            SessionEvent.session_metadata.isnot(None),
        ).all()

        total_with_trail = 0
        visited_count = 0
        walk_distances = []

        for (metadata,) in trail_sessions:
            if not metadata:
                continue
            trail = metadata.get("location_trail", [])
            if len(trail) < 2:
                continue
            total_with_trail += 1

            # Check if any trail point is within 200m of merchant
            min_dist = float("inf")
            for pt in trail:
                d = _haversine_m(merchant.lat, merchant.lng, pt.get("lat", 0), pt.get("lng", 0))
                if d < min_dist:
                    min_dist = d
            if min_dist <= 200:
                visited_count += 1

            # Compute total walk distance along trail
            total_dist = 0.0
            for i in range(1, len(trail)):
                total_dist += _haversine_m(
                    trail[i - 1].get("lat", 0), trail[i - 1].get("lng", 0),
                    trail[i].get("lat", 0), trail[i].get("lng", 0),
                )
            walk_distances.append(total_dist)

        if total_with_trail > 0:
            walk_traffic = {
                "visited_area": round(100 * visited_count / total_with_trail),
                "avg_walk_distance_m": round(sum(walk_distances) / len(walk_distances)) if walk_distances else 0,
            }

    # Check Pro subscription for session/customer detail gating
    has_pro = False
    session_details = None
    customer_details = None
    try:
        from app.services.merchant_onboarding_service import create_or_get_merchant_account
        from app.services.merchant_subscription_service import is_pro
        merchant_account = create_or_get_merchant_account(db, user.id)
        has_pro = is_pro(db, merchant_account.id)

        if has_pro and unique_drivers >= K_ANONYMITY_MIN:
            # Session-level detail (Pro only)
            recent_sessions = db.query(
                SessionEvent.id,
                SessionEvent.session_start,
                SessionEvent.session_end,
                SessionEvent.duration_minutes,
                SessionEvent.kwh_delivered,
                SessionEvent.charger_id,
            ).filter(
                base_filter,
            ).order_by(SessionEvent.session_start.desc()).limit(50).all()

            session_details = [
                {
                    "id": str(s.id),
                    "start": s.session_start.isoformat() if s.session_start else None,
                    "end": s.session_end.isoformat() if s.session_end else None,
                    "duration_minutes": round(float(s.duration_minutes), 1) if s.duration_minutes else None,
                    "kwh": round(float(s.kwh_delivered), 1) if s.kwh_delivered else None,
                    "charger_id": str(s.charger_id) if s.charger_id else None,
                }
                for s in recent_sessions
            ]

            # Customer visit frequency (Pro only)
            visit_freq = db.query(
                SessionEvent.driver_user_id,
                func.count(SessionEvent.id).label("visit_count"),
            ).filter(base_filter).group_by(
                SessionEvent.driver_user_id
            ).order_by(func.count(SessionEvent.id).desc()).limit(20).all()

            customer_details = [
                {"driver_id_hash": str(hash(v.driver_user_id))[-8:], "visit_count": v.visit_count}
                for v in visit_freq
            ]
    except Exception as e:
        logger.warning(f"Error checking Pro subscription: {e}")

    return {
        "period": period,
        "ev_sessions_nearby": _k_anon(total_sessions, unique_drivers) or 0,
        "unique_drivers": _k_anon(unique_drivers, unique_drivers) or 0,
        "avg_duration_minutes": avg_duration,
        "avg_kwh": avg_kwh,
        "peak_hours": peak_hours,
        "dwell_distribution": dwell_distribution,
        "walk_traffic": walk_traffic,
        "has_pro_subscription": has_pro,
        "session_details": session_details,
        "customer_details": customer_details,
    }

