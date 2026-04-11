"""
EV Arrival Code Checkin Router — /v1/checkin/*

Supports two flows:
1. V0 Arrival Code flow (QR pairing from car browser)
2. Phone-First flow (SMS session links from Tesla browser)

Handles session creation, verification, code generation, and merchant confirmation.
"""
import hashlib
import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver, get_current_driver_optional
from app.models import User
from app.models.arrival_session import ArrivalSession
from app.models.while_you_charge import Charger, Merchant
from app.services.analytics import get_analytics_client
from app.services.checkin_service import CODE_TTL_MINUTES, SESSION_TTL_MINUTES, get_checkin_service
from app.utils.ev_browser import detect_ev_browser, require_ev_browser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/checkin", tags=["checkin"])


# ─── Request/Response Models ──────────────────────────────────────────

class MerchantInfo(BaseModel):
    id: str
    name: str
    category: Optional[str] = None
    distance_m: Optional[int] = None
    walk_time_minutes: Optional[int] = None
    ordering_url: Optional[str] = None
    image_url: Optional[str] = None


class StartCheckinRequest(BaseModel):
    charger_id: Optional[str] = None
    lat: float
    lng: float
    accuracy_m: Optional[float] = None
    idempotency_key: Optional[str] = None


class StartCheckinResponse(BaseModel):
    session_id: str
    status: str
    pairing_required: bool
    pairing_qr_url: Optional[str] = None
    pairing_url: Optional[str] = None
    pairing_token: Optional[str] = None
    charger_id: Optional[str] = None
    charger_name: Optional[str] = None
    nearby_merchants: List[MerchantInfo] = []
    expires_at: str


class VerifyCheckinRequest(BaseModel):
    session_id: str
    method: str = Field(..., pattern="^(browser_geofence|phone_geofence|qr_scan)$")
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy_m: Optional[float] = None
    qr_payload: Optional[str] = None


class VerifyCheckinResponse(BaseModel):
    verified: bool
    verification_method: Optional[str] = None
    error: Optional[str] = None


class GenerateCodeRequest(BaseModel):
    session_id: str
    merchant_id: Optional[str] = None


class GenerateCodeResponse(BaseModel):
    code: str
    expires_at: str
    expires_in_minutes: int
    sms_sent: bool
    sms_phone_masked: str
    checkout_url: Optional[str] = None
    merchant_name: Optional[str] = None
    nearby_merchants: List[MerchantInfo] = []


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    flow_type: str
    paired: bool
    verified: bool
    code: Optional[str] = None
    code_expires_at: Optional[str] = None
    code_redeemed: bool = False
    merchant_id: Optional[str] = None
    merchant_name: Optional[str] = None
    charger_id: Optional[str] = None
    charger_name: Optional[str] = None
    expires_at: str


class RedeemCodeRequest(BaseModel):
    code: str
    order_number: Optional[str] = None
    order_total_cents: Optional[int] = None


class RedeemCodeResponse(BaseModel):
    redeemed: bool
    session_id: str
    already_redeemed: bool
    error: Optional[str] = None


class MerchantConfirmRequest(BaseModel):
    code: Optional[str] = None
    session_id: Optional[str] = None
    order_total_cents: Optional[int] = None
    confirmed: bool = True


class MerchantConfirmResponse(BaseModel):
    confirmed: bool
    session_id: str
    billable_amount_cents: Optional[int] = None
    billing_event_id: Optional[str] = None
    error: Optional[str] = None


class PairSessionRequest(BaseModel):
    pairing_token: str
    phone: str = Field(..., min_length=10, max_length=20)


class PairSessionResponse(BaseModel):
    paired: bool
    session_id: Optional[str] = None
    otp_sent: bool
    error: Optional[str] = None


class ConfirmPairingRequest(BaseModel):
    pairing_token: str
    otp_code: str = Field(..., min_length=4, max_length=8)


class ConfirmPairingResponse(BaseModel):
    confirmed: bool
    session_id: Optional[str] = None
    access_token: Optional[str] = None
    error: Optional[str] = None


# ─── Phone-First Flow Models ──────────────────────────────────────────

class PhoneStartRequest(BaseModel):
    """Request to start phone-first checkin from car browser."""
    phone: str = Field(..., min_length=10, max_length=20, description="Phone number (US formats accepted)")
    charger_hint: Optional[str] = Field(None, description="Optional charger ID hint")

    @validator('phone')
    def normalize_phone(cls, v):
        """Normalize phone to E.164 format."""
        # Remove non-digits
        digits = re.sub(r'\D', '', v)

        # Handle US numbers
        if len(digits) == 10:
            return f"+1{digits}"
        elif len(digits) == 11 and digits.startswith('1') or len(digits) > 10 and not v.startswith('+'):
            return f"+{digits}"

        return v if v.startswith('+') else f"+{digits}"


class PhoneStartResponse(BaseModel):
    """Response from phone-first checkin start."""
    ok: bool
    session_code: Optional[str] = None
    expires_in_seconds: int = 0
    message: Optional[str] = None
    error: Optional[str] = None


class TokenSessionRequest(BaseModel):
    """Request with session token."""
    token: str = Field(..., min_length=20)


class TokenSessionResponse(BaseModel):
    """Session status response for token-based lookup."""
    session_id: str
    session_code: str
    status: str
    flow_type: str
    verified: bool
    redeemed: bool
    charger_name: Optional[str] = None
    merchant_name: Optional[str] = None
    expires_in_seconds: int
    expires_at: Optional[str] = None


class TokenVerifyRequest(BaseModel):
    """Request to verify session via token and geolocation."""
    token: str = Field(..., min_length=20)
    lat: float
    lng: float
    accuracy_m: Optional[float] = None


# ─── Helper Functions ─────────────────────────────────────────────────

def _capture_event(event: str, user_id: Optional[int], properties: dict):
    """Fire-and-forget analytics event."""
    try:
        analytics = get_analytics_client()
        if analytics:
            analytics.capture(
                distinct_id=str(user_id) if user_id else "anonymous",
                event=event,
                properties=properties,
            )
    except Exception as e:
        logger.warning(f"Analytics capture failed: {e}")


def _session_to_status(session: ArrivalSession, db: Session) -> SessionStatusResponse:
    """Convert session to status response."""
    merchant_name = None
    charger_name = None

    if session.merchant_id:
        merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
        if merchant:
            merchant_name = merchant.name

    if session.charger_id:
        charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
        if charger:
            charger_name = charger.name

    return SessionStatusResponse(
        session_id=str(session.id),
        status=session.status,
        flow_type=session.flow_type,
        paired=session.paired_at is not None or session.driver_id is not None,
        verified=session.verified_at is not None,
        code=session.arrival_code,
        code_expires_at=session.arrival_code_expires_at.isoformat() + "Z" if session.arrival_code_expires_at else None,
        code_redeemed=session.arrival_code_redeemed_at is not None,
        merchant_id=session.merchant_id,
        merchant_name=merchant_name,
        charger_id=session.charger_id,
        charger_name=charger_name,
        expires_at=session.expires_at.isoformat() + "Z" if session.expires_at else "",
    )


def _get_client_ip(request: Request) -> str:
    """Get client IP from request, handling proxies."""
    # Check X-Forwarded-For header (from load balancer/proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take first IP (client IP)
        return forwarded.split(",")[0].strip()
    # Fall back to direct connection
    return request.client.host if request.client else "unknown"


def _hash_phone_for_event(phone: str) -> str:
    """Hash phone for analytics (privacy-preserving)."""
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


# ─── Phone-First Flow Endpoints ───────────────────────────────────────

@router.post("/phone-start", response_model=PhoneStartResponse, status_code=201)
async def phone_start_checkin(
    req: PhoneStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Start a phone-first checkin session from car browser.

    REQUIRES: Tesla or EV in-car browser (validated via User-Agent).

    Flow:
    1. Driver enters phone number in car browser
    2. We send SMS with session link + code
    3. Driver opens link on phone to continue

    Rate limits:
    - 3 sessions per phone per day
    - 10 requests per IP per hour
    """
    # Validate EV browser
    try:
        ev_info = require_ev_browser(request)
    except HTTPException:
        _capture_event("checkin.browser_rejected", None, {
            "user_agent": request.headers.get("User-Agent", "")[:100],
            "ip": _get_client_ip(request),
        })
        raise

    service = get_checkin_service()
    client_ip = _get_client_ip(request)

    # Build EV browser info
    ev_browser_info = {
        "browser_source": "tesla_browser" if ev_info.brand == "Tesla" else "ev_browser",
        "brand": ev_info.brand,
        "firmware_version": ev_info.firmware_version,
    }

    try:
        session, token, error = await service.phone_start_checkin(
            db=db,
            phone=req.phone,
            charger_hint=req.charger_hint,
            ev_browser_info=ev_browser_info,
            client_ip=client_ip,
        )

        if error:
            error_messages = {
                "rate_limit_phone": "You've reached the daily limit. Please try again tomorrow.",
                "rate_limit_ip": "Too many requests. Please try again later.",
            }

            _capture_event("checkin.rate_limited", None, {
                "phone_hash": _hash_phone_for_event(req.phone),
                "ip": client_ip,
                "limit_type": error,
            })

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": error,
                    "message": error_messages.get(error, "Rate limit exceeded"),
                }
            )

        # Send SMS
        sms_sent = await service.send_session_sms(db, session, req.phone, token)

        _capture_event("checkin.session_created", None, {
            "session_id": str(session.id),
            "phone_hash": _hash_phone_for_event(req.phone),
            "ip": client_ip,
            "source": "phone_first",
            "ev_brand": ev_info.brand,
            "user_agent": ev_info.user_agent[:100] if ev_info.user_agent else None,
        })

        if sms_sent:
            _capture_event("checkin.sms_sent", None, {
                "session_id": str(session.id),
                "phone_hash": _hash_phone_for_event(req.phone),
            })

        return PhoneStartResponse(
            ok=True,
            session_code=session.arrival_code,
            expires_in_seconds=SESSION_TTL_MINUTES * 60,
            message="Sent! Open the link on your phone.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Phone start checkin failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start check-in. Please try again.",
        )


@router.get("/s/{token}", response_model=TokenSessionResponse)
async def get_session_by_token(
    token: str,
    request: Request,
    user: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Get session status by signed token (from SMS link).

    Returns limited data if not authenticated.
    Returns full data if authenticated and phone matches.
    """
    service = get_checkin_service()

    session, payload = service.get_session_by_token(db, token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    _capture_event("checkin.link_opened", session.driver_id, {
        "session_id": str(session.id),
        "user_agent": request.headers.get("User-Agent", "")[:100],
        "authenticated": user is not None,
    })

    # Build response
    include_sensitive = user is not None and session.driver_id == user.id
    status_data = service.get_session_status_response(db, session, include_sensitive)

    return TokenSessionResponse(**status_data)


@router.post("/s/{token}/activate")
async def activate_session_by_token(
    token: str,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Activate a session after OTP verification.

    Called when user completes phone auth after opening SMS link.
    Links user to session.
    """
    service = get_checkin_service()

    session, payload = service.get_session_by_token(db, token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    # Get phone hash from token payload
    phone_hash = payload.get('phone_hash', '')

    activated = await service.activate_session(db, session, user, phone_hash)

    if not activated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to activate session",
        )

    _capture_event("checkin.otp_completed", user.id, {
        "session_id": str(session.id),
    })

    status_data = service.get_session_status_response(db, session, include_sensitive=True)
    return {"ok": True, "session": status_data}


@router.post("/s/{token}/verify", response_model=VerifyCheckinResponse)
async def verify_session_by_token(
    token: str,
    lat: float,
    lng: float,
    accuracy_m: Optional[float] = None,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Verify session location using phone geolocation.

    Called after session is activated.
    Uses phone geofence method.
    """
    service = get_checkin_service()

    session, payload = service.get_session_by_token(db, token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    # Verify user owns session
    if session.driver_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this session",
        )

    verified, error = await service.verify_checkin(
        db=db,
        session=session,
        method="phone_geofence",
        lat=lat,
        lng=lng,
        qr_payload=None,
    )

    if verified:
        _capture_event("checkin.verified", user.id, {
            "session_id": str(session.id),
            "method": "phone_geofence",
            "accuracy_m": accuracy_m,
            "charger_id": session.charger_id,
        })
    else:
        _capture_event("checkin.verification_failed", user.id, {
            "session_id": str(session.id),
            "method": "phone_geofence",
            "error": error,
        })

    return VerifyCheckinResponse(
        verified=verified,
        verification_method="phone_geofence" if verified else None,
        error=error,
    )


# ─── V0 Flow Endpoints ────────────────────────────────────────────────

@router.post("/start", response_model=StartCheckinResponse, status_code=201)
async def start_checkin(
    req: StartCheckinRequest,
    request: Request,
    user: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Start a checkin session from car browser.

    If user is not authenticated, returns pairing info with QR code.
    If authenticated, proceeds to verification.

    Idempotent via idempotency_key.
    """
    # Detect EV browser
    user_agent = request.headers.get("User-Agent", "")
    ev_info = detect_ev_browser(user_agent)

    browser_source = "web"
    if ev_info.is_ev_browser:
        browser_source = "tesla_browser" if ev_info.brand == "Tesla" else "ev_browser"

    ev_browser_info = {
        "browser_source": browser_source,
        "brand": ev_info.brand,
        "firmware_version": ev_info.firmware_version,
    }

    service = get_checkin_service()

    try:
        session, pairing_required, pairing_url, nearby = await service.start_checkin(
            db=db,
            lat=req.lat,
            lng=req.lng,
            accuracy_m=req.accuracy_m,
            user=user,
            charger_id=req.charger_id,
            ev_browser_info=ev_browser_info,
            idempotency_key=req.idempotency_key,
        )

        # Get charger info
        charger_name = None
        if session.charger_id:
            charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
            if charger:
                charger_name = charger.name

        # Build QR URL if needed
        pairing_qr_url = None
        if pairing_required and session.pairing_token:
            # QR code will be generated client-side from pairing_url
            pairing_qr_url = f"/api/qr?data={pairing_url}"

        _capture_event("checkin.started", user.id if user else None, {
            "session_id": str(session.id),
            "pairing_required": pairing_required,
            "charger_id": session.charger_id,
            "ev_browser": ev_info.is_ev_browser,
            "ev_brand": ev_info.brand,
        })

        return StartCheckinResponse(
            session_id=str(session.id),
            status=session.status,
            pairing_required=pairing_required,
            pairing_qr_url=pairing_qr_url,
            pairing_url=pairing_url,
            pairing_token=session.pairing_token,
            charger_id=session.charger_id,
            charger_name=charger_name,
            nearby_merchants=[MerchantInfo(**m) for m in nearby],
            expires_at=session.expires_at.isoformat() + "Z",
        )

    except Exception as e:
        logger.error(f"Error starting checkin: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start checkin",
        )


@router.post("/verify", response_model=VerifyCheckinResponse)
async def verify_checkin(
    req: VerifyCheckinRequest,
    user: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Verify arrival using one of three methods:

    A) browser_geofence: Browser location within 250m of charger
    B) phone_geofence: Phone location within 250m of charger
    C) qr_scan: QR code at charger (charger_id encoded)

    Only ONE method needs to succeed.
    Rate limited: 10 attempts per session.
    """
    service = get_checkin_service()

    session = service.get_session_by_id(db, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check expiry
    if session.expires_at < datetime.utcnow():
        session.status = 'expired'
        db.commit()
        raise HTTPException(status_code=410, detail="Session has expired")

    verified, error = await service.verify_checkin(
        db=db,
        session=session,
        method=req.method,
        lat=req.lat,
        lng=req.lng,
        qr_payload=req.qr_payload,
    )

    if verified:
        _capture_event("checkin.verified", session.driver_id, {
            "session_id": req.session_id,
            "method": req.method,
            "charger_id": session.charger_id,
        })

    return VerifyCheckinResponse(
        verified=verified,
        verification_method=req.method if verified else None,
        error=error,
    )


@router.post("/generate-code", response_model=GenerateCodeResponse)
async def generate_code(
    req: GenerateCodeRequest,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Generate EV Arrival Code after verification.

    - Code format: NVR-XXXX (4 alphanumeric chars)
    - TTL: 30 minutes
    - Single-use
    - Sends SMS with code + checkout URL

    Idempotent: returns existing code if already generated.
    """
    service = get_checkin_service()

    session = service.get_session_by_id(db, req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.driver_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized for this session")

    # Check expiry
    if session.expires_at < datetime.utcnow():
        session.status = 'expired'
        db.commit()
        raise HTTPException(status_code=410, detail="Session has expired")

    # Validate session state
    if session.status not in ('verified', 'code_generated'):
        raise HTTPException(
            status_code=400,
            detail=f"Session must be verified before generating code (current: {session.status})",
        )

    try:
        session, nearby = await service.generate_code(
            db=db,
            session=session,
            merchant_id=req.merchant_id,
        )

        # Send SMS
        sms_sent = await service.send_code_sms(db, session, user.phone)

        # Get merchant name
        merchant_name = None
        if session.merchant_id:
            merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
            if merchant:
                merchant_name = merchant.name

        _capture_event("checkin.code_generated", user.id, {
            "session_id": req.session_id,
            "code": session.arrival_code,
            "merchant_id": req.merchant_id,
            "sms_sent": sms_sent,
        })

        expires_in = CODE_TTL_MINUTES
        if session.arrival_code_expires_at:
            delta = session.arrival_code_expires_at - datetime.utcnow()
            expires_in = max(0, int(delta.total_seconds() / 60))

        return GenerateCodeResponse(
            code=session.arrival_code,
            expires_at=session.arrival_code_expires_at.isoformat() + "Z",
            expires_in_minutes=expires_in,
            sms_sent=sms_sent,
            sms_phone_masked=service.mask_phone(user.phone),
            checkout_url=session.checkout_url_sent,
            merchant_name=merchant_name,
            nearby_merchants=[MerchantInfo(**m) for m in nearby],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating code: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate code",
        )


@router.get("/session/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: str,
    db: Session = Depends(get_db),
):
    """
    Poll for session status.

    Used by car browser to detect:
    - Pairing complete
    - Verification complete
    - Code generation
    - Code redemption
    - Merchant confirmation
    """
    service = get_checkin_service()

    session = service.get_session_by_id(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check expiry
    if session.expires_at < datetime.utcnow() and session.status not in ('completed', 'merchant_confirmed', 'expired'):
        session.status = 'expired'
        db.commit()

    # Check code expiry
    if (session.arrival_code and session.arrival_code_expires_at and
            session.arrival_code_expires_at < datetime.utcnow() and
            not session.arrival_code_redeemed_at):
        session.status = 'expired'
        db.commit()

    return _session_to_status(session, db)


@router.post("/redeem", response_model=RedeemCodeResponse)
async def redeem_code(
    req: RedeemCodeRequest,
    db: Session = Depends(get_db),
):
    """
    Log code redemption.

    Called when driver applies code at checkout.
    Marks code as redeemed (prevents re-use).

    Note: Merchant confirmation is the billing trigger, not redemption.
    """
    service = get_checkin_service()

    session, already_redeemed, error = await service.redeem_code(
        db=db,
        code=req.code,
        order_number=req.order_number,
        order_total_cents=req.order_total_cents,
    )

    if not session:
        raise HTTPException(status_code=404, detail=error or "Code not found")

    if error and not already_redeemed:
        raise HTTPException(status_code=410, detail=error)

    if not already_redeemed:
        _capture_event("checkin.code_redeemed", session.driver_id, {
            "session_id": str(session.id),
            "code": req.code,
            "order_number": req.order_number,
        })

    return RedeemCodeResponse(
        redeemed=not already_redeemed,
        session_id=str(session.id),
        already_redeemed=already_redeemed,
        error=error,
    )


@router.post("/merchant-confirm", response_model=MerchantConfirmResponse)
async def merchant_confirm(
    req: MerchantConfirmRequest,
    db: Session = Depends(get_db),
):
    """
    Merchant confirms fulfillment.

    Creates BillingEvent if order_total_cents is provided.

    Can be called via:
    - Merchant portal
    - SMS reply webhook

    Idempotent: second call returns existing confirmation.
    """
    if not req.code and not req.session_id:
        raise HTTPException(status_code=400, detail="Either code or session_id is required")

    service = get_checkin_service()

    session, billing_event, error = await service.merchant_confirm(
        db=db,
        code=req.code,
        session_id=req.session_id,
        order_total_cents=req.order_total_cents,
    )

    if not session:
        raise HTTPException(status_code=404, detail=error or "Session not found")

    _capture_event("checkin.merchant_confirmed", session.driver_id, {
        "session_id": str(session.id),
        "order_total_cents": req.order_total_cents,
        "billable_cents": billing_event.billable_cents if billing_event else None,
    })

    return MerchantConfirmResponse(
        confirmed=True,
        session_id=str(session.id),
        billable_amount_cents=billing_event.billable_cents if billing_event else None,
        billing_event_id=str(billing_event.id) if billing_event else None,
        error=error,
    )


# ─── Pairing Endpoints ────────────────────────────────────────────────

@router.post("/pair", response_model=PairSessionResponse)
async def pair_session(
    req: PairSessionRequest,
    db: Session = Depends(get_db),
):
    """
    Start pairing from phone.

    Called when user scans QR code on phone.
    Sends OTP to provided phone number.

    Rate limited: 3 OTP requests per phone per hour.
    """
    service = get_checkin_service()

    session = service.get_session_by_pairing_token(db, req.pairing_token)
    if not session:
        raise HTTPException(status_code=404, detail="Pairing token not found")

    # Check expiry
    if session.pairing_token_expires_at and session.pairing_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Pairing token has expired")

    # Send OTP
    try:
        from app.services.otp_service_v2 import OTPServiceV2

        otp_service = OTPServiceV2()
        result = await otp_service.send_otp(req.phone)

        if not result.get('success'):
            return PairSessionResponse(
                paired=False,
                session_id=str(session.id),
                otp_sent=False,
                error=result.get('error', 'Failed to send OTP'),
            )

        _capture_event("checkin.pairing_started", None, {
            "session_id": str(session.id),
            "phone_masked": service.mask_phone(req.phone),
        })

        return PairSessionResponse(
            paired=False,
            session_id=str(session.id),
            otp_sent=True,
        )

    except Exception as e:
        logger.error(f"Error sending OTP: {e}", exc_info=True)
        return PairSessionResponse(
            paired=False,
            session_id=str(session.id),
            otp_sent=False,
            error="Failed to send verification code",
        )


@router.post("/pair/confirm", response_model=ConfirmPairingResponse)
async def confirm_pairing(
    req: ConfirmPairingRequest,
    db: Session = Depends(get_db),
):
    """
    Confirm OTP and complete pairing.

    Returns JWT access token for subsequent requests.
    Car browser polling will detect pairing is complete.
    """
    service = get_checkin_service()

    session = service.get_session_by_pairing_token(db, req.pairing_token)
    if not session:
        raise HTTPException(status_code=404, detail="Pairing token not found")

    # Check expiry
    if session.pairing_token_expires_at and session.pairing_token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Pairing token has expired")

    # Verify OTP and get/create user
    try:
        from app.services.auth.jwt_service import create_access_token
        from app.services.otp_service_v2 import OTPServiceV2

        otp_service = OTPServiceV2()

        # This will verify OTP and return/create user
        result = await otp_service.verify_otp_and_get_user(db, session.paired_phone or "", req.otp_code)

        if not result.get('success'):
            return ConfirmPairingResponse(
                confirmed=False,
                session_id=str(session.id),
                error=result.get('error', 'Invalid verification code'),
            )

        user = result.get('user')
        if not user:
            return ConfirmPairingResponse(
                confirmed=False,
                session_id=str(session.id),
                error="Failed to get user",
            )

        # Complete pairing
        updated_session = await service.complete_pairing(db, req.pairing_token, user)
        if not updated_session:
            return ConfirmPairingResponse(
                confirmed=False,
                session_id=str(session.id),
                error="Failed to complete pairing",
            )

        # Generate access token
        access_token = create_access_token(user.id)

        _capture_event("checkin.pairing_confirmed", user.id, {
            "session_id": str(session.id),
        })

        return ConfirmPairingResponse(
            confirmed=True,
            session_id=str(session.id),
            access_token=access_token,
        )

    except Exception as e:
        logger.error(f"Error confirming pairing: {e}", exc_info=True)
        return ConfirmPairingResponse(
            confirmed=False,
            session_id=str(session.id),
            error="Failed to verify code",
        )


# ─── Utility Endpoints ────────────────────────────────────────────────

@router.get("/code/{code}", response_model=SessionStatusResponse)
async def get_session_by_code(
    code: str,
    db: Session = Depends(get_db),
):
    """
    Look up session by arrival code.

    Used by merchants to verify a code.
    """
    service = get_checkin_service()

    session = service.get_session_by_code(db, code)
    if not session:
        raise HTTPException(status_code=404, detail="Code not found")

    return _session_to_status(session, db)
