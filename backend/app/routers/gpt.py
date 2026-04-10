import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.security.tokens import create_verify_token
from app.utils.names import normalize_merchant_name

router = APIRouter(prefix="/v1/gpt", tags=["gpt"])


class CreateSessionLinkRequest(BaseModel):
    user_id: int
    lat: Optional[float] = None
    lng: Optional[float] = None
    charger_hint: Optional[str] = None

@router.get("/find_merchants")
async def find_merchants(
    lat: float = Query(...),
    lng: float = Query(...),
    category: Optional[str] = Query(None),
    radius_m: Optional[int] = Query(1500, ge=0, le=10000),
    db: Session = Depends(get_db)
):
    """
    Find nearby merchants with optional category filter.
    Returns merchants sorted by distance with attached offers if available.
    """

    from app.services.geo import haversine_m
    
    # Fetch all merchants (or filter by category if provided)
    query = "SELECT id, name, category, lat, lng FROM merchants WHERE 1=1"
    params = {}
    
    if category:
        query += " AND LOWER(category) = LOWER(:category)"
        params["category"] = category
    
    result = db.execute(text(query), params)
    merchants = []
    
    for row in result:
        merch_lat = float(row.lat)
        merch_lng = float(row.lng)
        distance_m = haversine_m(lat, lng, merch_lat, merch_lng)
        
        if distance_m <= radius_m:
            # Fetch active offers for this merchant
            offers_result = db.execute(text("""
                SELECT title, start_time, end_time, reward_cents
                FROM offers
                WHERE merchant_id = :merchant_id AND active = 1
                LIMIT 1
            """), {"merchant_id": row.id})
            offer_row = offers_result.first()
            
            offer_obj = None
            if offer_row:
                offer_obj = {
                    "title": offer_row.title or "",
                    "window_start": str(offer_row.start_time) if offer_row.start_time else None,
                    "window_end": str(offer_row.end_time) if offer_row.end_time else None,
                    "est_reward_cents": int(offer_row.reward_cents) if offer_row.reward_cents else 0
                }
            
            merchants.append({
                "merchant_id": row.id,
                "name": row.name,
                "category": row.category,
                "lat": merch_lat,
                "lng": merch_lng,
                "distance_m": round(distance_m, 1),
                "has_offer": offer_row is not None,
                "offer": offer_obj
            })
    
    # Deduplicate by (normalized_name, rounded_latlng)
    # Key: (normalized_name, round(lat,5), round(lng,5))
    dedup_map = {}
    for merch in merchants:
        normalized = normalize_merchant_name(merch["name"])
        rounded_lat = round(merch["lat"], 5)
        rounded_lng = round(merch["lng"], 5)
        key = (normalized, rounded_lat, rounded_lng)
        
        # Keep the merchant with the smallest distance_m
        if key not in dedup_map or merch["distance_m"] < dedup_map[key]["distance_m"]:
            dedup_map[key] = merch
    
    # Convert back to list and sort by distance
    unique_merchants = list(dedup_map.values())
    unique_merchants.sort(key=lambda x: x["distance_m"])
    
    # Return with hints if empty
    if not unique_merchants:
        from app.utils.hints import build_empty_state_hint
        return {
            "items": [],
            "hint": build_empty_state_hint("merchants", lat, lng, radius_m, category)
        }
    
    return unique_merchants


@router.get("/find_charger")
async def find_charger(
    lat: float = Query(...),
    lng: float = Query(...),
    city: Optional[str] = Query(None),
    radius_m: Optional[int] = Query(2000, ge=0, le=10000),
    db: Session = Depends(get_db)
):
    """
    Find nearby chargers with green window times and nearby merchants.
    """
    from datetime import datetime
    from datetime import time as dt_time

    from app.services.chargers_openmap import fetch_chargers
    from app.services.geo import haversine_m
    
    # Fetch chargers from OpenChargeMap
    radius_km = radius_m / 1000.0
    try:
        charger_data = await fetch_chargers(lat, lng, radius_km=radius_km, max_results=20)
    except Exception as e:
        # Fallback to empty list if API fails
        charger_data = []
    
    # Compute green window (14:00-16:00)
    now = datetime.now().time()
    if dt_time(14, 0) <= now < dt_time(16, 0):
        green_window = {"start": "14:00", "end": "16:00"}
    else:
        # Next occurrence today or tomorrow
        if now >= dt_time(16, 0):
            green_window = {"start": "14:00", "end": "16:00"}  # Tomorrow
        else:
            green_window = {"start": "14:00", "end": "16:00"}  # Today
    
    # Fetch all merchants for nearby lookup
    merchants_result = db.execute(text("SELECT id, name, category, lat, lng FROM merchants"))
    all_merchants = [
        {"id": row.id, "name": row.name, "category": row.category, "lat": float(row.lat), "lng": float(row.lng)}
        for row in merchants_result
    ]
    
    chargers = []
    for charger in charger_data:
        charger_lat = charger.get("location", {}).get("lat")
        charger_lng = charger.get("location", {}).get("lng")
        
        if not charger_lat or not charger_lng:
            continue
        
        distance_m = haversine_m(lat, lng, charger_lat, charger_lng)
        if distance_m > radius_m:
            continue
        
        # Find nearby merchants (within 600m)
        nearby_merchants = []
        for merch in all_merchants:
            merch_dist = haversine_m(charger_lat, charger_lng, merch["lat"], merch["lng"])
            if merch_dist <= 600:
                nearby_merchants.append({
                    "merchant_id": merch["id"],
                    "name": merch["name"],
                    "category": merch["category"],
                    "distance_m": round(merch_dist, 1)
                })
        
        # Sort by distance and take top 5
        nearby_merchants.sort(key=lambda x: x["distance_m"])
        nearby_merchants = nearby_merchants[:5]
        
        chargers.append({
            "charger_id": str(charger.get("id", "")),
            "name": charger.get("title", "Unknown Charger"),
            "lat": charger_lat,
            "lng": charger_lng,
            "network": charger.get("operator", "unknown"),
            "distance_m": round(distance_m, 1),
            "green_window": green_window,
            "nearby_merchants": nearby_merchants
        })
    
    # Sort by distance
    chargers.sort(key=lambda x: x["distance_m"])
    
    # Return with hints if empty
    if not chargers:
        from app.utils.hints import build_empty_state_hint
        return {
            "items": [],
            "hint": build_empty_state_hint("chargers", lat, lng, radius_m)
        }
    
    return chargers

@router.post("/create_session_link")
def create_session_link(request: CreateSessionLinkRequest, db: Session = Depends(get_db)):
    """
    Create a session and return a signed verify link.
    
    Rate limit: 5/min per user (handled by middleware if configured)
    """

    from app.services.fraud import compute_risk_score, emit_abuse_event
    
    now = datetime.utcnow()
    
    # Check risk score before creating session
    risk_result = compute_risk_score(db, user_id=request.user_id, now=now)
    
    if risk_result["score"] >= settings.block_score_threshold:
        emit_abuse_event(
            db,
            user_id=request.user_id,
            event_type="session_blocked",
            severity=risk_result["score"],
            details={"reasons": risk_result["reasons"]}
        )
        
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "RATE_LIMITED",
                    "reason": "risk_block",
                    "score": risk_result["score"],
                    "reasons": risk_result["reasons"]
                }
            }
        )
    
    session_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=30)
    
    # Insert session row
    try:
        db.execute(text("""
            INSERT INTO sessions (
                id, user_id, session_type, status, charger_id,
                started_at, expires_at, created_at
            ) VALUES (
                :id, :user_id, 'gpt', 'started', :charger_id,
                :started_at, :expires_at, :created_at
            )
        """), {
            "id": session_id,
            "user_id": request.user_id,
            "charger_id": request.charger_hint,
            "started_at": now,
            "expires_at": expires_at,
            "created_at": now
        })
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create session: {str(e)}")
    
    # Create JWT token
    token = create_verify_token(
        user_id=request.user_id,
        session_id=session_id,
        ttl_seconds=600  # 10 minutes
    )
    
    # Build verify URL
    base_url = settings.public_base_url.rstrip('/')
    verify_url = f"{base_url}/verify/{token}"
    
    return {
        "session_id": session_id,
        "url": verify_url,
        "expires_at": expires_at.isoformat()
    }

@router.get("/me")
def get_me(
    user_id: int = Query(...),
    db: Session = Depends(get_db)
):
    """
    Get user summary with wallet balance, social counts, and monthly earnings.
    """
    from datetime import datetime
    
    # Get user handle
    user_result = db.execute(text("""
        SELECT handle FROM users WHERE id = :user_id
    """), {"user_id": user_id}).first()
    handle = user_result[0] if user_result and user_result[0] else None
    
    # Count followers (users following this user) - follows table uses follower_id and followee_id
    followers_result = db.execute(text("""
        SELECT COUNT(*) FROM follows WHERE followee_id = :user_id
    """), {"user_id": str(user_id)}).scalar()
    followers = int(followers_result) if followers_result else 0
    
    # Count following (users this user follows)
    following_result = db.execute(text("""
        SELECT COUNT(*) FROM follows WHERE follower_id = :user_id
    """), {"user_id": str(user_id)}).scalar()
    following = int(following_result) if following_result else 0
    
    # Calculate wallet balance (sum of all wallet_ledger entries)
    wallet_result = db.execute(text("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
        WHERE user_id = :user_id
    """), {"user_id": user_id}).scalar()
    wallet_cents = int(wallet_result) if wallet_result else 0
    
    # Calculate monthly earnings (current month start to now)
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    
    # Calculate monthly earnings using existing reward_events schema (net_cents)
    month_earnings_result = db.execute(text("""
        SELECT COALESCE(SUM(net_cents), 0) FROM reward_events
        WHERE user_id = :user_id 
        AND created_at >= :month_start
    """), {
        "user_id": str(user_id),
        "month_start": month_start
    }).scalar()
    month_self_cents = int(month_earnings_result) if month_earnings_result else 0
    
    # month_pool_cents placeholder (for future allocations)
    month_pool_cents = 0

    # Recent rewards (last 3)
    recent = []
    try:
        rows = db.execute(text("""
            SELECT source, gross_cents, net_cents, created_at
            FROM reward_events
            WHERE user_id = :uid
            ORDER BY created_at DESC
            LIMIT 3
        """), {"uid": str(user_id)}).fetchall()
        for r in rows:
            source = r.source or "reward"
            cents = int(r.gross_cents or 0)
            when = r.created_at.isoformat() if hasattr(r.created_at, 'isoformat') and r.created_at else str(r.created_at)
            recent.append({"when": when, "source": source, "cents": cents})
    except Exception:
        pass
    
    return {
        "handle": handle or f"user_{user_id}",
        "reputation": 0,  # Placeholder
        "followers": followers,
        "following": following,
        "wallet_cents": wallet_cents,
        "month_self_cents": month_self_cents,
        "month_pool_cents": month_pool_cents,
        "recent_rewards": recent
    }

@router.post("/follow")
def follow(user_id: int):
    """Follow a user (stub)"""
    return {"ok": True}

@router.post("/unfollow")
def unfollow(user_id: int):
    """Unfollow a user (stub)"""
    return {"ok": True}

@router.post("/redeem")
def redeem(intent_id: str):
    """Redeem an intent (log only)"""
    # Log the redemption attempt
    print(f"[LOG] Redeem attempt for intent_id={intent_id}")
    return {"ok": True, "message": "Redemption logged"}

