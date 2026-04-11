"""
Merchant analytics and summary services
"""
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.utils.log import get_logger

logger = get_logger(__name__)


def merchant_summary(
    db: Session,
    merchant_id: int,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Generate merchant analytics summary.
    
    Returns:
        {
            "merchant_id": int,
            "verified_sessions": int (sessions within 600m),
            "purchase_rewards": int (count),
            "total_rewards_paid": int (cents),
            "top_hours": dict (hour -> count),
            "last_events": List[dict]
        }
    """
    # Default to last 30 days
    if not from_time:
        from_time = datetime.utcnow() - timedelta(days=30)
    if not to_time:
        to_time = datetime.utcnow()
    
    # Get merchant location
    merchant_result = db.execute(text("""
        SELECT lat, lng FROM merchants WHERE id = :merchant_id
    """), {"merchant_id": merchant_id}).first()
    
    if not merchant_result:
        return {
            "merchant_id": merchant_id,
            "error": "Merchant not found"
        }
    
    merchant_lat = float(merchant_result[0]) if merchant_result[0] else None
    merchant_lng = float(merchant_result[1]) if merchant_result[1] else None
    
    # 1. Verified sessions within 600m (using haversine)
    # We'll approximate by checking sessions that are close enough
    # For production, use PostGIS or similar, but for now use simple bounding box + haversine filter
    verified_sessions = 0
    if merchant_lat and merchant_lng:
        # Get all verified sessions in time window
        sessions_result = db.execute(text("""
            SELECT id, lat, lng, started_at FROM sessions
            WHERE status = 'verified'
            AND verified_at >= :from_time
            AND verified_at <= :to_time
            AND lat IS NOT NULL AND lng IS NOT NULL
        """), {
            "from_time": from_time,
            "to_time": to_time
        })
        
        from app.services.geo import haversine_m
        
        for row in sessions_result:
            session_lat = float(row[1])
            session_lng = float(row[2])
            distance = haversine_m(
                merchant_lat, merchant_lng,
                session_lat, session_lng
            )
            if distance <= 600:  # 600m radius
                verified_sessions += 1
    
    # 2. Purchase rewards tied to this merchant
    purchase_rewards_count = db.execute(text("""
        SELECT COUNT(*) FROM payments
        WHERE merchant_id = :merchant_id
        AND status = 'confirmed'
        AND claimed = 1
        AND created_at >= :from_time
        AND created_at <= :to_time
    """), {
        "merchant_id": merchant_id,
        "from_time": from_time,
        "to_time": to_time
    }).scalar()
    purchase_rewards_count = int(purchase_rewards_count) if purchase_rewards_count else 0
    
    # 3. Total rewards paid (sum of purchase rewards for this merchant)
    total_rewards_result = db.execute(text("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM payments
        WHERE merchant_id = :merchant_id
        AND status = 'confirmed'
        AND claimed = 1
        AND created_at >= :from_time
        AND created_at <= :to_time
    """), {
        "merchant_id": merchant_id,
        "from_time": from_time,
        "to_time": to_time
    }).scalar()
    total_rewards_paid = int(total_rewards_result) if total_rewards_result else 0
    
    # 4. Top hours histogram (from sessions near merchant)
    hours_hist = {}
    if merchant_lat and merchant_lng:
        from app.services.geo import haversine_m
        
        sessions_result = db.execute(text("""
            SELECT lat, lng, verified_at FROM sessions
            WHERE status = 'verified'
            AND verified_at >= :from_time
            AND verified_at <= :to_time
            AND lat IS NOT NULL AND lng IS NOT NULL
        """), {
            "from_time": from_time,
            "to_time": to_time
        })
        
        for row in sessions_result:
            session_lat = float(row[0])
            session_lng = float(row[1])
            verified_at = row[2]
            
            # Only count sessions within 600m
            distance = haversine_m(
                merchant_lat, merchant_lng,
                session_lat, session_lng
            )
            
            if distance <= 600 and verified_at:
                try:
                    if isinstance(verified_at, str):
                        dt = datetime.fromisoformat(verified_at.replace('Z', '+00:00')[:19])
                    else:
                        dt = verified_at
                    hour = dt.hour
                    hours_hist[hour] = hours_hist.get(hour, 0) + 1
                except:
                    pass
    
    # 5. Last 10 events (purchases and verify rewards)
    last_events = []
    
    # Purchases
    purchases_result = db.execute(text("""
        SELECT id, amount_cents, created_at, claimed
        FROM payments
        WHERE merchant_id = :merchant_id
        AND created_at >= :from_time
        AND created_at <= :to_time
        ORDER BY created_at DESC
        LIMIT 10
    """), {
        "merchant_id": merchant_id,
        "from_time": from_time,
        "to_time": to_time
    })
    
    for row in purchases_result:
        last_events.append({
            "type": "purchase",
            "id": str(row[0]),
            "amount_cents": row[1],
            "created_at": str(row[2]) if row[2] else None,
            "claimed": bool(row[3]) if row[3] else False
        })
    
    # Sort by created_at DESC and limit to 10
    last_events.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    last_events = last_events[:10]
    
    return {
        "merchant_id": merchant_id,
        "verified_sessions": verified_sessions,
        "purchase_rewards": purchase_rewards_count,
        "total_rewards_paid": total_rewards_paid,
        "top_hours": hours_hist,
        "last_events": last_events,
        "period": {
            "from": from_time.isoformat(),
            "to": to_time.isoformat()
        }
    }


def merchant_offers(db: Session, merchant_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get local and external offers for a merchant.
    
    Returns:
        {
            "local": List[dict],  # From offers table
            "external": List[dict]  # From external feed provider
        }
    """
    # Local offers
    local_offers_result = db.execute(text("""
        SELECT id, title, start_time, end_time, reward_cents, active
        FROM offers
        WHERE merchant_id = :merchant_id
        ORDER BY created_at DESC
        LIMIT 20
    """), {"merchant_id": merchant_id})
    
    local_offers = []
    for row in local_offers_result:
        local_offers.append({
            "id": row[0],
            "title": row[1] or "",
            "window_start": str(row[2]) if row[2] else None,
            "window_end": str(row[3]) if row[3] else None,
            "est_reward_cents": int(row[4]) if row[4] else 0,
            "active": bool(row[5]) if row[5] else False
        })
    
    # External offers (via provider)
    from app.services.offers_feed import fetch_external_offers
    external_offers = fetch_external_offers(db, merchant_id)
    
    return {
        "local": local_offers,
        "external": external_offers
    }


def merchant_billing_summary(
    db: Session,
    merchant_id: str,
    period_start: Optional[datetime] = None,
    period_end: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Get billing summary for a merchant based on Nova redemptions.
    
    Merchants only pay the platform fee on redeemed Nova, not the full redemption amount.
    
    Args:
        db: Database session
        merchant_id: Merchant ID (string)
        period_start: Start of billing period (defaults to start of current month)
        period_end: End of billing period (defaults to end of current month)
    
    Returns:
        {
            "period_start": "2025-12-01",
            "period_end": "2025-12-31",
            "nova_redeemed_cents": 200,
            "platform_fee_bps": 1500,
            "platform_fee_cents": 30,
            "status": "pending",
            "settlement_method": "invoice"
        }
    """
    # Default to current calendar month
    now = datetime.utcnow()
    if not period_start:
        period_start = datetime(now.year, now.month, 1)
    if not period_end:
        # Last day of current month
        last_day = monthrange(now.year, now.month)[1]
        period_end = datetime(now.year, now.month, last_day, 23, 59, 59)
    
    # Import MerchantRedemption model
    from app.models.domain import MerchantRedemption
    
    # Aggregate redemptions for this merchant in the period
    result = db.query(
        func.sum(MerchantRedemption.nova_spent_cents).label('total_nova_cents')
    ).filter(
        MerchantRedemption.merchant_id == merchant_id,
        MerchantRedemption.created_at >= period_start,
        MerchantRedemption.created_at <= period_end
    ).first()
    
    nova_redeemed_cents = int(result[0]) if result[0] else 0
    
    # Calculate platform fee (in basis points, so divide by 10000)
    platform_fee_bps = settings.PLATFORM_FEE_BPS
    platform_fee_cents = int(round(nova_redeemed_cents * platform_fee_bps / 10000))
    
    return {
        "period_start": period_start.strftime("%Y-%m-%d"),
        "period_end": period_end.strftime("%Y-%m-%d"),
        "nova_redeemed_cents": nova_redeemed_cents,
        "platform_fee_bps": platform_fee_bps,
        "platform_fee_cents": platform_fee_cents,
        "status": "pending",  # Always pending for now
        "settlement_method": "invoice"  # Future: ach | card
    }
