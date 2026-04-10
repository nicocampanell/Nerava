from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_extra import CommunityPeriod, Follow, RewardEvent

router = APIRouter(prefix="/v1/social", tags=["social"])

class FollowRequest(BaseModel):
    follower_id: str
    followee_id: str
    follow: bool

class FeedItem(BaseModel):
    id: int
    user_id: str
    source: str
    gross_cents: int
    community_cents: int
    net_cents: int
    meta: dict
    timestamp: datetime

@router.post("/follow")
async def follow(request: FollowRequest, db: Session = Depends(get_db)):
    """Toggle follow relationship between users."""
    if request.follower_id == request.followee_id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")
    
    existing = db.query(Follow).filter(
        Follow.follower_id == request.follower_id,
        Follow.followee_id == request.followee_id
    ).first()
    
    if request.follow and not existing:
        follow = Follow(follower_id=request.follower_id, followee_id=request.followee_id)
        db.add(follow)
        db.commit()
    elif not request.follow and existing:
        db.delete(existing)
        db.commit()
    
    return {"ok": True, "following": request.follow}

@router.get("/followers")
async def get_followers(user_id: str = Query(...), db: Session = Depends(get_db)):
    """Get list of users following the given user."""
    followers = db.query(Follow).filter(Follow.followee_id == user_id).all()
    return [{"follower_id": f.follower_id, "created_at": f.created_at} for f in followers]

@router.get("/following")
async def get_following(user_id: str = Query(...), db: Session = Depends(get_db)):
    """Get list of users the given user is following."""
    following = db.query(Follow).filter(Follow.follower_id == user_id).all()
    return [{"followee_id": f.followee_id, "created_at": f.created_at} for f in following]

@router.get("/feed")
async def get_feed(
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None), 
    radius_km: Optional[float] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Get recent reward events (community activity feed)."""
    events = db.query(RewardEvent).order_by(RewardEvent.created_at.desc()).limit(limit).all()
    
    feed_items = []
    for event in events:
        feed_items.append(FeedItem(
            id=event.id,
            user_id=event.user_id,
            source=event.source,
            gross_cents=event.gross_cents,
            community_cents=event.community_cents,
            net_cents=event.net_cents,
            meta=event.meta,
            timestamp=event.created_at
        ))
    
    return feed_items

@router.get("/pool")
async def get_pool(period_key: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Get community pool statistics for a period."""
    if not period_key:
        period_key = datetime.utcnow().strftime("%Y-%m")
    
    period = db.query(CommunityPeriod).filter(CommunityPeriod.period_key == period_key).first()
    if not period:
        return {
            "period_key": period_key,
            "total_gross_cents": 0,
            "total_community_cents": 0,
            "total_distributed_cents": 0
        }
    
    return {
        "period_key": period.period_key,
        "total_gross_cents": period.total_gross_cents,
        "total_community_cents": period.total_community_cents,
        "total_distributed_cents": period.total_distributed_cents
    }
