"""
Merchant Weekly Report Generator Service

Aggregates key metrics per merchant over a time window for the Domain pilot.
"""
import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models_extra import RewardEvent
from app.models_while_you_charge import Merchant
from app.services.nova import cents_to_nova
from app.utils.log import get_logger

logger = get_logger(__name__)

# Default average ticket size in cents ($8 = 800 cents)
DEFAULT_AVG_TICKET_CENTS = 800


class MerchantReport(BaseModel):
    """Merchant report data model"""
    merchant_id: str
    merchant_name: str
    period_start: datetime
    period_end: datetime
    ev_visits: int
    unique_drivers: int
    total_nova_awarded: int
    total_rewards_cents: int
    implied_revenue_cents: Optional[int] = None


def _parse_merchant_id_from_meta(meta: dict) -> Optional[str]:
    """Extract merchant_id from RewardEvent meta JSON."""
    if not meta:
        return None
    
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return None
    
    if isinstance(meta, dict):
        return meta.get("merchant_id")
    
    return None


def get_merchant_report(
    db: Session,
    merchant_id: str,
    period_start: datetime,
    period_end: datetime,
    avg_ticket_cents: Optional[int] = None,
) -> Optional[MerchantReport]:
    """
    Generate a merchant report for a specific merchant over a time period.
    
    Args:
        db: Database session
        merchant_id: Merchant ID (string)
        period_start: Start of reporting period
        period_end: End of reporting period
        avg_ticket_cents: Optional average ticket size in cents. If None, uses default.
    
    Returns:
        MerchantReport if merchant found, None otherwise
    """
    # Get merchant info
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if not merchant:
        logger.warning(f"Merchant {merchant_id} not found")
        return None
    
    # Use default avg ticket if not provided
    if avg_ticket_cents is None:
        avg_ticket_cents = DEFAULT_AVG_TICKET_CENTS
    
    # Query reward events for this merchant in the period
    # Merchant visits are stored as RewardEvent with source="MERCHANT_VISIT" or "merchant_visit"
    # and merchant_id in meta JSON
    
    # Get all reward events in the period
    events = db.query(RewardEvent).filter(
        RewardEvent.created_at >= period_start,
        RewardEvent.created_at < period_end,
        RewardEvent.source.in_(["MERCHANT_VISIT", "merchant_visit"])
    ).all()
    
    # Filter events that match this merchant_id from meta
    merchant_events = []
    for event in events:
        meta_merchant_id = _parse_merchant_id_from_meta(event.meta)
        if meta_merchant_id == merchant_id:
            merchant_events.append(event)
    
    # Calculate metrics
    ev_visits = len(merchant_events)
    unique_drivers = len(set(e.user_id for e in merchant_events))
    total_rewards_cents = sum(e.gross_cents for e in merchant_events)
    total_nova_awarded = cents_to_nova(total_rewards_cents)
    
    # Calculate implied revenue
    implied_revenue_cents = ev_visits * avg_ticket_cents if ev_visits > 0 else None
    
    return MerchantReport(
        merchant_id=merchant_id,
        merchant_name=merchant.name,
        period_start=period_start,
        period_end=period_end,
        ev_visits=ev_visits,
        unique_drivers=unique_drivers,
        total_nova_awarded=total_nova_awarded,
        total_rewards_cents=total_rewards_cents,
        implied_revenue_cents=implied_revenue_cents
    )


def get_domain_merchant_reports_for_period(
    db: Session,
    period_start: datetime,
    period_end: datetime,
    avg_ticket_cents: Optional[int] = None,
) -> List[MerchantReport]:
    """
    Get reports for all Domain merchants for a given period.
    
    This function finds merchants that have had visits in the period,
    rather than relying on DOMAIN_MERCHANT_IDS (which may not be populated).
    
    Args:
        db: Database session
        period_start: Start of reporting period
        period_end: End of reporting period
        avg_ticket_cents: Optional average ticket size in cents
    
    Returns:
        List of MerchantReport objects, one per merchant with visits in period
    """
    # Get all reward events for merchant visits in the period
    events = db.query(RewardEvent).filter(
        RewardEvent.created_at >= period_start,
        RewardEvent.created_at < period_end,
        RewardEvent.source.in_(["MERCHANT_VISIT", "merchant_visit"])
    ).all()
    
    # Group by merchant_id from meta
    merchant_ids = set()
    for event in events:
        meta_merchant_id = _parse_merchant_id_from_meta(event.meta)
        if meta_merchant_id:
            merchant_ids.add(meta_merchant_id)
    
    # Generate reports for each merchant
    reports = []
    for merchant_id in merchant_ids:
        report = get_merchant_report(
            db=db,
            merchant_id=merchant_id,
            period_start=period_start,
            period_end=period_end,
            avg_ticket_cents=avg_ticket_cents
        )
        if report:
            reports.append(report)
    
    # Sort by ev_visits descending
    reports.sort(key=lambda r: r.ev_visits, reverse=True)
    
    return reports

