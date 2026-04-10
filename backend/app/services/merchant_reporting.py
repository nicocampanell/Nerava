"""
Merchant Reporting Service
Provides merchant summary statistics and shareable social content.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..models.domain import DomainMerchant, MerchantRedemption


def get_merchant_summary(db: Session, merchant_id: str) -> Dict[str, Any]:
    """
    Get aggregated merchant summary statistics.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        
    Returns:
        Dict with summary statistics:
        - total_redemptions: int
        - total_discount_cents: int
        - unique_driver_count: int
        - last_7d_redemptions: int
        - last_30d_redemptions: int
        - avg_discount_cents: float
    """
    # Base query for all redemptions
    base_query = db.query(MerchantRedemption).filter(
        MerchantRedemption.merchant_id == merchant_id
    )
    
    # Total redemptions
    total_redemptions = base_query.count()
    
    # Total discount amount
    total_discount_result = base_query.with_entities(
        func.sum(MerchantRedemption.discount_cents)
    ).scalar()
    total_discount_cents = int(total_discount_result) if total_discount_result else 0
    
    # Unique driver count
    unique_driver_result = base_query.with_entities(
        func.count(func.distinct(MerchantRedemption.driver_user_id))
    ).scalar()
    unique_driver_count = int(unique_driver_result) if unique_driver_result else 0
    
    # Last 7 days
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    last_7d_redemptions = base_query.filter(
        MerchantRedemption.created_at >= seven_days_ago
    ).count()
    
    # Last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    last_30d_redemptions = base_query.filter(
        MerchantRedemption.created_at >= thirty_days_ago
    ).count()
    
    # Average discount
    avg_discount_cents = (
        total_discount_cents / total_redemptions 
        if total_redemptions > 0 else 0
    )
    
    return {
        "total_redemptions": total_redemptions,
        "total_discount_cents": total_discount_cents,
        "unique_driver_count": unique_driver_count,
        "last_7d_redemptions": last_7d_redemptions,
        "last_30d_redemptions": last_30d_redemptions,
        "avg_discount_cents": round(avg_discount_cents, 2)
    }


def get_shareable_stats(db: Session, merchant_id: str) -> List[str]:
    """
    Generate shareable social media stats for merchant.
    
    Creates human-readable stat lines that merchants can share on social media.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        
    Returns:
        List of shareable stat strings
    """
    summary = get_merchant_summary(db, merchant_id)
    
    lines = []
    
    # Line 1: Weekly redemption count
    if summary["last_7d_redemptions"] > 0:
        count = summary["last_7d_redemptions"]
        if count == 1:
            lines.append("We supported 1 EV driver reward with Nerava this week.")
        else:
            lines.append(f"We supported {count} EV driver rewards with Nerava this week.")
    
    # Line 2: Monthly savings
    if summary["last_30d_redemptions"] > 0:
        total_dollars = summary["total_discount_cents"] / 100
        merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
        merchant_name = merchant.name if merchant else "our store"
        
        # For monthly, recalculate from last 30 days only
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        monthly_discount_result = db.query(
            func.sum(MerchantRedemption.discount_cents)
        ).filter(
            and_(
                MerchantRedemption.merchant_id == merchant_id,
                MerchantRedemption.created_at >= thirty_days_ago
            )
        ).scalar()
        monthly_discount_cents = int(monthly_discount_result) if monthly_discount_result else 0
        monthly_dollars = monthly_discount_cents / 100
        
        if monthly_dollars >= 1:
            lines.append(f"EV drivers saved ${monthly_dollars:.0f} using Nova at {merchant_name} this month.")
        else:
            lines.append(f"EV drivers are earning Nova rewards at {merchant_name}.")
    
    # Line 3: Total impact (if substantial)
    if summary["total_redemptions"] >= 10:
        total_dollars = summary["total_discount_cents"] / 100
        lines.append(f"Together, we've supported {summary['total_redemptions']} EV driver rewards totaling ${total_dollars:.0f} in savings.")
    
    # Fallback if no redemptions yet
    if not lines:
        merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
        merchant_name = merchant.name if merchant else "our store"
        lines.append(f"Welcome to Nerava! {merchant_name} is ready to reward EV drivers with Nova.")
    
    return lines

