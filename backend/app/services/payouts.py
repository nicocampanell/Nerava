from datetime import datetime

from sqlalchemy.orm import Session

from ..models_extra import CommunityPeriod, FollowerShare
from .wallet import credit_wallet


def settle_unpaid_follower_shares(db: Session, limit: int = 500) -> int:
    """Settle unpaid follower shares by crediting wallets."""
    rows = db.query(FollowerShare).filter(FollowerShare.settled == False).limit(limit).all()
    count = 0
    for row in rows:
        credit_wallet(row.payee_user_id, row.cents, "USD")
        row.settled = True
        count += 1
        key = datetime.utcnow().strftime("%Y-%m")
        p = db.query(CommunityPeriod).filter(CommunityPeriod.period_key == key).first()
        if p: 
            p.total_distributed_cents += row.cents
    db.commit()
    return count
