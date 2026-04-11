from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..models_extra import CommunityPeriod, Follow, FollowerShare, RewardEvent
from ..services.ledger import record_reward_proof

COMMUNITY_PCT = 0.10

def record_reward_event(db: Session, *, user_id: str, source: str, gross_cents: int, meta: Optional[Dict[str, Any]] = None) -> RewardEvent:
    """Record a reward event and distribute community portion to followers."""
    meta = meta or {}
    community = int(round(gross_cents * COMMUNITY_PCT))
    net = max(0, gross_cents - community)

    ev = RewardEvent(
        user_id=user_id, 
        source=source, 
        gross_cents=gross_cents,
        community_cents=community, 
        net_cents=net, 
        meta=meta
    )
    db.add(ev)
    db.flush()  # get ev.id

    # Followers at the time of earning
    followers = [r.follower_id for r in db.query(Follow).filter(Follow.followee_id == user_id).all()]
    n = len(followers)
    if community > 0 and n > 0:
        split = community // n
        remainder = community - split * n
        for i, fid in enumerate(followers):
            cents = split + (1 if i < remainder else 0)
            db.add(FollowerShare(
                reward_event_id=ev.id, 
                payee_user_id=fid, 
                cents=cents, 
                settled=False
            ))

    # Period rollup
    key = datetime.utcnow().strftime("%Y-%m")
    p = db.query(CommunityPeriod).filter(CommunityPeriod.period_key == key).first()
    if not p:
        p = CommunityPeriod(period_key=key)
        db.add(p)
        db.flush()
    p.total_gross_cents += gross_cents
    p.total_community_cents += community

    db.commit()
    db.refresh(ev)
    
    # Record proof in ledger (if enabled)
    try:
        record_reward_proof(ev)
    except Exception as e:
        # Don't fail the reward if ledger fails
        print(f"Warning: Failed to record proof: {e}")
    
    return ev
