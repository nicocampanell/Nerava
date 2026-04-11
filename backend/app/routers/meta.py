import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(tags=["meta"])

@router.get("/health")
def health():
    """Basic health check endpoint"""
    return {"ok": True}

# DEPRECATED: /healthz removed from meta router
# Use root-level /healthz (liveness) and /readyz (readiness) instead.
# Root-level /healthz is defined in main_simple.py and does not perform DB checks.

@router.get("/version")
def version():
    """Get version info"""
    git_sha = os.getenv("GIT_SHA", "dev")
    build_time = os.getenv("BUILD_TIME", datetime.utcnow().isoformat())
    return {
        "git_sha": git_sha,
        "build_time": build_time
    }

@router.get("/debug")
def debug():
    """Minimal environment snapshot (safe for debugging)"""
    return {
        "python_version": os.sys.version.split()[0],
        "environment": os.getenv("ENVIRONMENT", "development"),
        "database_url": os.getenv("DATABASE_URL", "sqlite:///./nerava.db").split("//")[0] + "//***",  # Hide credentials
        "region": os.getenv("REGION", "local"),
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/debug/rewards")
def debug_rewards(
    user_id: int = Query(...),
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Debug endpoint for reward_events (dev only).
    Returns last N reward events for a user with wallet balance.
    """
    # Guard: only allow in non-production
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if app_env == "prod" or app_env == "production":
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")
    
    # Fetch last N reward events
    is_sqlite = os.getenv("DATABASE_URL", "").startswith("sqlite")
    
    if is_sqlite:
        # SQLite: use LIKE for JSON in TEXT column
        events_result = db.execute(text("""
            SELECT id, source, gross_cents, net_cents, community_cents, created_at, meta
            FROM reward_events
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"user_id": str(user_id), "limit": limit})
    else:
        # Postgres: can extract JSON fields
        events_result = db.execute(text("""
            SELECT id, source, gross_cents, net_cents, community_cents, created_at, meta
            FROM reward_events
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"user_id": str(user_id), "limit": limit})
    
    events = []
    for row in events_result:
        # Parse meta JSON
        meta_str = row[6] if len(row) > 6 else "{}"
        try:
            if isinstance(meta_str, str):
                meta = json.loads(meta_str)
            else:
                meta = meta_str
        except:
            meta = {"raw": str(meta_str)}
        
        session_id = meta.get("session_id") if isinstance(meta, dict) else None
        
        events.append({
            "id": row[0],
            "source": row[1],
            "gross_cents": row[2],
            "net_cents": row[3],
            "community_cents": row[4],
            "created_at": str(row[5]) if row[5] else None,
            "session_id": session_id
        })
    
    # Calculate wallet balance
    wallet_result = db.execute(text("""
        SELECT COALESCE(SUM(amount_cents), 0) FROM wallet_ledger
        WHERE user_id = :user_id
    """), {"user_id": user_id}).scalar()
    wallet_cents = int(wallet_result) if wallet_result else 0
    
    return {
        "user_id": user_id,
        "wallet_cents": wallet_cents,
        "events": events,
        "count": len(events)
    }


@router.get("/debug/abuse")
def debug_abuse(
    user_id: int = Query(...),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Debug endpoint for abuse/risk data (dev only).
    Returns recent verify attempts, device fingerprints, and abuse events.
    """
    # Guard: only allow in non-production
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if app_env == "prod" or app_env == "production":
        raise HTTPException(status_code=403, detail="Debug endpoints disabled in production")
    
    # Fetch recent verify attempts
    attempts_result = db.execute(text("""
        SELECT id, session_id, ip, ua, accuracy_m, outcome, created_at
        FROM verify_attempts
        WHERE user_id = :user_id
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"user_id": user_id, "limit": limit})
    
    attempts = []
    for row in attempts_result:
        attempts.append({
            "id": row[0],
            "session_id": row[1],
            "ip": row[2],
            "ua": row[3],
            "accuracy_m": float(row[4]) if row[4] else None,
            "outcome": row[5],
            "created_at": str(row[6]) if row[6] else None
        })
    
    # Fetch device fingerprints
    devices_result = db.execute(text("""
        SELECT device_hash, first_seen, last_seen, ua, last_ip
        FROM device_fingerprints
        WHERE user_id = :user_id
        ORDER BY last_seen DESC
        LIMIT 10
    """), {"user_id": user_id})
    
    devices = []
    for row in devices_result:
        devices.append({
            "device_hash": row[0],
            "first_seen": str(row[1]) if row[1] else None,
            "last_seen": str(row[2]) if row[2] else None,
            "ua": row[3],
            "last_ip": row[4]
        })
    
    # Fetch recent abuse events
    abuse_result = db.execute(text("""
        SELECT id, type, severity, details_json, created_at
        FROM abuse_events
        WHERE user_id = :user_id
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"user_id": user_id, "limit": limit})
    
    abuse_events = []
    for row in abuse_result:
        details_str = row[3] if len(row) > 3 else "{}"
        try:
            details = json.loads(details_str) if isinstance(details_str, str) else details_str
        except:
            details = {"raw": str(details_str)}
        
        abuse_events.append({
            "id": row[0],
            "type": row[1],
            "severity": row[2],
            "details": details,
            "created_at": str(row[4]) if len(row) > 4 and row[4] else None
        })
    
    # Compute current risk score
    from datetime import datetime

    from app.services.fraud import compute_risk_score
    risk_result = compute_risk_score(db, user_id=user_id, now=datetime.utcnow())
    
    return {
        "user_id": user_id,
        "risk_score": risk_result["score"],
        "risk_reasons": risk_result["reasons"],
        "verify_attempts": attempts,
        "device_fingerprints": devices,
        "abuse_events": abuse_events,
        "counts": {
            "attempts": len(attempts),
            "devices": len(devices),
            "abuse_events": len(abuse_events)
        }
    }

