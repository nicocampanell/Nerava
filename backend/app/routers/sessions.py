"""
Public verify flow endpoints: verify page and locate endpoint
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.security.tokens import decode_verify_token
from app.services.rewards import award_verify_bonus
from app.utils.log import get_logger

router = APIRouter(tags=["sessions"])
logger = get_logger(__name__)


class LocateRequest(BaseModel):
    lat: float
    lng: float
    accuracy: float
    ts: str
    ua: str


@router.get("/verify/{token}", response_class=HTMLResponse)
async def verify_page(token: str):
    """
    Public verify page that requests GPS and submits location.
    """
    base_url = settings.public_base_url.rstrip('/')
    
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nerava — Verify</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        .container {{
            background: white;
            border-radius: 16px;
            padding: 40px;
            max-width: 500px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{
            color: #333;
            margin-bottom: 10px;
            font-size: 28px;
        }}
        .subtitle {{
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }}
        .status {{
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            text-align: center;
            font-weight: 600;
        }}
        .status.pending {{
            background: #fef3c7;
            color: #92400e;
        }}
        .status.success {{
            background: #d1fae5;
            color: #065f46;
        }}
        .status.error {{
            background: #fee2e2;
            color: #991b1b;
        }}
        .perks {{
            margin-top: 30px;
            padding-top: 30px;
            border-top: 1px solid #e5e7eb;
        }}
        .perks h3 {{
            color: #333;
            margin-bottom: 15px;
            font-size: 18px;
        }}
        .perk-item {{
            padding: 12px;
            background: #f9fafb;
            border-radius: 8px;
            margin-bottom: 10px;
        }}
        .perk-item strong {{
            color: #333;
            display: block;
            margin-bottom: 4px;
        }}
        .perk-item span {{
            color: #666;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Nerava — Verify</h1>
        <p class="subtitle">Please allow location access to complete verification</p>
        
        <div id="status" class="status pending">Requesting location...</div>
        
        <div id="perks" class="perks" style="display: none;">
            <h3>Nearby Perks</h3>
            <div id="perks-list"></div>
        </div>
    </div>
    
    <script>
        const token = '{token}';
        const apiBase = '{base_url}';
        
        function updateStatus(message, type) {{
            const statusEl = document.getElementById('status');
            statusEl.textContent = message;
            statusEl.className = 'status ' + type;
        }}
        
        function showPerks(perks) {{
            const perksEl = document.getElementById('perks');
            const listEl = document.getElementById('perks-list');
            
            if (!perks || perks.length === 0) {{
                listEl.innerHTML = '<div class="perk-item"><span>No perks available nearby</span></div>';
            }} else {{
                listEl.innerHTML = perks.map(p => `
                    <div class="perk-item">
                        <strong>${{p.name || 'Unknown'}}</strong>
                        <span>${{p.category || ''}} ${{p.distance_m ? `(${{Math.round(p.distance_m)}}m)` : ''}}</span>
                        ${{p.has_offer && p.offer ? `<span style="color: #059669; font-weight: 600;">✓ ${{p.offer.title}} (${{p.offer.est_reward_cents / 100}} reward)</span>` : ''}}
                    </div>
                `).join('');
            }}
            
            perksEl.style.display = 'block';
        }}
        
        // Request GPS
        if (navigator.geolocation) {{
            navigator.geolocation.getCurrentPosition(
                async (position) => {{
                    const lat = position.coords.latitude;
                    const lng = position.coords.longitude;
                    const accuracy = position.coords.accuracy;
                    const ts = new Date().toISOString();
                    const ua = navigator.userAgent;
                    
                    try {{
                        // POST to locate endpoint
                        const locateRes = await fetch(`${{apiBase}}/v1/sessions/locate`, {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json',
                                'Authorization': `Bearer ${{token}}`
                            }},
                            body: JSON.stringify({{ lat, lng, accuracy, ts, ua }})
                        }});
                        
                        const locateData = await locateRes.json();
                        
                        if (locateRes.ok && locateData.verified) {{
                            updateStatus('Verified ✅', 'success');
                            
                            // Fetch nearby perks
                            try {{
                                const perksRes = await fetch(`${{apiBase}}/v1/gpt/find_merchants?lat=${{lat}}&lng=${{lng}}&radius_m=800`);
                                const perks = await perksRes.json();
                                showPerks(perks);
                            }} catch (e) {{
                                console.warn('Failed to load perks:', e);
                                showPerks([]);
                            }}
                        }} else {{
                            updateStatus(`Verification failed: ${{locateData.reason || locateData.detail || 'Unknown error'}}`, 'error');
                        }}
                    }} catch (e) {{
                        updateStatus(`Error: ${{e.message}}`, 'error');
                        console.error('Locate request failed:', e);
                    }}
                }},
                (error) => {{
                    updateStatus(`Location access denied: ${{error.message}}`, 'error');
                }},
                {{ enableHighAccuracy: true, timeout: 10000 }}
            );
        }} else {{
            updateStatus('Geolocation not supported', 'error');
        }}
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.post("/v1/sessions/locate")
async def locate_session(
    request: LocateRequest,
    authorization: Optional[str] = Header(None),
    req: Request = None,
    db: Session = Depends(get_db)
):
    """
    Verify session location. One-time use token.
    
    Rate limit: 30/min per IP (handled by middleware if configured)
    """
    from app.services.fraud import (
        compute_risk_score,
        emit_abuse_event,
        hash_device,
        record_verify_attempt,
        touch_device,
    )
    
    # Get client IP (from FastAPI Request)
    client_ip = None
    if req:
        client_ip = req.client.host if req.client else None
        # Check for forwarded headers
        forwarded = req.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
    
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = authorization.replace("Bearer ", "").strip()
    
    # Decode token
    try:
        payload = decode_verify_token(token)
        user_id = int(payload.get("sub"))
        session_id = payload.get("sid")
        jti = payload.get("jti")
    except HTTPException:
        raise
    except Exception as e:
        record_verify_attempt(
            db,
            user_id=user_id if 'user_id' in locals() else 0,
            session_id=session_id if 'session_id' in locals() else None,
            ip=client_ip,
            ua=request.ua,
            accuracy_m=None,
            outcome="token_decode_failed"
        )
        raise HTTPException(status_code=401, detail=f"Token decode failed: {str(e)}")
    
    # Build device hash and touch device
    device_hash = hash_device(client_ip or "", request.ua or "")
    touch_device(db, user_id=user_id, device_hash=device_hash, ua=request.ua, ip=client_ip)
    
    # Validate accuracy (already enforces MIN_ALLOWED_ACCURACY_M)
    accuracy_check_passed = request.accuracy <= settings.min_allowed_accuracy_m
    if not accuracy_check_passed:
        record_verify_attempt(
            db,
            user_id=user_id,
            session_id=session_id,
            ip=client_ip,
            ua=request.ua,
            accuracy_m=request.accuracy,
            outcome="accuracy_reject"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Location accuracy too low: {request.accuracy}m (max {settings.min_allowed_accuracy_m}m)"
        )
    
    # Check session exists and is valid
    result = db.execute(text("""
        SELECT id, user_id, status, expires_at, verified_at
        FROM sessions
        WHERE id = :session_id
    """), {"session_id": session_id})
    session_row = result.first()
    
    if not session_row:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Access row by index (SQLAlchemy returns Row tuples)
    session_status = session_row[2]
    session_expires_at = session_row[3]
    session_verified_at = session_row[4]
    
    # Check if already verified (one-time use)
    if session_verified_at:
        record_verify_attempt(
            db,
            user_id=user_id,
            session_id=session_id,
            ip=client_ip,
            ua=request.ua,
            accuracy_m=None,
            outcome="used"
        )
        return {
            "verified": False,
            "reason": "used",
            "session_id": session_id,
            "message": "Token already used"
        }
    
    # Validate session state
    now = datetime.utcnow()
    if session_status != 'started':
        raise HTTPException(
            status_code=400,
            detail=f"Session status is '{session_status}', expected 'started'"
        )
    
    if session_expires_at:
        # Handle datetime comparison (SQLite returns strings, Postgres returns datetime)
        expires_dt = session_expires_at
        if isinstance(expires_dt, str):
            try:
                # Try ISO format first
                if 'T' in expires_dt:
                    expires_dt = datetime.fromisoformat(expires_dt.replace('Z', '+00:00')[:19])
                else:
                    # Fallback to SQLite format
                    expires_dt = datetime.strptime(expires_dt[:19], '%Y-%m-%d %H:%M:%S')
            except Exception:
                # If all parsing fails, allow it (defensive)
                pass
        if isinstance(expires_dt, datetime) and expires_dt < now:
            record_verify_attempt(
                db,
                user_id=user_id,
                session_id=session_id,
                ip=client_ip,
                ua=request.ua,
                accuracy_m=None,
                outcome="expired"
            )
            raise HTTPException(status_code=400, detail="Session has expired")
    
    # Update session with location and verify
    try:
        db.execute(text("""
            UPDATE sessions
            SET lat = :lat,
                lng = :lng,
                accuracy_m = :accuracy_m,
                verified_at = :verified_at,
                status = 'verified'
            WHERE id = :session_id AND status = 'started'
        """), {
            "session_id": session_id,
            "lat": request.lat,
            "lng": request.lng,
            "accuracy_m": request.accuracy,
            "verified_at": now
        })
        db.commit()
    except Exception as e:
        db.rollback()
        record_verify_attempt(
            db,
            user_id=user_id,
            session_id=session_id,
            ip=client_ip,
            ua=request.ua,
            accuracy_m=request.accuracy,
            outcome="error"
        )
        raise HTTPException(status_code=500, detail=f"Failed to update session: {str(e)}")
    
    # Record successful verify attempt
    record_verify_attempt(
        db,
        user_id=user_id,
        session_id=session_id,
        ip=client_ip,
        ua=request.ua,
        accuracy_m=request.accuracy,
        outcome="ok"
    )
    
    # Log verification (without exact coordinates for privacy)
    logger.info(f"Session verified: session_id={session_id}, user_id={user_id}, accuracy={request.accuracy}m")
    
    # Recompute risk score after verification
    risk_result = compute_risk_score(db, user_id=user_id, now=now)
    
    # Award verify bonus reward (atomic, idempotent) - but check risk score first
    reward_result = None
    should_block_reward = risk_result["score"] >= settings.block_score_threshold
    
    if should_block_reward:
        emit_abuse_event(
            db,
            user_id=user_id,
            event_type="reward_blocked",
            severity=risk_result["score"],
            details={
                "session_id": session_id,
                "reasons": risk_result["reasons"]
            }
        )
        reward_result = {
            "awarded": False,
            "user_delta": 0,
            "pool_delta": 0,
            "reason": "risk_block"
        }
    else:
        try:
            reward_result = award_verify_bonus(
                db=db,
                user_id=user_id,
                session_id=session_id,
                amount=settings.verify_reward_cents,
                now=now
            )
        except Exception as e:
            logger.error(f"Reward award failed: session_id={session_id}, error={str(e)}")
            # Continue - verification succeeded but reward failed
            reward_result = {
                "awarded": False,
                "user_delta": 0,
                "pool_delta": 0,
                "reason": "internal_error"
            }
    
    # Build response
    response = {
        "verified": True,
        "session_id": session_id,
        "message": "ok",
        "rewarded": reward_result["awarded"] if reward_result else False
    }
    
    if reward_result and reward_result["awarded"]:
        from app.services.nova import cents_to_nova
        response.update({
            "reward_cents": settings.verify_reward_cents,
            "nova_awarded": cents_to_nova(settings.verify_reward_cents),
            "wallet_delta_cents": reward_result["user_delta"],
            "wallet_delta_nova": cents_to_nova(reward_result["user_delta"]),
            "pool_delta_cents": reward_result["pool_delta"]
        })
    elif reward_result and not reward_result["awarded"]:
        response["reason"] = reward_result.get("reason", "unknown")
    
    return response

