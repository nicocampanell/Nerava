"""
Arrival Service V2 — Phase 0 phone-first EV arrival flow.

Core business logic for PIN pairing, geofence verification, and promo code generation.
"""
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.arrival_session import ArrivalSession
from app.models.car_pin import CarPin
from app.models.while_you_charge import Merchant
from app.services.geo import haversine_m

# PIN alphabet: excludes confusing chars (0, O, I, 1, L)
PIN_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
PIN_TTL_MINUTES = 5
PROMO_CODE_TTL_MINUTES = 10
SESSION_TTL_HOURS = 2
MAX_PIN_ATTEMPTS = 5


def generate_pin() -> str:
    """Generate 6-char PIN in format XXX-XXX."""
    part1 = ''.join(secrets.choice(PIN_ALPHABET) for _ in range(3))
    part2 = ''.join(secrets.choice(PIN_ALPHABET) for _ in range(3))
    return f"{part1}-{part2}"


def generate_promo_code() -> str:
    """Generate promo code in format EV-XXXXX."""
    digits = ''.join(secrets.choice(string.digits) for _ in range(5))
    return f"EV-{digits}"


def generate_session_token() -> str:
    """Generate a random session token (simple UUID, not fingerprint)."""
    return secrets.token_urlsafe(32)


def create_phone_session(db: Session, merchant_id: str) -> Tuple[ArrivalSession, str]:
    """
    Create a new phone session in 'pending' state.
    Returns (session, session_token).

    Uses pairing_token field for the session token (existing column).
    Uses flow_type='phase0' to distinguish from legacy flow.
    Status 'pending_pairing' is the Phase 0 initial state.
    """
    token = generate_session_token()

    # Get a placeholder user ID for Phase 0 (anonymous flow)
    # In Phase 0, we don't require authentication but driver_id is NOT NULL
    # We'll use driver_id=1 as a placeholder for anonymous sessions
    # TODO: Create a proper anonymous/system user or make driver_id nullable
    ANONYMOUS_DRIVER_ID = 1

    session = ArrivalSession(
        merchant_id=merchant_id,
        pairing_token=token,  # Use existing pairing_token column
        pairing_token_expires_at=datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
        expires_at=datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS),
        driver_id=ANONYMOUS_DRIVER_ID,  # Placeholder for anonymous Phase 0 users
        arrival_type="ev_dine_in",  # Default type
        flow_type="phase0",  # Mark as Phase 0 flow
        status="pending_pairing",  # Phase 0 status
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    return session, token


def create_car_pin(db: Session, user_agent: str, ip_address: str) -> CarPin:
    """
    Generate a new PIN for car browser display.
    PIN is NOT tied to any session yet - it's a standalone record.
    """
    # Generate unique PIN (retry if collision)
    for _ in range(10):
        pin = generate_pin()
        existing = db.query(CarPin).filter(CarPin.pin == pin).first()
        if not existing:
            break
    else:
        raise Exception("Failed to generate unique PIN after 10 attempts")

    car_pin = CarPin(
        pin=pin,
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=datetime.utcnow() + timedelta(minutes=PIN_TTL_MINUTES),
    )
    db.add(car_pin)
    db.commit()
    db.refresh(car_pin)

    return car_pin


def verify_pin(db: Session, session_token: str, pin: str) -> Tuple[bool, str, Optional[ArrivalSession]]:
    """
    Verify PIN and link car verification to phone session.

    Returns (success, error_message, session).
    """
    # Find phone session using pairing_token column
    session = db.query(ArrivalSession).filter(
        ArrivalSession.pairing_token == session_token,
        ArrivalSession.status == "pending_pairing",
    ).first()

    if not session:
        return False, "Session not found or already verified", None

    # Check attempt limit using verification_attempts column
    if (session.verification_attempts or 0) >= MAX_PIN_ATTEMPTS:
        session.status = "expired"
        db.commit()
        return False, "Too many attempts. Please start over.", None

    # Increment attempts
    session.verification_attempts = (session.verification_attempts or 0) + 1

    # Find PIN (case-insensitive, normalize format)
    normalized_pin = pin.upper().strip()
    if len(normalized_pin) == 6:
        normalized_pin = f"{normalized_pin[:3]}-{normalized_pin[3:]}"

    car_pin = db.query(CarPin).filter(CarPin.pin == normalized_pin).first()

    if not car_pin:
        db.commit()
        return False, "Invalid code. Please check and try again.", None

    if not car_pin.is_valid():
        db.commit()
        return False, "Code expired. Please get a new code from your car.", None

    # Link car verification to session using existing columns
    session.verified_at = datetime.utcnow()
    session.verification_method = "car_pin"
    session.browser_source = "phone"  # Phone initiated the session
    session.ev_brand = car_pin.user_agent[:30] if car_pin.user_agent else None  # Store browser info
    session.status = "pending_verification"  # Move to next status

    # Mark PIN as used
    car_pin.used_at = datetime.utcnow()
    car_pin.used_by_session_id = str(session.id)

    db.commit()
    db.refresh(session)

    return True, "", session


def check_location(
    db: Session,
    session_token: str,
    lat: float,
    lng: float
) -> Tuple[bool, dict]:
    """
    Check if driver has arrived at merchant geofence.
    If arrived, generate and return promo code.

    Returns (arrived, response_dict).
    """
    # Find session using pairing_token, accept pending_verification status
    session = db.query(ArrivalSession).filter(
        ArrivalSession.pairing_token == session_token,
        ArrivalSession.status == "pending_verification",
    ).first()

    if not session:
        return False, {"error": "Session not found or not ready for location check"}

    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
    if not merchant:
        return False, {"error": "Merchant not found"}

    # Calculate distance using haversine
    distance = haversine_m(lat, lng, merchant.lat, merchant.lng)

    geofence_radius = 150  # Default geofence radius in meters

    if distance > geofence_radius:
        return False, {
            "state": "car_verified",
            "arrived": False,
            "distance_m": round(distance),
            "message": f"Drive to {merchant.name} to unlock your credit"
        }

    # ARRIVED! Generate promo code
    promo_code = generate_promo_code()
    promo_expires = datetime.utcnow() + timedelta(minutes=PROMO_CODE_TTL_MINUTES)

    # Update session using existing columns
    session.status = "verified"  # Or "code_generated" for clarity
    session.arrival_detected_at = datetime.utcnow()
    session.arrival_lat = lat
    session.arrival_lng = lng
    session.arrival_distance_m = distance
    session.arrival_code = promo_code  # Store in arrival_code column
    session.arrival_code_generated_at = datetime.utcnow()
    session.arrival_code_expires_at = promo_expires

    db.commit()

    return True, {
        "state": "arrived",
        "arrived": True,
        "promo_code": promo_code,
        "promo_code_expires_at": promo_expires.isoformat(),
        "message": "Show this code to the cashier"
    }


def redeem_promo_code(db: Session, promo_code: str) -> Tuple[bool, str, Optional[dict]]:
    """
    Mark a promo code as redeemed (called by merchant).

    Returns (success, error_message, session_info).
    """
    # Use arrival_code column for promo codes
    session = db.query(ArrivalSession).filter(
        ArrivalSession.arrival_code == promo_code.upper().strip()
    ).first()

    if not session:
        return False, "Promo code not found", None

    if session.arrival_code_redeemed_at:
        return False, "Already redeemed", {
            "session_id": str(session.id),
            "redeemed_at": session.arrival_code_redeemed_at.isoformat(),
        }

    if session.arrival_code_expires_at and session.arrival_code_expires_at < datetime.utcnow():
        return False, "Promo code expired", None

    session.arrival_code_redeemed_at = datetime.utcnow()
    session.arrival_code_redemption_count = (session.arrival_code_redemption_count or 0) + 1
    session.status = "code_redeemed"
    db.commit()

    return True, "", {
        "session_id": str(session.id),
        "merchant_id": str(session.merchant_id),
        "redeemed_at": session.arrival_code_redeemed_at.isoformat(),
    }


def get_session_status(db: Session, session_token: str) -> Optional[dict]:
    """Get current session status for polling."""
    session = db.query(ArrivalSession).filter(
        ArrivalSession.pairing_token == session_token
    ).first()

    if not session:
        return None

    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()

    # Map status to phase0 state names for frontend compatibility
    status_to_state = {
        "pending_pairing": "pending",
        "pending_verification": "car_verified",
        "verified": "arrived",
        "code_redeemed": "redeemed",
        "expired": "expired",
    }
    state = status_to_state.get(session.status, session.status)

    result = {
        "session_id": str(session.id),
        "state": state,
        "merchant": {
            "id": str(merchant.id),
            "name": merchant.name,
            "address": merchant.address,
            "lat": merchant.lat,
            "lng": merchant.lng,
            "geofence_radius_m": 150,  # Default geofence radius
        } if merchant else None,
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }

    # Only include promo code if state is 'arrived' or 'redeemed'
    if state in ("arrived", "redeemed") and session.arrival_code:
        result["promo_code"] = session.arrival_code
        result["promo_code_expires_at"] = session.arrival_code_expires_at.isoformat() if session.arrival_code_expires_at else None

    return result
