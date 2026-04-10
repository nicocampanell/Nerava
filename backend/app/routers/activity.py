from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User, UserReputation

router = APIRouter()

@router.get("/v1/activity")
async def get_activity_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's activity data including reputation and follow earnings"""
    
    # Use authenticated user
    me = str(current_user.id)
    
    # Get current month
    from datetime import datetime
    month = int(datetime.now().strftime("%Y%m"))
    
    # Get reputation with streak_days and counts
    rep = db.query(UserReputation).filter(
        UserReputation.user_id == me
    ).first()

    if rep:
        reputation = {
            'score': rep.score or 0,
            'tier': rep.tier or 'Bronze',
            'streakDays': rep.streak_days or 0,
            'followers_count': rep.followers_count or 0,
            'following_count': rep.following_count or 0,
            'status': 'active'
        }
    else:
        # No reputation row - new user
        reputation = {
            'score': 0,
            'tier': 'Bronze',
            'streakDays': 0,
            'followers_count': 0,
            'following_count': 0,
            'status': 'new'
        }
    
    # Get earnings from monthly table (fallback to demo data)
    earn_query = text("""
        SELECT fem.payer_user_id AS user_id,
               'member' AS handle,
               NULL AS avatar_url,
               'Bronze' AS tier,
               fem.amount_cents,
               fem.context
        FROM follow_earnings_monthly fem
        WHERE fem.month_yyyymm = :month AND fem.receiver_user_id = :user_id
        ORDER BY fem.amount_cents DESC
    """)
    
    earn_result = db.execute(earn_query, {'month': month, 'user_id': me})
    earnings = []
    total_cents = 0
    
    for row in earn_result:
        earnings.append({
            'userId': row[0],
            'handle': row[1],
            'avatarUrl': row[2],
            'tier': row[3],
            'amountCents': int(row[4]),
            'context': row[5]
        })
        total_cents += int(row[4])
    
    # If no earnings found, return empty list (no demo fallback)
    
    return {
        'month': month,
        'reputation': reputation,
        'followEarnings': earnings,
        'totals': {
            'followCents': total_cents
        }
    }

@router.post("/v1/session/verify")
async def verify_session(
    session_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Verify a charging session and trigger auto-follow + rewards"""
    
    # Use authenticated user
    import uuid
    me = str(current_user.id)
    session_id = session_data.get('sessionId', str(uuid.uuid4()))
    station_id = session_data.get('stationId', 'STATION_A')
    energy_kwh = session_data.get('energyKwh', 15.0)
    
    # Get session details
    session_query = text("""
        SELECT id, user_id, station_id, start_at, energy_kwh 
        FROM sessions 
        WHERE id = :session_id AND user_id = :user_id
    """)
    
    session_result = db.execute(session_query, {
        'session_id': session_id,
        'user_id': me
    })
    
    session_row = session_result.fetchone()
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Trigger auto-follow
    from app.services.activity import auto_follow_on_verified_session, reward_followers_for_session
    
    await auto_follow_on_verified_session(me, station_id, session_row[3])
    await reward_followers_for_session(me, session_id, station_id, float(session_row[4] or 0))
    
    return {"ok": True}
