from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_extra import Challenge, Participation

router = APIRouter(prefix="/v1/challenges", tags=["challenges"])

@router.post("/create")
async def create_challenge(
    name: str,
    scope: str,
    starts_at: datetime,
    ends_at: datetime,
    goal_kwh: int,
    sponsor_merchant_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Create a new group challenge (admin only)."""
    try:
        challenge = Challenge(
            name=name,
            scope=scope,
            starts_at=starts_at,
            ends_at=ends_at,
            goal_kwh=goal_kwh,
            sponsor_merchant_id=sponsor_merchant_id
        )
        db.add(challenge)
        db.flush()
        
        return {
            "id": challenge.id,
            "name": challenge.name,
            "scope": challenge.scope,
            "starts_at": challenge.starts_at,
            "ends_at": challenge.ends_at,
            "goal_kwh": challenge.goal_kwh,
            "sponsor_merchant_id": challenge.sponsor_merchant_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create challenge: {str(e)}")

@router.post("/join")
async def join_challenge(
    challenge_id: int,
    user_id: str,
    db: Session = Depends(get_db)
):
    """Join a challenge."""
    try:
        # Check if challenge exists and is active
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        
        if datetime.utcnow() < challenge.starts_at:
            raise HTTPException(status_code=400, detail="Challenge hasn't started yet")
        
        if datetime.utcnow() > challenge.ends_at:
            raise HTTPException(status_code=400, detail="Challenge has ended")
        
        # Check if user already joined
        existing = db.query(Participation).filter(
            Participation.challenge_id == challenge_id,
            Participation.user_id == user_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="User already joined this challenge")
        
        # Create participation
        participation = Participation(
            challenge_id=challenge_id,
            user_id=user_id,
            kwh=0,
            points=0
        )
        db.add(participation)
        db.commit()
        
        return {"message": "Successfully joined challenge", "challenge_id": challenge_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to join challenge: {str(e)}")

@router.post("/credit")
async def credit_challenge(
    challenge_id: int,
    user_id: str,
    kwh: int,
    db: Session = Depends(get_db)
):
    """Credit kWh to a challenge (called from charging flow)."""
    try:
        # Check if user is participating
        participation = db.query(Participation).filter(
            Participation.challenge_id == challenge_id,
            Participation.user_id == user_id
        ).first()
        
        if not participation:
            raise HTTPException(status_code=404, detail="User not participating in this challenge")
        
        # Check if challenge is still active
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        
        if datetime.utcnow() > challenge.ends_at:
            raise HTTPException(status_code=400, detail="Challenge has ended")
        
        # Update participation
        participation.kwh += kwh
        participation.points += kwh * 10  # 10 points per kWh
        
        # Add challenge info to reward event meta
        # This would be called from the charging flow
        # The actual reward event creation happens elsewhere
        
        db.commit()
        
        return {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "kwh_contributed": participation.kwh,
            "points_earned": participation.points
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to credit challenge: {str(e)}")

@router.get("/leaderboard")
async def get_leaderboard(
    challenge_id: int,
    limit: int = Query(10, description="Number of top participants to return"),
    db: Session = Depends(get_db)
):
    """Get leaderboard for a challenge."""
    try:
        # Get top participants
        participants = db.query(Participation).filter(
            Participation.challenge_id == challenge_id
        ).order_by(desc(Participation.kwh)).limit(limit).all()
        
        leaderboard = []
        for i, participant in enumerate(participants):
            leaderboard.append({
                "rank": i + 1,
                "user_id": participant.user_id,
                "kwh": participant.kwh,
                "points": participant.points
            })
        
        # Get challenge info
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            raise HTTPException(status_code=404, detail="Challenge not found")
        
        # Calculate total progress
        total_kwh = sum(p.kwh for p in participants)
        progress_percent = (total_kwh / challenge.goal_kwh) * 100 if challenge.goal_kwh > 0 else 0
        
        return {
            "challenge_id": challenge_id,
            "challenge_name": challenge.name,
            "goal_kwh": challenge.goal_kwh,
            "total_kwh": total_kwh,
            "progress_percent": min(100, progress_percent),
            "leaderboard": leaderboard
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get leaderboard: {str(e)}")

@router.get("/active")
async def get_active_challenges(
    scope: Optional[str] = Query(None, description="Filter by scope: city or global"),
    db: Session = Depends(get_db)
):
    """Get active challenges."""
    try:
        now = datetime.utcnow()
        query = db.query(Challenge).filter(
            Challenge.starts_at <= now,
            Challenge.ends_at >= now
        )
        
        if scope:
            query = query.filter(Challenge.scope == scope)
        
        challenges = query.all()
        
        return [
            {
                "id": challenge.id,
                "name": challenge.name,
                "scope": challenge.scope,
                "starts_at": challenge.starts_at,
                "ends_at": challenge.ends_at,
                "goal_kwh": challenge.goal_kwh,
                "sponsor_merchant_id": challenge.sponsor_merchant_id
            }
            for challenge in challenges
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get active challenges: {str(e)}")
