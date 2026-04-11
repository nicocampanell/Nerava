"""
Smartcar EV integration router
Handles OAuth flow and telemetry endpoints

Configuration:
- Set SMARTCAR_CLIENT_ID, SMARTCAR_CLIENT_SECRET, and SMARTCAR_REDIRECT_URI environment variables
- SMARTCAR_MODE defaults to "sandbox" for local dev (set to "live" for production)
- Register the callback URL in Smartcar dashboard: {your-backend-url}/oauth/smartcar/callback
- For local dev, use a tunnel (Cloudflare Tunnel, ngrok, etc.) to expose localhost:8001

Example .env:
  SMARTCAR_CLIENT_ID=your_client_id
  SMARTCAR_CLIENT_SECRET=your_client_secret
  SMARTCAR_MODE=sandbox
  SMARTCAR_REDIRECT_URI=https://your-tunnel-domain/oauth/smartcar/callback
  FRONTEND_URL=http://localhost:8001/app
  NERAVA_DEV_ALLOW_ANON_USER=true

Local Testing Flow:
1. Start local backend with migrations:
   - Delete nerava.db if schema is stale (or let migrations upgrade it)
   - Run: python3 -m uvicorn app.main_simple:app --host 0.0.0.0 --port 8001 --reload
   - Migrations will run on startup and create/update the DB schema

2. Start local UI at http://localhost:8001/app

3. Set env vars in .env:
   - NERAVA_DEV_ALLOW_ANON_USER=true (allows user_id=1 fallback)
   - FRONTEND_URL=http://localhost:8001/app
   - SMARTCAR_REDIRECT_URI=https://<your-tunnel-host>/oauth/smartcar/callback
   - SMARTCAR_CLIENT_ID, SMARTCAR_CLIENT_SECRET, SMARTCAR_MODE=sandbox

4. Start a tunnel (e.g., cloudflared tunnel --url http://localhost:8001) and update
   SMARTCAR_REDIRECT_URI with the tunnel URL

5. Register the callback URL in Smartcar dashboard

6. In the UI, go to the EV connect screen, click "Connect EV", complete Smartcar/Tesla login

7. Verify:
   - /oauth/smartcar/callback returns 302 redirect back to the app (no 500)
   - A row exists in vehicle_accounts and vehicle_tokens for user_id=1
   - /v1/ev/me/telemetry/latest returns 200 with telemetry data or clean 404 if unavailable
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies.feature_flags import require_smartcar
from app.dependencies_domain import get_current_user
from app.models import User
from app.models_vehicle import VehicleAccount, VehicleTelemetry, VehicleToken
from app.services.ev_telemetry import poll_vehicle_telemetry_for_account
from app.services.smartcar_service import (
    SmartcarTokenExpiredError,
    exchange_code_for_tokens,
    list_vehicles,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ev"])


# Response models
class ConnectResponse(BaseModel):
    url: str


class TelemetryResponse(BaseModel):
    recorded_at: datetime
    soc_pct: Optional[float]
    charging_state: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]


# Helper: Create signed state token using SMARTCAR_STATE_SECRET
def create_state_token(user_public_id: str) -> str:
    """Create a cryptographically signed state token for OAuth flow using user public_id"""
    from ..core.security import create_smartcar_state_jwt
    return create_smartcar_state_jwt(user_public_id)


# Helper: Verify and decode state token
def verify_state_token(token: str) -> str:
    """Verify state token and return user_public_id"""
    from ..core.security import verify_smartcar_state_jwt
    try:
        payload = verify_smartcar_state_jwt(token)
        user_public_id = payload.get("user_public_id")
        if not user_public_id:
            raise ValueError("Missing user_public_id in token")
        return user_public_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="State token expired"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid state token: {str(e)}"
        )


@router.get("/v1/ev/connect", response_model=ConnectResponse, dependencies=[Depends(require_smartcar)])
async def connect_vehicle(
    current_user: User = Depends(get_current_user),
):
    """
    Generate Smartcar Connect URL for the authenticated user
    
    Returns a URL that the frontend should redirect the user to.
    After OAuth, Smartcar will redirect to /oauth/smartcar/callback.
    """
    # Check feature flag first
    if not settings.SMARTCAR_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smartcar integration is disabled. Set SMARTCAR_ENABLED=true to enable."
        )
    
    if not settings.smartcar_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smartcar integration is not configured. Set SMARTCAR_CLIENT_ID, SMARTCAR_CLIENT_SECRET, and SMARTCAR_REDIRECT_URI."
        )
    
    # Create signed state token using user public_id
    import uuid
    nonce = str(uuid.uuid4())
    state = create_state_token(current_user.public_id)
    
    # Log for debugging
    logger.info(f"smartcar_connect_started user_public_id={current_user.public_id} nonce={nonce}")
    
    # Build Smartcar Connect URL
    base_url = f"{settings.SMARTCAR_CONNECT_URL}/oauth/authorize"
    
    # Default scope - can be extended later
    scope = "read_vehicle_info read_location read_charge"
    
    params = {
        "response_type": "code",
        "client_id": settings.SMARTCAR_CLIENT_ID,
        "redirect_uri": settings.SMARTCAR_REDIRECT_URI,
        "scope": scope,
        "mode": settings.SMARTCAR_MODE,
        "state": state,
    }
    
    connect_url = f"{base_url}?{urlencode(params)}"
    
    # Log mode and redirect_uri (but NOT client_secret)
    logger.info(
        f"Generated Smartcar Connect URL for user {current_user.id} "
        f"(mode={settings.SMARTCAR_MODE}, redirect_uri={settings.SMARTCAR_REDIRECT_URI})"
    )
    
    return ConnectResponse(url=connect_url)


@router.get("/oauth/smartcar/callback", dependencies=[Depends(require_smartcar)])
async def smartcar_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Smartcar OAuth callback endpoint
    
    This is called by Smartcar after the user authorizes.
    We exchange the code for tokens, fetch vehicle info, and store everything.
    """
    # Check feature flag first
    if not settings.SMARTCAR_ENABLED:
        logger.warning("Smartcar callback received but SMARTCAR_ENABLED=false")
        frontend_url = f"{settings.frontend_url.rstrip('/')}/#profile?error=smartcar_disabled"
        return RedirectResponse(url=frontend_url, status_code=302)
    
    # Handle OAuth errors
    if error:
        logger.error(f"Smartcar OAuth error: {error}")
        # Redirect to frontend with error
        frontend_url = f"{settings.frontend_url.rstrip('/')}/#profile?error={error}"
        return RedirectResponse(url=frontend_url, status_code=302)
    
    # Validate required parameters
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code"
        )
    
    if not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing state parameter"
        )
    
    # Verify state token and get user_public_id (stateless - no session required)
    try:
        user_public_id = verify_state_token(state)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"State token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state token"
        )
    
    # Fetch user by public_id (stateless callback - no session)
    user = db.query(User).filter(User.public_id == user_public_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user_id = user.id  # Use integer id for vehicle_account linkage
    
    try:
        # Exchange code for tokens
        token_data = await exchange_code_for_tokens(code)
        
        access_token = token_data["access_token"]
        refresh_token = token_data["refresh_token"]
        expires_in = token_data.get("expires_in", 3600)
        scope = token_data.get("scope", "")
        
        # List vehicles (get first one for now)
        # Smartcar API returns: {"vehicles": ["vehicle_id_1", "vehicle_id_2", ...]}
        # The vehicles array contains vehicle ID strings, not objects
        vehicles_data = await list_vehicles(access_token)
        vehicles = vehicles_data.get("vehicles", [])
        
        logger.info(f"[Smartcar] vehicles_resp keys: {list(vehicles_data.keys())}")
        
        if not vehicles:
            logger.warning(f"No vehicles found for user {user_id}")
            frontend_url = f"{settings.frontend_url}/#profile?error=no_vehicles"
            return RedirectResponse(url=frontend_url)
        
        # Use first vehicle (can extend to multi-vehicle later)
        # vehicles[0] is already the vehicle ID string, not an object
        vehicle_id = vehicles[0] if isinstance(vehicles[0], str) else vehicles[0].get("id", vehicles[0])
        
        logger.info(f"[Smartcar] Selected vehicle_id={vehicle_id} for user_id={user_id}")
        
        # Upsert VehicleAccount
        existing_account = (
            db.query(VehicleAccount)
            .filter(
                VehicleAccount.user_id == user_id,
                VehicleAccount.provider == "smartcar",
                VehicleAccount.provider_vehicle_id == vehicle_id
            )
            .first()
        )
        
        if existing_account:
            account = existing_account
            account.is_active = True
            account.updated_at = datetime.utcnow()
        else:
            account = VehicleAccount(
                id=str(uuid.uuid4()),
                user_id=user_id,
                provider="smartcar",
                provider_vehicle_id=vehicle_id,
                display_name=None,  # Could fetch from vehicle info endpoint
                is_active=True,
            )
            db.add(account)
        
        db.flush()  # Get account.id
        
        # Encrypt tokens before storing (P0 security fix)
        from app.services.token_encryption import encrypt_token
        encrypted_access_token = encrypt_token(access_token)
        encrypted_refresh_token = encrypt_token(refresh_token)
        
        # Create token record with encrypted tokens
        token_record = VehicleToken(
            id=str(uuid.uuid4()),
            vehicle_account_id=account.id,
            access_token=encrypted_access_token,
            refresh_token=encrypted_refresh_token,
            encryption_version=1,  # Mark as encrypted
            expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
            scope=scope,
        )
        
        db.add(token_record)
        db.commit()
        
        logger.info(f"smartcar_linked user_public_id={user.public_id} vehicle_count={len(vehicles)}")
        
        # Redirect to frontend account page with success indicator
        frontend_url = f"{settings.frontend_url.rstrip('/')}/#profile?vehicle=connected"
        
        return RedirectResponse(url=frontend_url, status_code=302)
        
    except Exception as e:
        logger.error(f"Error in Smartcar callback: {e}", exc_info=True)
        db.rollback()
        
        # Redirect to frontend with error
        frontend_url = f"{settings.frontend_url.rstrip('/')}/#profile?error=connection_failed"
        
        return RedirectResponse(url=frontend_url)


class EvStatusResponse(BaseModel):
    connected: bool
    vehicle_label: Optional[str] = None
    last_sync_at: Optional[str] = None
    status: str  # "connected", "needs_attention", "not_connected"


@router.get("/v1/ev/status", response_model=EvStatusResponse, dependencies=[Depends(require_smartcar)])
async def get_ev_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get vehicle connection status for the current user
    
    Returns connection status, vehicle label, and last sync timestamp.
    """
    if not settings.SMARTCAR_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smartcar integration is disabled. Set SMARTCAR_ENABLED=true to enable."
        )
    # Find user's active vehicle account
    account = (
        db.query(VehicleAccount)
        .filter(
            VehicleAccount.user_id == current_user.id,
            VehicleAccount.provider == "smartcar",
            VehicleAccount.is_active == True
        )
        .first()
    )
    
    if not account:
        return EvStatusResponse(
            connected=False,
            vehicle_label=None,
            last_sync_at=None,
            status="not_connected"
        )
    
    # Get latest telemetry for last_sync_at
    from sqlalchemy import desc
    
    latest_telemetry = (
        db.query(VehicleTelemetry)
        .filter(VehicleTelemetry.vehicle_account_id == account.id)
        .order_by(desc(VehicleTelemetry.recorded_at))
        .first()
    )
    
    last_sync_at = None
    if latest_telemetry and latest_telemetry.recorded_at:
        last_sync_at = latest_telemetry.recorded_at.isoformat()
    
    # Check if token exists and is valid
    latest_token = (
        db.query(VehicleToken)
        .filter(VehicleToken.vehicle_account_id == account.id)
        .order_by(desc(VehicleToken.created_at))
        .first()
    )
    
    if not latest_token:
        return EvStatusResponse(
            connected=False,
            vehicle_label=account.display_name,
            last_sync_at=last_sync_at,
            status="needs_attention"
        )
    
    # Check if token is expired (with 5 minute buffer)
    token_expired = latest_token.expires_at < datetime.utcnow() + timedelta(minutes=5)
    
    if token_expired:
        return EvStatusResponse(
            connected=True,
            vehicle_label=account.display_name,
            last_sync_at=last_sync_at,
            status="needs_attention"
        )
    
    return EvStatusResponse(
        connected=True,
        vehicle_label=account.display_name,
        last_sync_at=last_sync_at,
        status="connected"
    )


@router.post("/v1/ev/disconnect", dependencies=[Depends(require_smartcar)])
async def disconnect_vehicle(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Disconnect vehicle by deactivating vehicle account and revoking tokens
    """
    if not settings.SMARTCAR_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smartcar integration is disabled. Set SMARTCAR_ENABLED=true to enable."
        )
    
    # Find all active vehicle accounts for user
    accounts = (
        db.query(VehicleAccount)
        .filter(
            VehicleAccount.user_id == current_user.id,
            VehicleAccount.provider == "smartcar",
            VehicleAccount.is_active == True
        )
        .all()
    )
    
    if not accounts:
        return {"ok": True, "message": "No connected vehicles to disconnect"}
    
    # Deactivate all accounts
    for account in accounts:
        account.is_active = False
        account.updated_at = datetime.utcnow()
    
    db.commit()
    
    logger.info(f"Vehicle disconnected for user {current_user.id}, deactivated {len(accounts)} account(s)")
    
    return {"ok": True}


@router.get("/v1/ev/me/telemetry/latest", response_model=TelemetryResponse)
async def get_latest_telemetry(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get latest telemetry for the current user's connected vehicle
    
    This endpoint polls Smartcar for fresh data and returns it.
    This is the production test endpoint.
    """
    if not settings.SMARTCAR_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Smartcar integration is disabled. Set SMARTCAR_ENABLED=true to enable."
        )

    # Find user's active vehicle account
    account = (
        db.query(VehicleAccount)
        .filter(
            VehicleAccount.user_id == current_user.id,
            VehicleAccount.provider == "smartcar",
            VehicleAccount.is_active == True
        )
        .first()
    )
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No connected vehicle found"
        )
    
    try:
        # Poll fresh telemetry
        telemetry = await poll_vehicle_telemetry_for_account(db, account)
        
        return TelemetryResponse(
            recorded_at=telemetry.recorded_at,
            soc_pct=telemetry.soc_pct,
            charging_state=telemetry.charging_state,
            latitude=telemetry.latitude,
            longitude=telemetry.longitude,
        )
        
    except SmartcarTokenExpiredError:
        # Refresh token expired - user needs to re-authenticate
        logger.warning(f"Smartcar token expired for user {current_user.id}, re-auth required")
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail={
                "error": "VEHICLE_REAUTH_REQUIRED",
                "message": "Your vehicle connection has expired. Please reconnect via Profile → Connect EV.",
                "action": "reauth"
            }
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            # Smartcar returned 400 - likely token/credential issue
            logger.error(f"Smartcar token error for user {current_user.id}: {e.response.status_code}")
            raise HTTPException(
                status_code=status.HTTP_424_FAILED_DEPENDENCY,
                detail={
                    "error": "SMARTCAR_TOKEN_EXCHANGE_FAILED",
                    "status": 400,
                    "hint": "Check SMARTCAR_CLIENT_ID/SECRET and redirect URI configuration"
                }
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Smartcar API error: {e.response.status_code}"
        )
    except ValueError as e:
        # No token found - user not connected
        logger.error(f"No token found for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No connected vehicle found"
        )
    except Exception as e:
        logger.error(f"Error polling telemetry for user {current_user.id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch vehicle telemetry: {str(e)}"
        )

