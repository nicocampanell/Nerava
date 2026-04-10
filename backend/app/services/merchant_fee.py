"""
Merchant Fee Service

Records merchant fees for Nova redemptions.
Fee is 15% of Nova redeemed (e.g., 300 cents Nova → 45 cents fee).
"""
import logging
import uuid
from calendar import monthrange
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.domain import MerchantFeeLedger

logger = logging.getLogger(__name__)


def record_merchant_fee(
    db: Session,
    merchant_id: str,
    nova_redeemed_cents: int,
    ts: datetime
) -> int:
    """
    Record merchant fee for a Nova redemption.
    
    Determines the period (month) from ts, upserts the ledger row,
    increments nova_redeemed_cents, and recomputes fee_cents.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        nova_redeemed_cents: Amount of Nova redeemed in this transaction (in cents)
        ts: Timestamp of the redemption (used to determine period)
        
    Returns:
        int: The incremental fee for THIS redemption (e.g., 45 for 300 Nova)
        
    Note:
        - Fee is 15% of Nova redeemed
        - Period is determined by ts (first day of month)
        - If status is "invoiced" or "paid", it is NOT changed
        - Otherwise, status is set to "accruing"
    """
    # Determine period_start as first day of ts month
    period_start = date(ts.year, ts.month, 1)
    
    # Determine period_end as last day of month
    last_day = monthrange(ts.year, ts.month)[1]
    period_end = date(ts.year, ts.month, last_day)
    
    # Get or create ledger row
    ledger = db.query(MerchantFeeLedger).filter(
        MerchantFeeLedger.merchant_id == merchant_id,
        MerchantFeeLedger.period_start == period_start
    ).first()
    
    if not ledger:
        # Create new ledger row
        ledger = MerchantFeeLedger(
            id=str(uuid.uuid4()),
            merchant_id=merchant_id,
            period_start=period_start,
            period_end=period_end,
            nova_redeemed_cents=0,
            fee_cents=0,
            status="accruing"
        )
        db.add(ledger)
        db.flush()
    
    # Increment nova_redeemed_cents
    ledger.nova_redeemed_cents += nova_redeemed_cents
    
    # Recompute fee_cents = round(nova_redeemed_cents * 0.15)
    ledger.fee_cents = round(ledger.nova_redeemed_cents * 0.15)
    
    # Update status only if not "invoiced" or "paid"
    if ledger.status not in ("invoiced", "paid"):
        ledger.status = "accruing"
    
    ledger.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ledger)
    
    # Calculate incremental fee for THIS redemption
    incremental_fee = round(nova_redeemed_cents * 0.15)
    
    logger.info(
        f"Recorded merchant fee: merchant {merchant_id}, "
        f"period {period_start}, nova_redeemed={ledger.nova_redeemed_cents}, "
        f"fee={ledger.fee_cents}, incremental_fee={incremental_fee}"
    )
    
    return incremental_fee

