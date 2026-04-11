import os
import uuid

from sqlalchemy import text

from app.db import get_db

# Activity configuration
AUTO_FOLLOW_WINDOW_DAYS = int(os.getenv('AUTO_FOLLOW_WINDOW_DAYS', '7'))
REWARD_PER_KWH_CENTS = int(os.getenv('REWARD_PER_KWH_CENTS', '4'))  # 4¢/kWh demo
MIN_REWARD_CENTS = int(os.getenv('MIN_REWARD_CENTS', '5'))  # floor per session

async def auto_follow_on_verified_session(user_id: str, station_id: str, at_timestamp) -> int:
    """Auto-follow drivers who charged at the same station within the window"""
    db = next(get_db())
    
    days = AUTO_FOLLOW_WINDOW_DAYS
    since = f"NOW() - INTERVAL '{days} days'"
    
    # Find drivers who charged at the same station within the window (exclude me)
    query = text("""
        SELECT DISTINCT user_id 
        FROM sessions 
        WHERE station_id = :station_id 
        AND start_at >= :since 
        AND user_id != :user_id
        LIMIT 200
    """)
    
    result = db.execute(query, {
        'station_id': station_id,
        'since': since,
        'user_id': user_id
    })
    
    rows = result.fetchall()
    if not rows:
        return 0
    
    # New user follows the prior drivers
    values = []
    for row in rows:
        values.append(f"('{user_id}','{row[0]}', NOW(), true)")
    
    if values:
        insert_query = text(f"""
            INSERT INTO follows (follower_id, followee_id, created_at, is_auto)
            VALUES {','.join(values)}
            ON CONFLICT DO NOTHING
        """)
        db.execute(insert_query)
        db.commit()
    
    return len(rows)

async def reward_followers_for_session(payer_user_id: str, session_id: str, station_id: str, energy_kwh: float) -> int:
    """Reward followers when someone charges"""
    db = next(get_db())
    
    # Fetch list of followers that should earn
    query = text("SELECT follower_id FROM follows WHERE followee_id = :payer_user_id LIMIT 500")
    result = db.execute(query, {'payer_user_id': payer_user_id})
    followers = result.fetchall()
    
    if not followers:
        return 0
    
    # Calculate reward amount
    per_kwh = REWARD_PER_KWH_CENTS
    cents = max(MIN_REWARD_CENTS, round((energy_kwh or 0) * per_kwh))
    if not isinstance(cents, (int, float)) or cents <= 0:
        cents = MIN_REWARD_CENTS
    
    # Create earnings events
    values = []
    for follower in followers:
        event_id = str(uuid.uuid4())
        values.append(f"('{event_id}','{payer_user_id}','{follower[0]}','{station_id}','{session_id}',{energy_kwh or 0},{cents})")
    
    if values:
        insert_query = text(f"""
            INSERT INTO follow_earnings_events
            (id, payer_user_id, receiver_user_id, station_id, session_id, energy_kwh, amount_cents)
            VALUES {','.join(values)}
        """)
        db.execute(insert_query)
        db.commit()
    
    return len(followers)
