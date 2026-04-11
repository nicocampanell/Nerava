"""
Purchase webhook normalization, merchant management, and session matching
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.geo import haversine_m
from app.utils.log import get_logger

logger = get_logger(__name__)


def normalize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a purchase webhook payload from Square or CLO/affiliate providers.
    
    Returns:
        {
            "provider": "square"|"clo",
            "provider_ref": str (unique ref from provider),
            "user_id": int (from payload or metadata),
            "merchant_ext_id": str (external merchant ID),
            "merchant_name": str,
            "amount_cents": int,
            "ts": datetime,
            "city": str (optional),
            "lat": float (optional),
            "lng": float (optional),
            "category": str (optional),
            "raw": dict (original payload)
        }
    """
    provider = None
    normalized = {
        "provider": None,
        "provider_ref": None,
        "user_id": None,
        "merchant_ext_id": None,
        "merchant_name": None,
        "amount_cents": None,
        "ts": None,
        "city": None,
        "lat": None,
        "lng": None,
        "category": None,
        "raw": payload
    }
    
    # Detect provider and normalize
    # Check for Square format (type + data.object)
    if "type" in payload and "data" in payload:
        # Square webhook format
        provider = "square"
        data_obj = payload.get("data", {}).get("object", {})
        obj = payload.get("object", {})
        
        # Square payment object (prefer data.object, fallback to object)
        payment_obj = data_obj if data_obj.get("object") == "payment" else (obj if obj.get("object") == "payment" else {})
        
        normalized["provider"] = "square"
        normalized["provider_ref"] = payment_obj.get("id") or payload.get("id") or payload.get("data", {}).get("id")
        
        # Extract amount (Square format: amount_money.amount)
        amount_val = None
        if payment_obj.get("amount_money"):
            amount_val = payment_obj.get("amount_money", {}).get("amount")
        elif payment_obj.get("amount"):
            amount_val = payment_obj.get("amount")
        
        if amount_val is not None:
            normalized["amount_cents"] = int(amount_val)
        
        # Square location/merchant info
        location = payment_obj.get("location", {})
        normalized["merchant_ext_id"] = location.get("id") or payment_obj.get("location_id")
        normalized["merchant_name"] = location.get("name") or payment_obj.get("merchant_name")
        
        # Timestamp
        ts_str = payment_obj.get("created_at") or payload.get("event_time")
        if ts_str:
            try:
                normalized["ts"] = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            except:
                normalized["ts"] = datetime.utcnow()
        else:
            normalized["ts"] = datetime.utcnow()
        
        # User ID from metadata (added during checkout in later phases)
        metadata = payment_obj.get("metadata", {})
        if "user_id" in metadata:
            try:
                normalized["user_id"] = int(metadata["user_id"])
            except:
                pass
        
        # Fallback: check top-level user_id (for dev mocks)
        if normalized["user_id"] is None and "user_id" in payload:
            try:
                normalized["user_id"] = int(payload["user_id"])
            except:
                pass
        
    elif "provider" in payload or payload.get("event_type") in ["purchase", "transaction"]:
        # CLO/Generic format
        provider = payload.get("provider", "clo")
        normalized["provider"] = provider
        normalized["provider_ref"] = payload.get("transaction_id") or payload.get("id")
        normalized["user_id"] = payload.get("user_id")
        normalized["amount_cents"] = int(payload.get("amount_cents", 0))
        normalized["merchant_ext_id"] = payload.get("merchant_ext_id") or payload.get("merchant_id")
        normalized["merchant_name"] = payload.get("merchant_name")
        normalized["city"] = payload.get("city")
        normalized["category"] = payload.get("category")
        
        # Timestamp
        ts_str = payload.get("ts") or payload.get("timestamp") or payload.get("created_at")
        if ts_str:
            if isinstance(ts_str, str):
                try:
                    normalized["ts"] = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except:
                    normalized["ts"] = datetime.utcnow()
            else:
                normalized["ts"] = ts_str
        else:
            normalized["ts"] = datetime.utcnow()
    
    # Fallback: direct payload fields (for dev mock)
    if not normalized["provider"]:
        normalized["provider"] = payload.get("provider", "square")
    if not normalized["provider_ref"]:
        # Generate a provider_ref if missing
        import hashlib
        payload_str = json.dumps(payload, sort_keys=True) if isinstance(payload, dict) else str(payload)
        normalized["provider_ref"] = payload.get("provider_ref") or hashlib.md5(payload_str.encode(), usedforsecurity=False).hexdigest()[:16]
    if normalized["user_id"] is None:
        normalized["user_id"] = payload.get("user_id")
    if normalized["amount_cents"] is None:
        # Try to get amount_cents from payload directly
        amount_val = payload.get("amount_cents") or payload.get("amount")
        if amount_val is not None:
            normalized["amount_cents"] = int(amount_val)
        else:
            normalized["amount_cents"] = 0
    if not normalized["merchant_ext_id"]:
        normalized["merchant_ext_id"] = payload.get("merchant_ext_id")
    if not normalized["merchant_name"]:
        normalized["merchant_name"] = payload.get("merchant_name")
    if not normalized["city"]:
        normalized["city"] = payload.get("city")
    if not normalized["lat"]:
        normalized["lat"] = payload.get("lat")
    if not normalized["lng"]:
        normalized["lng"] = payload.get("lng")
    
    # Ensure required fields
    if normalized["ts"] is None:
        normalized["ts"] = datetime.utcnow()
    
    return normalized


def find_or_create_merchant(
    db: Session,
    *,
    ext_id: str,
    name: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    category: Optional[str] = None,
    city: Optional[str] = None
) -> Optional[int]:
    """
    Find existing merchant by ext_id, or create new one.
    Returns merchant_id.
    """
    # Try to find by ext_id first (if column exists)
    if ext_id:
        try:
            result = db.execute(text("""
                SELECT id FROM merchants WHERE external_id = :ext_id LIMIT 1
            """), {"ext_id": ext_id}).first()
            
            if result:
                return int(result[0])
        except:
            # Column might not exist yet
            pass
    
    # Create new merchant
    try:
        # Insert with required columns (name, category, lat, lng, created_at)
        # Try to include external_id if it exists
        db.execute(text("""
            INSERT INTO merchants (name, category, lat, lng, created_at)
            VALUES (:name, :category, :lat, :lng, :created_at)
        """), {
            "name": name or f"Merchant {ext_id or 'Unknown'}",
            "category": category or "general",
            "lat": lat if lat is not None else 0.0,
            "lng": lng if lng is not None else 0.0,
            "created_at": datetime.utcnow()
        })
        
        # Get inserted ID
        merchant_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        
        # Try to update external_id if column exists and ext_id provided
        if ext_id:
            try:
                db.execute(text("""
                    UPDATE merchants SET external_id = :ext_id WHERE id = :merchant_id
                """), {"ext_id": ext_id, "merchant_id": merchant_id})
            except:
                # Column might not exist, ignore
                pass
        
        db.commit()
        
        logger.info(f"Created merchant: id={merchant_id}, ext_id={ext_id}, name={name}")
        return int(merchant_id) if merchant_id else None
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create merchant: {e}")
        # Return None if creation fails
        return None


def match_session(
    db: Session,
    *,
    user_id: int,
    merchant_id: int,
    ts: datetime,
    radius_m: int = 120,
    ttl_min: int = 30
) -> Optional[str]:
    """
    Match a purchase timestamp to the user's most recent verified session.
    
    Rules:
    - Session status = 'verified'
    - Session started_at <= ts <= started_at + TTL
    - Distance from charger to merchant <= radius_m
    
    Returns session_id if match found, None otherwise.
    """
    # Get merchant location
    merchant_result = db.execute(text("""
        SELECT lat, lng FROM merchants WHERE id = :merchant_id
    """), {"merchant_id": merchant_id}).first()
    
    if not merchant_result:
        return None
    
    merchant_lat = merchant_result[0]
    merchant_lng = merchant_result[1]
    
    if merchant_lat is None or merchant_lng is None:
        # Can't compute distance without merchant coords
        return None
    
    # Calculate window: ts must be within [started_at, started_at + TTL]
    window_start = ts - timedelta(minutes=ttl_min)
    
    # Find matching sessions
    sessions_result = db.execute(text("""
        SELECT id, lat, lng, started_at, verified_at
        FROM sessions
        WHERE user_id = :user_id
        AND status = 'verified'
        AND started_at <= :ts
        AND started_at >= :window_start
        AND lat IS NOT NULL
        AND lng IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 10
    """), {
        "user_id": str(user_id),
        "ts": ts,
        "window_start": window_start
    })
    
    # Check distance for each session
    for session_row in sessions_result:
        session_id = session_row[0]
        session_lat = float(session_row[1]) if session_row[1] else None
        session_lng = float(session_row[2]) if session_row[2] else None
        
        if session_lat is None or session_lng is None:
            continue
        
        # Calculate distance
        distance = haversine_m(
            (session_lat, session_lng),
            (merchant_lat, merchant_lng)
        )
        
        if distance <= radius_m:
            logger.info(
                f"Session match: session_id={session_id}, distance={distance:.1f}m, "
                f"merchant_id={merchant_id}"
            )
            return str(session_id)
    
    return None

