"""
Arrival Router V2 — Phase 0 phone-first EV arrival endpoints.

Endpoints for the Phase 0 flow:
- Phone session creation (no auth)
- Car PIN generation (EV browser required)
- PIN verification
- Location checking and promo code generation
- Session status polling
- Promo code redemption
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.while_you_charge import Merchant
from app.services import arrival_service_v2 as service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/arrival", tags=["arrival"])


class StartRequest(BaseModel):
    merchant_id: str


class VerifyPinRequest(BaseModel):
    session_token: str
    pin: str


class CheckLocationRequest(BaseModel):
    session_token: str
    lat: float
    lng: float


class RedeemRequest(BaseModel):
    promo_code: str


@router.post("/start")
def start_session(request: StartRequest, db: Session = Depends(get_db)):
    """
    Start a new phone session for a merchant.
    Called when driver taps "Check In" on merchant page.

    Accepts either internal merchant ID (m_xxx) or Google Place ID (ChIJxxx).
    """
    merchant_id = request.merchant_id

    # Try to find merchant by ID first
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()

    # If not found and looks like a Google Place ID, try place_id lookup
    if not merchant and merchant_id.startswith("ChIJ"):
        merchant = db.query(Merchant).filter(Merchant.place_id == merchant_id).first()
        if merchant:
            merchant_id = merchant.id  # Use internal ID for session
            logger.info(f"[ARRIVAL] Resolved Place ID {request.merchant_id} to merchant {merchant.id}")

    if not merchant:
        logger.warning(f"[ARRIVAL] Merchant not found: {request.merchant_id}")
        raise HTTPException(status_code=404, detail="Merchant not found")

    session, token = service.create_phone_session(db, merchant_id)

    return {
        "session_id": str(session.id),
        "session_token": token,
        "merchant": {
            "id": str(merchant.id),
            "name": merchant.name,
            "logo_url": merchant.logo_url,
            "offer": getattr(merchant, 'ev_offer_text', None) or "$5 charging credit",
            "address": merchant.address,
        },
        "state": "pending",
        "next_step": "verify_car",
    }


@router.post("/car-pin")
def generate_car_pin(request: Request, db: Session = Depends(get_db)):
    """
    Generate a PIN for display in car browser.
    PIN is NOT tied to any session - it's a standalone linking token.

    Note: EV browser restriction removed for demo. User-Agent logged for future verification.
    """
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client else None

    # Log user agent for future verification (removed EV browser requirement for demo)
    from app.utils.ev_browser import detect_ev_browser
    ev_info = detect_ev_browser(user_agent)
    is_ev_browser = ev_info is not None

    logger.info(f"[CAR-PIN] User-Agent: {user_agent[:200]}")
    logger.info(f"[CAR-PIN] IP: {ip_address}, Is EV Browser: {is_ev_browser}")
    if ev_info:
        logger.info(f"[CAR-PIN] EV Info: brand={ev_info.brand}, firmware={ev_info.firmware_version}")

    car_pin = service.create_car_pin(db, user_agent, ip_address)

    return {
        "pin": car_pin.pin,
        "expires_in_seconds": 300,
        "display_message": "Enter this code on your phone",
        "is_ev_browser": is_ev_browser,
    }


@router.post("/verify-pin")
def verify_pin(request: VerifyPinRequest, db: Session = Depends(get_db)):
    """
    Verify PIN entered on phone and link car verification to session.
    """
    success, error, session = service.verify_pin(db, request.session_token, request.pin)

    if not success:
        raise HTTPException(status_code=400, detail=error)

    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()

    return {
        "session_id": str(session.id),
        "state": "car_verified",
        "next_step": "go_to_merchant",
        "merchant": {
            "id": str(merchant.id),
            "name": merchant.name,
            "address": merchant.address,
            "lat": merchant.lat,
            "lng": merchant.lng,
            "geofence_radius_m": 150,  # Default geofence radius
        } if merchant else None,
    }


@router.post("/check-location")
def check_location(request: CheckLocationRequest, db: Session = Depends(get_db)):
    """
    Check if driver has arrived at merchant geofence.
    Called periodically by phone while driver is en route.
    Generates and returns promo code when driver arrives.
    """
    arrived, result = service.check_location(
        db, request.session_token, request.lat, request.lng
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@router.get("/status")
def get_status(session_token: str, db: Session = Depends(get_db)):
    """
    Get current session status.
    Used for polling and recovering session state.
    """
    result = service.get_session_status(db, session_token)

    if not result:
        raise HTTPException(status_code=404, detail="Session not found")

    return result


@router.post("/redeem")
def redeem_code(request: RedeemRequest, db: Session = Depends(get_db)):
    """
    Mark a promo code as redeemed.
    Called by merchant when driver shows code.
    """
    success, error, info = service.redeem_promo_code(db, request.promo_code)

    if not success:
        raise HTTPException(status_code=400, detail=error)

    return {
        "redeemed": True,
        **info,
    }
