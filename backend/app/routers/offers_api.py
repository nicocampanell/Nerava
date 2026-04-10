"""Offers API router."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.geo import haversine_m

router = APIRouter(prefix="/v1/offers", tags=["offers"])


@router.get("/nearby")
def offers_nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_m: int = Query(1500, ge=0, le=10000),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db)
) -> List[dict]:
    """Get nearby offers."""
    # Query merchants with offers
    query = """
        SELECT 
            m.id, m.name, m.category, m.lat, m.lng,
            o.title, o.start_time, o.end_time, o.reward_cents
        FROM merchants m
        JOIN offers o ON m.id = o.merchant_id
        WHERE o.active = 1
    """
    
    params = {}
    if category:
        query += " AND m.category = :category"
        params["category"] = category
    
    result = db.execute(text(query), params)
    
    offers = []
    for row in result:
        merchant_lat = float(row.lat)
        merchant_lng = float(row.lng)
        distance = haversine_m(lat, lng, merchant_lat, merchant_lng)
        
        if distance <= radius_m:
            offers.append({
                "id": f"merchant_{row.id}",
                "title": row.title or "",
                "est_reward_cents": int(row.reward_cents) if row.reward_cents else 0,
                "window_start": str(row.start_time) if row.start_time else None,
                "window_end": str(row.end_time) if row.end_time else None,
                "distance_m": round(distance, 1),
                "source": "local"
            })
    
    # Sort by distance
    offers.sort(key=lambda x: x["distance_m"])
    
    return offers

