"""Merchant claim flow endpoints - Email + Phone verification"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..core.config import settings

# NOTE: OTPServiceV2 imported lazily inside functions to avoid triggering
# Twilio SDK import at startup, which causes issues in App Runner
from ..core.email_sender import get_email_sender
from ..core.security import create_access_token
from ..db import get_db
from ..models import ClaimSession, DomainMerchant, User, UserPreferences
from ..utils.phone import normalize_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/merchant/claim", tags=["merchant-claim"])

MAGIC_LINK_EXPIRY_MINUTES = 15


# Request/Response Models
class ClaimStartRequest(BaseModel):
    merchant_id: str
    email: EmailStr
    phone: str
    business_name: str


class ClaimStartResponse(BaseModel):
    session_id: str
    message: str


class VerifyPhoneRequest(BaseModel):
    session_id: str
    code: str


class VerifyPhoneResponse(BaseModel):
    phone_verified: bool
    message: str


class SendMagicLinkRequest(BaseModel):
    session_id: str


class SendMagicLinkResponse(BaseModel):
    email_sent: bool
    message: str


class VerifyMagicLinkResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict
    merchant_id: str


# Endpoints
@router.post("/start", response_model=ClaimStartResponse)
async def start_claim(
    request: ClaimStartRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """
    Step 1: Start claim process
    - Validate merchant exists and is not claimed
    - Create claim session
    - Send OTP to phone
    """
    # Validate merchant exists
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == request.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    # Check if already claimed
    if merchant.owner_user_id:
        raise HTTPException(status_code=400, detail="Merchant already claimed")

    # Normalize phone
    try:
        normalized_phone = normalize_phone(request.phone)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid phone number: {str(e)}")

    # Check for existing pending session
    existing_session = db.query(ClaimSession).filter(
        ClaimSession.merchant_id == request.merchant_id,
        ClaimSession.completed_at.is_(None)
    ).first()

    if existing_session:
        # Update existing session
        existing_session.email = request.email.lower().strip()
        existing_session.phone = normalized_phone
        existing_session.business_name = request.business_name
        existing_session.phone_verified = False
        existing_session.email_verified = False
        existing_session.magic_link_token = None
        existing_session.magic_link_expires_at = None
        session = existing_session
    else:
        # Create new session
        session = ClaimSession(
            id=str(uuid.uuid4()),
            merchant_id=request.merchant_id,
            email=request.email.lower().strip(),
            phone=normalized_phone,
            business_name=request.business_name,
        )
        db.add(session)

    db.commit()
    db.refresh(session)

    # Send OTP (lazy import to avoid Twilio SDK import at startup)
    from ..services.otp_service_v2 import OTPServiceV2

    try:
        client_ip = http_request.client.host if http_request.client else "unknown"
        request_id = getattr(http_request.state, "request_id", None)
        user_agent = http_request.headers.get("user-agent")

        await OTPServiceV2.send_otp(
            db=db,
            phone=normalized_phone,
            request_id=request_id,
            ip=client_ip,
            user_agent=user_agent,
        )
        logger.info(f"[ClaimFlow] OTP sent to {normalized_phone[-4:]} for session {session.id}")
    except Exception as e:
        logger.error(f"[ClaimFlow] Failed to send OTP: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send verification code")

    return ClaimStartResponse(
        session_id=str(session.id),
        message="Verification code sent to your phone"
    )


@router.post("/verify-phone", response_model=VerifyPhoneResponse)
async def verify_phone(
    request: VerifyPhoneRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """
    Step 2: Verify phone OTP
    """
    session = db.query(ClaimSession).filter(ClaimSession.id == request.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.completed_at:
        raise HTTPException(status_code=400, detail="Session already completed")

    # Verify OTP (lazy import to avoid Twilio SDK import at startup)
    from ..services.otp_service_v2 import OTPServiceV2

    try:
        client_ip = http_request.client.host if http_request.client else "unknown"
        request_id = getattr(http_request.state, "request_id", None)
        user_agent = http_request.headers.get("user-agent")

        normalized_phone = await OTPServiceV2.verify_otp(
            db=db,
            phone=session.phone,
            code=request.code,
            request_id=request_id,
            ip=client_ip,
            user_agent=user_agent,
        )
        
        # Verify phone matches session
        if normalized_phone != session.phone:
            raise HTTPException(status_code=400, detail="Phone number mismatch")

        session.phone_verified = True
        db.commit()

        logger.info(f"[ClaimFlow] Phone verified for session {session.id}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ClaimFlow] Phone verification failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid verification code")

    return VerifyPhoneResponse(
        phone_verified=True,
        message="Phone verified successfully"
    )


@router.post("/send-magic-link", response_model=SendMagicLinkResponse)
async def send_magic_link(
    request: SendMagicLinkRequest,
    http_request: Request,
    db: Session = Depends(get_db),
):
    """
    Step 3: Send magic link email (requires phone verified)
    """
    session = db.query(ClaimSession).filter(ClaimSession.id == request.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.completed_at:
        raise HTTPException(status_code=400, detail="Session already completed")

    if not session.phone_verified:
        raise HTTPException(status_code=400, detail="Phone not verified")

    # Generate magic link token
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)

    session.magic_link_token = token
    session.magic_link_expires_at = expires_at
    db.commit()

    # Send email
    merchant_portal_url = settings.FRONTEND_URL.rstrip("/")
    if "/merchant" not in merchant_portal_url:
        merchant_portal_url = f"{merchant_portal_url}/merchant"
    magic_link_url = f"{merchant_portal_url}/claim/verify?token={token}"

    email_sender = get_email_sender()
    subject = f"Complete your {session.business_name} claim on Nerava"
    
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2>Complete Your Business Claim</h2>
        <p>You're almost done claiming <strong>{session.business_name}</strong> on Nerava!</p>
        <p>Click the button below to complete your claim and access your merchant dashboard:</p>
        <p style="text-align: center; margin: 30px 0;">
            <a href="{magic_link_url}"
               style="background-color: #10B981; color: white; padding: 12px 24px;
                      text-decoration: none; border-radius: 6px; font-weight: bold;">
                Complete Claim
            </a>
        </p>
        <p style="color: #666; font-size: 14px;">
            This link expires in 15 minutes. If you didn't request this, please ignore this email.
        </p>
        <p style="color: #999; font-size: 12px;">— The Nerava Team</p>
    </body>
    </html>
    """
    
    body_text = f"""Complete Your Business Claim

You're almost done claiming {session.business_name} on Nerava!

Click this link to complete your claim:
{magic_link_url}

This link expires in 15 minutes. If you didn't request this, please ignore this email.

— The Nerava Team
"""

    try:
        email_sender.send_email(
            to_email=session.email,
            subject=subject,
            body_text=body_text,
            body_html=html_content,
        )
        logger.info(f"[ClaimFlow] Magic link sent to {session.email} for session {session.id}")
    except Exception as e:
        logger.error(f"[ClaimFlow] Failed to send email: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send email")

    return SendMagicLinkResponse(
        email_sent=True,
        message=f"Magic link sent to {session.email}"
    )


@router.get("/verify-magic-link", response_model=VerifyMagicLinkResponse)
async def verify_magic_link(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Step 4: Verify magic link and create merchant account
    """
    # Find session by token
    session = db.query(ClaimSession).filter(ClaimSession.magic_link_token == token).first()

    if not session:
        raise HTTPException(status_code=400, detail="Invalid or expired link")

    if session.completed_at:
        raise HTTPException(status_code=400, detail="Link already used")

    if session.magic_link_expires_at and session.magic_link_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Link expired")

    if not session.phone_verified:
        raise HTTPException(status_code=400, detail="Phone not verified")

    # Check if user exists
    user = db.query(User).filter(
        (User.email == session.email) | (User.phone == session.phone)
    ).first()

    if not user:
        # Create new user
        from ..core.security import hash_password
        placeholder_password = "magic-link-user-no-password"
        user = User(
            email=session.email,
            phone=session.phone,
            password_hash=hash_password(placeholder_password),
            role_flags="merchant_admin",
            is_active=True,
            auth_provider="local",
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        logger.info(f"[ClaimFlow] Created merchant user {user.id} for {session.email}")
    else:
        # Update existing user
        if "merchant_admin" not in (user.role_flags or "").split(","):
            current_roles = [r.strip() for r in (user.role_flags or "").split(",") if r.strip()]
            current_roles.append("merchant_admin")
            user.role_flags = ",".join(current_roles)
        # Ensure phone/email are set
        if not user.phone:
            user.phone = session.phone
        if not user.email:
            user.email = session.email
        logger.info(f"[ClaimFlow] Updated existing user {user.id} with merchant_admin role")

    # Claim the merchant
    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == session.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")
    
    merchant.owner_user_id = user.id
    merchant.status = "active"  # Activate merchant

    # Mark session complete
    session.email_verified = True
    session.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(user)

    # Generate token
    access_token = create_access_token(
        subject=user.public_id,
        auth_provider=user.auth_provider,
        role="merchant"
    )

    logger.info(f"[ClaimFlow] Merchant {session.merchant_id} claimed by user {user.id}")

    return VerifyMagicLinkResponse(
        access_token=access_token,
        user={
            "id": str(user.id),
            "public_id": user.public_id,
            "email": user.email,
            "phone": user.phone,
            "role_flags": user.role_flags,
        },
        merchant_id=str(session.merchant_id)
    )


@router.get("/session/{session_id}")
async def get_session_status(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Get claim session status"""
    session = db.query(ClaimSession).filter(ClaimSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": str(session.id),
        "business_name": session.business_name,
        "phone_verified": session.phone_verified,
        "email_verified": session.email_verified,
        "completed": session.completed_at is not None,
    }




