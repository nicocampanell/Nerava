"""
Tesla OAuth and EV Verification Router.

Handles Tesla account connection, Tesla-based login, and charging verification for EV rewards.
"""
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token
from app.core.token_encryption import encrypt_token
from app.db import get_db
from app.dependencies.domain import get_current_user
from app.models import User, UserPreferences
from app.models.domain import DomainMerchant
from app.models.tesla_connection import EVVerificationCode, TeslaConnection, TeslaOAuthState
from app.models.while_you_charge import Merchant
from app.services.geo import haversine_m
from app.services.refresh_token_service import RefreshTokenService
from app.services.tesla_auth_service import fetch_tesla_user_profile, verify_tesla_id_token
from app.services.tesla_oauth import (
    generate_ev_code,
    get_tesla_oauth_service,
    get_valid_access_token,
)

PROXIMITY_THRESHOLD_METERS = 500

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/tesla", tags=["Tesla"])


# ==================== Request/Response Models ====================

class TeslaConnectionStatus(BaseModel):
    connected: bool
    vehicle_name: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_year: Optional[int] = None
    exterior_color: Optional[str] = None
    battery_level: Optional[int] = None
    vin: Optional[str] = None


class TeslaConnectResponse(BaseModel):
    authorization_url: str
    state: str


class TeslaLoginRequest(BaseModel):
    code: str
    state: str


class VehicleInfo(BaseModel):
    id: str
    vin: Optional[str] = None
    display_name: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    color: Optional[str] = None


class TeslaLoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict
    vehicles: List[VehicleInfo] = []


class SelectVehicleRequest(BaseModel):
    vehicle_id: str


class VerifyChargingRequest(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    merchant_place_id: Optional[str] = None
    merchant_name: Optional[str] = None
    charger_id: Optional[str] = None


class VerifyChargingResponse(BaseModel):
    is_charging: bool
    battery_level: Optional[int] = None
    charge_rate_kw: Optional[int] = None
    ev_code: Optional[str] = None
    message: str


class EVCodeResponse(BaseModel):
    code: str
    merchant_name: Optional[str] = None
    expires_at: datetime
    status: str


# ==================== Tesla Login Endpoints ====================

@router.get("/login-url")
async def get_tesla_login_url(db: Session = Depends(get_db)):
    """
    Start Tesla OAuth login flow (no auth required).

    Returns authorization URL to redirect the user to Tesla sign-in.
    """
    oauth_service = get_tesla_oauth_service()

    # Clean up expired states periodically
    TeslaOAuthState.cleanup_expired(db)

    state = secrets.token_urlsafe(32)
    TeslaOAuthState.store(db, state, {"purpose": "login"})

    auth_url = oauth_service.get_authorization_url(state)

    return {"authorization_url": auth_url, "state": state}


@router.post("/login", response_model=TeslaLoginResponse)
async def tesla_login(
    payload: TeslaLoginRequest,
    db: Session = Depends(get_db),
):
    """
    Complete Tesla login: exchange code, verify id_token, find-or-create user.

    No auth required — this IS the login endpoint.
    """
    # Validate state (DB-backed, TTL enforced in TeslaOAuthState.pop)
    state_data = TeslaOAuthState.pop(db, payload.state)
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    if state_data.get("purpose") != "login":
        raise HTTPException(status_code=400, detail="State not valid for login flow")

    oauth_service = get_tesla_oauth_service()

    try:
        # Exchange code for tokens (uses default /callback redirect_uri)
        token_response = await oauth_service.exchange_code_for_tokens(
            payload.code
        )

        tesla_access_token_raw = token_response["access_token"]
        tesla_refresh_token_raw = token_response["refresh_token"]
        expires_in = token_response.get("expires_in", 3600)

        # Verify id_token to get Tesla sub
        id_token_str = token_response.get("id_token")
        if not id_token_str:
            raise HTTPException(status_code=400, detail="Tesla did not return an id_token")

        tesla_claims = verify_tesla_id_token(id_token_str)
        tesla_sub = tesla_claims["sub"]

        # Best-effort fetch email/name from userinfo
        profile = await fetch_tesla_user_profile(tesla_access_token_raw)

        # Fetch vehicles
        vehicles_raw = []
        try:
            vehicles_raw = await oauth_service.get_vehicles(tesla_access_token_raw)
        except Exception as ve:
            logger.warning(f"Could not fetch Tesla vehicles during login: {ve}")

        # Find or create user
        user = db.query(User).filter(
            User.auth_provider == "tesla",
            User.provider_sub == tesla_sub,
        ).first()

        if not user:
            user = User(
                public_id=str(uuid.uuid4()),
                email=profile.get("email"),
                display_name=profile.get("name"),
                auth_provider="tesla",
                provider_sub=tesla_sub,
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPreferences(user_id=user.id))
        else:
            # Update email if we got one and user doesn't have one
            if profile.get("email") and not user.email:
                user.email = profile["email"]
            if profile.get("name") and not user.display_name:
                user.display_name = profile["name"]

        # Store / update TeslaConnection
        existing_conn = db.query(TeslaConnection).filter(
            TeslaConnection.user_id == user.id,
            TeslaConnection.is_active == True,
        ).first()

        # Encrypt tokens before storage
        encrypted_access = encrypt_token(tesla_access_token_raw)
        encrypted_refresh = encrypt_token(tesla_refresh_token_raw)

        if existing_conn:
            existing_conn.access_token = encrypted_access
            existing_conn.refresh_token = encrypted_refresh
            existing_conn.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            existing_conn.tesla_user_id = tesla_sub
            existing_conn.updated_at = datetime.utcnow()
        else:
            conn = TeslaConnection(
                user_id=user.id,
                access_token=encrypted_access,
                refresh_token=encrypted_refresh,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                tesla_user_id=tesla_sub,
            )
            db.add(conn)

        # Issue Nerava JWT + refresh token
        nerava_access_token = create_access_token(user.public_id, auth_provider="tesla")
        refresh_token_plain, _rt_model = RefreshTokenService.create_refresh_token(db, user)

        db.commit()
        db.refresh(user)

        # Build vehicle list for response
        vehicles_out = [
            VehicleInfo(
                id=str(v.get("id")),
                vin=v.get("vin"),
                display_name=v.get("display_name"),
                model=v.get("vehicle_config", {}).get("car_type"),
                year=v.get("year") or v.get("vehicle_config", {}).get("model_year"),
                color=v.get("color") or v.get("vehicle_config", {}).get("exterior_color"),
            )
            for v in vehicles_raw
        ]

        logger.info(f"Tesla login successful for user {user.id} (sub={tesla_sub})")

        return TeslaLoginResponse(
            access_token=nerava_access_token,
            refresh_token=refresh_token_plain,
            token_type="bearer",
            user={
                "public_id": user.public_id,
                "auth_provider": user.auth_provider,
                "email": user.email,
                "display_name": user.display_name,
                "phone": user.phone if hasattr(user, "phone") else None,
            },
            vehicles=vehicles_out,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tesla login failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Tesla login failed. Please try again.",
        )


@router.post("/select-vehicle")
async def select_vehicle(
    payload: SelectVehicleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Select a vehicle after Tesla login. Updates the TeslaConnection with vehicle details.
    """
    connection = db.query(TeslaConnection).filter(
        TeslaConnection.user_id == current_user.id,
        TeslaConnection.is_active == True,
    ).first()

    if not connection:
        raise HTTPException(status_code=400, detail="No active Tesla connection")

    oauth_service = get_tesla_oauth_service()
    access_token = await get_valid_access_token(db, connection, oauth_service)
    if not access_token:
        raise HTTPException(status_code=401, detail="Tesla session expired. Please log in again.")

    try:
        vehicles = await oauth_service.get_vehicles(access_token)
    except Exception as e:
        logger.error(f"Failed to fetch vehicles for select: {e}")
        raise HTTPException(status_code=502, detail="Could not reach Tesla API")

    selected = None
    for v in vehicles:
        if str(v.get("id")) == payload.vehicle_id:
            selected = v
            break

    if not selected:
        raise HTTPException(status_code=404, detail="Vehicle not found on your Tesla account")

    connection.vehicle_id = str(selected.get("id"))
    connection.vin = selected.get("vin")
    connection.vehicle_name = selected.get("display_name")
    connection.vehicle_model = selected.get("vehicle_config", {}).get("car_type", "Tesla")
    connection.updated_at = datetime.utcnow()

    # Decode VIN for richer vehicle display (e.g. "2024 Model Y Long Range")
    decoded_vehicle_model = connection.vehicle_model
    if connection.vin:
        from app.services.vin_decoder import decode_tesla_vin
        decoded = decode_tesla_vin(connection.vin)
        if decoded:
            decoded_vehicle_model = decoded["display"]
            connection.vehicle_model = decoded_vehicle_model
            # Also update user's vehicle_model for account display
            current_user.vehicle_model = decoded_vehicle_model

    db.commit()

    logger.info(f"User {current_user.id} selected vehicle {connection.vin} ({decoded_vehicle_model})")

    return {
        "success": True,
        "vehicle": {
            "id": connection.vehicle_id,
            "vin": connection.vin,
            "display_name": connection.vehicle_name,
            "model": decoded_vehicle_model,
        },
    }


# ==================== Existing Endpoints ====================

@router.get("/status", response_model=TeslaConnectionStatus)
async def get_tesla_connection_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if user has connected their Tesla account, with live battery + model."""
    connection = db.query(TeslaConnection).filter(
        TeslaConnection.user_id == current_user.id,
        TeslaConnection.is_active == True
    ).first()

    if not connection:
        return TeslaConnectionStatus(connected=False)

    vehicle_model = connection.vehicle_model
    vehicle_year = None
    exterior_color = None
    battery_level = None

    # Try VIN decode first for year + model
    if connection.vin:
        from app.services.vin_decoder import decode_tesla_vin
        decoded = decode_tesla_vin(connection.vin)
        if decoded:
            vehicle_year = decoded["year"]
            if not vehicle_model or vehicle_model in ("Tesla", "model3", "modely", "models", "modelx"):
                vehicle_model = decoded["display"]  # e.g. "2024 Model 3 Long Range"

    # Fetch live vehicle data from Tesla API for battery + color + backfill
    if connection.vehicle_id:
        try:
            oauth_service = get_tesla_oauth_service()
            access_token = await get_valid_access_token(db, connection, oauth_service)
            vehicle_data = await oauth_service.get_vehicle_data(access_token, connection.vehicle_id)

            charge_state = vehicle_data.get("charge_state", {})
            battery_level = charge_state.get("battery_level")

            vehicle_config = vehicle_data.get("vehicle_config", {})
            exterior_color = vehicle_config.get("exterior_color")
            logger.info(
                "Tesla vehicle_config for user %s: car_type=%s model_year=%s exterior_color=%s",
                current_user.id,
                vehicle_config.get("car_type"),
                vehicle_config.get("model_year"),
                exterior_color,
            )

            # Backfill model year from vehicle_config if VIN decode didn't get it
            if not vehicle_year:
                vehicle_year = vehicle_config.get("model_year")

            # Backfill vehicle_model if still raw car_type
            if not vehicle_model or vehicle_model in ("Tesla", "model3", "modely", "models", "modelx"):
                car_type = vehicle_config.get("car_type")
                if car_type:
                    model_map = {
                        "model3": "Model 3",
                        "modely": "Model Y",
                        "models": "Model S",
                        "modelx": "Model X",
                        "cybertruck": "Cybertruck",
                    }
                    vehicle_model = model_map.get(car_type.lower(), car_type)

            # Persist enriched data
            if connection.vehicle_model != vehicle_model:
                connection.vehicle_model = vehicle_model
                db.commit()
        except Exception as e:
            logger.warning(f"Could not fetch live Tesla data for status: {e}")
            # Fall back to session data for battery
            try:
                from sqlalchemy import text
                result = db.execute(text(
                    "SELECT battery_end_pct FROM session_events "
                    "WHERE driver_user_id = :uid AND battery_end_pct IS NOT NULL "
                    "ORDER BY updated_at DESC LIMIT 1"
                ), {"uid": current_user.id}).first()
                if result:
                    battery_level = result[0]
            except Exception:
                pass

    return TeslaConnectionStatus(
        connected=True,
        vehicle_name=connection.vehicle_name,
        vehicle_model=vehicle_model,
        vehicle_year=vehicle_year,
        exterior_color=exterior_color,
        vin=connection.vin[-4:] if connection.vin else None,
        battery_level=battery_level,
    )


@router.get("/connect", response_model=TeslaConnectResponse)
async def initiate_tesla_connection(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Start Tesla OAuth flow.

    Returns authorization URL to redirect user to Tesla login.
    """
    oauth_service = get_tesla_oauth_service()

    # Generate state token for CSRF protection (DB-backed)
    state = secrets.token_urlsafe(32)
    TeslaOAuthState.store(db, state, {"user_id": current_user.id})

    auth_url = oauth_service.get_authorization_url(state)

    return TeslaConnectResponse(
        authorization_url=auth_url,
        state=state
    )


@router.get("/callback")
async def tesla_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db)
):
    """
    Handle Tesla OAuth callback for both login and connect flows.

    Detects the flow from state data:
    - Login flow (purpose="login"): redirects to /tesla-callback with code+state
      (state is preserved for POST /auth/tesla/login to consume)
    - Connect flow (has user_id): exchanges code for tokens and stores connection
    """
    # Peek at state without consuming it (login flow needs it for POST /login)
    state_row = db.query(TeslaOAuthState).filter(TeslaOAuthState.state == state).first()
    if not state_row:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    if state_row.expires_at < datetime.utcnow():
        db.delete(state_row)
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    state_data = json.loads(state_row.data_json)

    # Login flow: pass code+state to frontend, keep state in DB for POST /login
    if state_data.get("purpose") == "login":
        app_url = settings.DRIVER_APP_URL or "https://app.nerava.network"
        return RedirectResponse(
            url=f"{app_url}/tesla-callback?code={code}&state={state}"
        )

    # Connect flow: consume the state and exchange code for tokens
    db.delete(state_row)
    db.commit()

    user_id = state_data["user_id"]
    oauth_service = get_tesla_oauth_service()

    try:
        # Exchange code for tokens
        token_response = await oauth_service.exchange_code_for_tokens(code)

        access_token_raw = token_response["access_token"]
        refresh_token_raw = token_response["refresh_token"]
        expires_in = token_response.get("expires_in", 3600)

        # Try to get user's vehicles (may fail with 412 if partner registration incomplete)
        vehicle = None
        try:
            vehicles = await oauth_service.get_vehicles(access_token_raw)
            if vehicles:
                vehicle = vehicles[0]
        except Exception as ve:
            logger.warning(f"Could not fetch Tesla vehicles (partner registration may be incomplete): {ve}")
            # Continue without vehicle data — tokens are still valid

        # Check for existing connection
        existing = db.query(TeslaConnection).filter(
            TeslaConnection.user_id == user_id,
            TeslaConnection.is_active == True
        ).first()

        # Encrypt tokens before storage
        encrypted_access = encrypt_token(access_token_raw)
        encrypted_refresh = encrypt_token(refresh_token_raw)

        if existing:
            # Update existing connection
            existing.access_token = encrypted_access
            existing.refresh_token = encrypted_refresh
            existing.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            if vehicle:
                existing.vehicle_id = str(vehicle.get("id"))
                existing.vin = vehicle.get("vin")
                existing.vehicle_name = vehicle.get("display_name")
                existing.vehicle_model = vehicle.get("vehicle_config", {}).get("car_type", "Tesla")
            existing.updated_at = datetime.utcnow()
        else:
            # Create new connection
            connection = TeslaConnection(
                user_id=user_id,
                access_token=encrypted_access,
                refresh_token=encrypted_refresh,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                vehicle_id=str(vehicle.get("id")) if vehicle else None,
                vin=vehicle.get("vin") if vehicle else None,
                vehicle_name=vehicle.get("display_name") if vehicle else None,
                vehicle_model=vehicle.get("vehicle_config", {}).get("car_type", "Tesla") if vehicle else None,
            )
            db.add(connection)

        db.commit()

        # Redirect to app with success
        app_url = settings.DRIVER_APP_URL or "https://app.nerava.network"
        return RedirectResponse(url=f"{app_url}/tesla-connected?success=true")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tesla OAuth callback failed: {e}")
        app_url = settings.DRIVER_APP_URL or "https://app.nerava.network"
        return RedirectResponse(url=f"{app_url}/tesla-connected?error=connection_failed")


@router.post("/verify-charging", response_model=VerifyChargingResponse)
async def verify_charging_and_generate_code(
    request: VerifyChargingRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify Tesla charging via Fleet API and generate an EV reward code.

    Attempts real Fleet API charging verification. If the vehicle is asleep
    or unreachable, falls back to issuing a code based on valid Tesla
    connection + location proximity (enforced by frontend).
    """
    # Get Tesla connection
    connection = db.query(TeslaConnection).filter(
        TeslaConnection.user_id == current_user.id,
        TeslaConnection.is_active == True
    ).first()

    if not connection:
        raise HTTPException(
            status_code=400,
            detail="Tesla not connected. Please connect your Tesla first."
        )

    oauth_service = get_tesla_oauth_service()

    # Verify the token is still valid (refresh if needed)
    access_token = await get_valid_access_token(db, connection, oauth_service)
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Tesla session expired. Please reconnect your Tesla."
        )

    # --- Location validation (optional — skip proximity check if unavailable) ---
    if request.merchant_place_id and request.lat is not None and request.lng is not None:
        # Look up merchant coordinates from either table
        merchant_lat: Optional[float] = None
        merchant_lng: Optional[float] = None

        domain_merchant = db.query(DomainMerchant).filter(
            DomainMerchant.google_place_id == request.merchant_place_id
        ).first()
        if domain_merchant:
            merchant_lat = domain_merchant.lat
            merchant_lng = domain_merchant.lng

        if merchant_lat is None:
            wyc_merchant = db.query(Merchant).filter(
                Merchant.place_id == request.merchant_place_id
            ).first()
            if wyc_merchant:
                merchant_lat = wyc_merchant.lat
                merchant_lng = wyc_merchant.lng

        if merchant_lat is not None and merchant_lng is not None:
            distance = haversine_m(request.lat, request.lng, merchant_lat, merchant_lng)
            if distance > PROXIMITY_THRESHOLD_METERS:
                logger.info(
                    f"User {current_user.id} too far from merchant "
                    f"{request.merchant_place_id} ({distance:.0f}m)"
                )
                raise HTTPException(
                    status_code=400,
                    detail="You need to be near the merchant to get a code"
                )

    # Check if user already has an active code for this merchant
    existing_code = db.query(EVVerificationCode).filter(
        EVVerificationCode.user_id == current_user.id,
        EVVerificationCode.merchant_place_id == request.merchant_place_id,
        EVVerificationCode.status == "active",
        EVVerificationCode.expires_at > datetime.utcnow()
    ).first()

    if existing_code:
        return VerifyChargingResponse(
            is_charging=True,
            ev_code=existing_code.code,
            message="You're connected! Show this code to redeem your reward."
        )

    # --- Fleet API charging verification (all vehicles) ---
    try:
        is_charging, charge_data, charging_vehicle = (
            await oauth_service.verify_charging_all_vehicles(access_token)
        )
    except Exception as e:
        logger.error(f"Fleet API verification failed for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=502,
            detail="Unable to reach your Tesla right now. Please make sure your vehicle is online and try again."
        )

    # Backfill primary vehicle_id if missing and we found vehicles
    if not connection.vehicle_id and charging_vehicle:
        connection.vehicle_id = str(charging_vehicle.get("id"))
        connection.vin = charging_vehicle.get("vin")
        connection.vehicle_name = charging_vehicle.get("display_name") or "Tesla"
        connection.vehicle_model = (
            charging_vehicle.get("vehicle_config", {}).get("car_type", "Tesla")
        )
        connection.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Backfilled vehicle_id {connection.vehicle_id} for user {current_user.id}")

    battery_level = charge_data.get("battery_level")
    charge_rate_kw = charge_data.get("charger_power")

    if not is_charging:
        charging_state = charge_data.get("charging_state", "unknown")
        logger.warning(
            f"Fleet API: no vehicle charging for user {current_user.id} | "
            f"state={charging_state} battery={charge_data.get('battery_level')} "
            f"rate={charge_data.get('charge_rate')} power={charge_data.get('charger_power')} "
            f"error={charge_data.get('error', 'none')}"
        )
        return VerifyChargingResponse(
            is_charging=False,
            battery_level=battery_level,
            message="Your Tesla isn't currently charging. Plug in to verify your session and unlock your reward."
        )

    # Charging confirmed — generate EV code
    charging_vin = charging_vehicle.get("vin") if charging_vehicle else connection.vin
    logger.info(f"Fleet API confirmed charging for user {current_user.id} "
               f"(vin={charging_vin}, battery={battery_level}%, power={charge_rate_kw}kW)")

    ev_code = generate_ev_code()

    # Ensure code is unique
    while db.query(EVVerificationCode).filter(EVVerificationCode.code == ev_code).first():
        ev_code = generate_ev_code()

    code_record = EVVerificationCode(
        user_id=current_user.id,
        tesla_connection_id=connection.id,
        code=ev_code,
        charger_id=request.charger_id,
        merchant_place_id=request.merchant_place_id,
        merchant_name=request.merchant_name,
        charging_verified=True,
        battery_level=battery_level,
        charge_rate_kw=charge_rate_kw,
        lat=str(request.lat) if request.lat else None,
        lng=str(request.lng) if request.lng else None,
        expires_at=datetime.utcnow() + timedelta(hours=2),
    )
    db.add(code_record)

    connection.last_used_at = datetime.utcnow()
    db.commit()

    logger.info(f"Generated EV code {ev_code} for user {current_user.id} "
               f"at merchant {request.merchant_name} (Fleet API verified)")

    return VerifyChargingResponse(
        is_charging=True,
        battery_level=battery_level,
        charge_rate_kw=charge_rate_kw,
        ev_code=ev_code,
        message="Charging verified! Show this code to the merchant to redeem your reward.",
    )


@router.get("/codes", response_model=list[EVCodeResponse])
async def get_user_ev_codes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's active EV verification codes."""
    codes = db.query(EVVerificationCode).filter(
        EVVerificationCode.user_id == current_user.id,
        EVVerificationCode.status == "active",
        EVVerificationCode.expires_at > datetime.utcnow()
    ).order_by(EVVerificationCode.created_at.desc()).limit(10).all()

    return [
        EVCodeResponse(
            code=c.code,
            merchant_name=c.merchant_name,
            expires_at=c.expires_at,
            status=c.status
        )
        for c in codes
    ]


@router.post("/disconnect")
async def disconnect_tesla(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Disconnect Tesla account."""
    connection = db.query(TeslaConnection).filter(
        TeslaConnection.user_id == current_user.id,
        TeslaConnection.is_active == True
    ).first()

    if connection:
        connection.is_active = False
        connection.updated_at = datetime.utcnow()
        db.commit()

    return {"success": True, "message": "Tesla disconnected"}
