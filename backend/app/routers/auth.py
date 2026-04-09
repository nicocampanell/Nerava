"""
Production Auth v1 Router
Implements Google SSO, Apple SSO, Phone OTP, refresh token rotation, /me, logout
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import create_access_token
from ..db import get_db
from ..dependencies.domain import get_current_user, get_current_user_optional
from ..models import User, UserPreferences
from ..services.analytics import get_analytics_client
from ..services.refresh_token_service import RefreshTokenService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ============================================
# Request/Response Models
# ============================================


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class LogoutResponse(BaseModel):
    ok: bool


class GoogleAuthRequest(BaseModel):
    id_token: str


class AppleAuthRequest(BaseModel):
    id_token: str


class OTPStartRequest(BaseModel):
    phone: str


class EmailOTPStartRequest(BaseModel):
    email: EmailStr


class OTPStartResponse(BaseModel):
    otp_sent: bool


class OTPVerifyRequest(BaseModel):
    phone: str
    code: str


class EmailOTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class UserMeResponse(BaseModel):
    public_id: str
    auth_provider: str
    email: Optional[str] = None
    phone: Optional[str] = None
    display_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================
# Core Auth Endpoints
# ============================================


@router.get("/me", response_model=UserMeResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """Get current user information"""
    return UserMeResponse(
        public_id=current_user.public_id,
        auth_provider=current_user.auth_provider,
        email=current_user.email,
        phone=current_user.phone,
        display_name=current_user.display_name,
        created_at=current_user.created_at,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(
    payload: RefreshRequest,
    db: Session = Depends(get_db),
):
    """
    Refresh access token using refresh token.
    Implements token rotation: old token is revoked, new token is issued.
    """
    plain_refresh_token = payload.refresh_token

    # Validate refresh token
    old_token = RefreshTokenService.validate_refresh_token(db, plain_refresh_token)
    if not old_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        )

    # Check if token was already revoked (reuse detection)
    if old_token.revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected",
            headers={"X-Error-Code": "refresh_reuse_detected"},
        )

    # Rotate token: revoke old, create new
    new_plain_token, new_refresh_token = RefreshTokenService.rotate_refresh_token(db, old_token)

    # Get user
    user = db.query(User).filter(User.id == old_token.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Create new access token
    access_token = create_access_token(user.public_id, auth_provider=user.auth_provider)

    db.commit()

    return RefreshResponse(
        access_token=access_token, refresh_token=new_plain_token, token_type="bearer"
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    payload: LogoutRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Logout: revoke refresh token(s).
    If refresh_token is provided, revoke that specific token.
    Otherwise, revoke all tokens for the current user.
    """
    if payload.refresh_token:
        # Revoke specific token
        token = RefreshTokenService.validate_refresh_token(db, payload.refresh_token)
        if token:
            RefreshTokenService.revoke_refresh_token(db, token)
            db.commit()
    elif current_user:
        # Revoke all tokens for current user
        RefreshTokenService.revoke_all_user_tokens(db, current_user.id)
        db.commit()

    return LogoutResponse(ok=True)


# ============================================
# Provider Auth Endpoints (to be implemented)
# ============================================


@router.post("/google", response_model=TokenResponse)
async def auth_google(
    payload: GoogleAuthRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticate with Google ID token.
    Priority 1 - must work end-to-end.
    """
    # Import here to avoid circular dependencies
    from ..services.google_auth import verify_google_id_token

    try:
        # Verify Google ID token
        google_user_info = verify_google_id_token(payload.id_token)

        # Extract user info
        email = google_user_info.get("email")
        provider_sub = google_user_info.get("sub")  # Google subject ID

        if not provider_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Google ID token: missing sub",
            )

        # Find or create user by (auth_provider, provider_sub)
        user = (
            db.query(User)
            .filter(User.auth_provider == "google", User.provider_sub == provider_sub)
            .first()
        )

        if not user:
            # Create new user
            import uuid

            user = User(
                public_id=str(uuid.uuid4()),
                email=email,
                auth_provider="google",
                provider_sub=provider_sub,
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPreferences(user_id=user.id))
            db.commit()
            db.refresh(user)

            # Emit driver_signed_up event (non-blocking)
            try:
                from ..events.domain import DriverSignedUpEvent
                from ..events.outbox import store_outbox_event

                event = DriverSignedUpEvent(
                    user_id=str(user.id),
                    email=email or "",
                    auth_provider="google",
                    created_at=datetime.utcnow(),
                )
                store_outbox_event(db, event)
            except Exception as e:
                logger.warning(f"Failed to emit driver_signed_up event: {e}")

        # Create tokens
        access_token = create_access_token(user.public_id, auth_provider=user.auth_provider)
        refresh_token_plain, refresh_token_model = RefreshTokenService.create_refresh_token(
            db, user
        )

        db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_plain,
            token_type="bearer",
            user={
                "public_id": user.public_id,
                "auth_provider": user.auth_provider,
                "email": user.email,
                "phone": user.phone,
                "name": user.display_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google authentication failed",
        )


@router.post("/apple", response_model=TokenResponse)
async def auth_apple(
    payload: AppleAuthRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticate with Apple ID token.
    Priority 2.
    """
    # Import here to avoid circular dependencies
    from ..services.apple_auth import verify_apple_id_token

    try:
        # Verify Apple ID token
        apple_user_info = verify_apple_id_token(payload.id_token)

        # Extract user info
        email = apple_user_info.get("email")
        provider_sub = apple_user_info.get("sub")  # Apple subject ID

        if not provider_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Apple ID token: missing sub",
            )

        # Find or create user by (auth_provider, provider_sub)
        user = (
            db.query(User)
            .filter(User.auth_provider == "apple", User.provider_sub == provider_sub)
            .first()
        )

        if not user:
            # Create new user
            import uuid

            user = User(
                public_id=str(uuid.uuid4()),
                email=email,
                auth_provider="apple",
                provider_sub=provider_sub,
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPreferences(user_id=user.id))
            db.commit()
            db.refresh(user)

            # Emit driver_signed_up event (non-blocking)
            try:
                from ..events.domain import DriverSignedUpEvent
                from ..events.outbox import store_outbox_event

                event = DriverSignedUpEvent(
                    user_id=str(user.id),
                    email=email or "",
                    auth_provider="apple",
                    created_at=datetime.utcnow(),
                )
                store_outbox_event(db, event)
            except Exception as e:
                logger.warning(f"Failed to emit driver_signed_up event: {e}")

        # Create tokens
        access_token = create_access_token(user.public_id, auth_provider=user.auth_provider)
        refresh_token_plain, refresh_token_model = RefreshTokenService.create_refresh_token(
            db, user
        )

        db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_plain,
            token_type="bearer",
            user={
                "public_id": user.public_id,
                "auth_provider": user.auth_provider,
                "email": user.email,
                "phone": user.phone,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Apple auth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Apple authentication failed",
        )


@router.post("/otp/start", response_model=OTPStartResponse)
async def otp_start(
    payload: OTPStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Start phone OTP flow: generate and send OTP code.
    Includes rate limiting per phone + IP and audit logging.
    """
    import logging

    from ..services.analytics import get_analytics_client
    from ..services.otp_service_v2 import OTPServiceV2

    logger = logging.getLogger(__name__)
    client_ip = request.client.host if request.client else "unknown"
    request_id = getattr(request.state, "request_id", None)

    try:
        # Use production-ready OTP service (handles rate limiting, audit, normalization)
        otp_sent = await OTPServiceV2.send_otp(
            db=db,
            phone=payload.phone,
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )

        # PostHog: Capture OTP start event
        analytics = get_analytics_client()
        from ..utils.phone import get_phone_last4

        phone_last4 = get_phone_last4(payload.phone)
        analytics.capture(
            event="server.driver.otp.start",
            distinct_id=f"phone_{payload.phone}",  # Anonymous ID until verified
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "phone_last4": phone_last4,
            },
        )

        return OTPStartResponse(otp_sent=otp_sent)
    except HTTPException as e:
        # PostHog: Capture OTP start failure (rate limit, etc.)
        analytics = get_analytics_client()
        from ..utils.phone import get_phone_last4

        phone_last4 = get_phone_last4(payload.phone)
        analytics.capture(
            event="server.driver.otp.start",
            distinct_id=f"phone_{payload.phone}",
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "phone_last4": phone_last4,
                "error": e.detail,
                "status_code": e.status_code,
            },
        )
        raise
    except Exception as e:
        logger.error(f"[Auth][OTP] OTP send failed: {str(e)}", exc_info=True)
        # PostHog: Capture exception
        analytics = get_analytics_client()
        from ..utils.phone import get_phone_last4

        phone_last4 = get_phone_last4(payload.phone)
        analytics.capture(
            event="server.driver.otp.start",
            distinct_id=f"phone_{payload.phone}",
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "phone_last4": phone_last4,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to send code. Try again later.",
        )


@router.post("/otp/verify", response_model=TokenResponse)
async def otp_verify(
    payload: OTPVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Verify OTP code and authenticate user.
    Creates user if phone number is new.
    Includes audit logging and role-based token creation.
    """
    import logging

    from ..services.analytics import get_analytics_client
    from ..services.otp_service_v2 import OTPServiceV2
    from ..utils.phone import get_phone_last4

    logger = logging.getLogger(__name__)
    client_ip = request.client.host if request.client else "unknown"
    request_id = getattr(request.state, "request_id", None)

    try:
        # Verify OTP using production-ready service (handles rate limiting, audit, normalization)
        phone = await OTPServiceV2.verify_otp(
            db=db,
            phone=payload.phone,
            code=payload.code,
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
        )

        # Find or create user by phone
        user = db.query(User).filter(User.phone == phone).first()
        is_new_user = False

        if not user:
            # Create new user with driver role
            import uuid

            user = User(
                public_id=str(uuid.uuid4()),
                phone=phone,
                auth_provider="phone",
                role_flags="driver",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPreferences(user_id=user.id))
            db.commit()
            db.refresh(user)
            is_new_user = True

        # Create tokens with role claim
        access_token = create_access_token(
            user.public_id, auth_provider=user.auth_provider, role="driver"
        )
        refresh_token_plain, refresh_token_model = RefreshTokenService.create_refresh_token(
            db, user
        )

        db.commit()

        # PostHog: Fire otp_verified event
        analytics = get_analytics_client()
        phone_last4 = get_phone_last4(phone)
        analytics.capture(
            event="otp_verified",
            distinct_id=user.public_id,
            request_id=request_id,
            user_id=user.public_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={"phone_last4": phone_last4, "is_new_user": is_new_user, "source": "driver"},
        )

        # HubSpot: Track OTP verify (only for new users - signup event handled in auth_domain)
        # Note: Login events are not tracked as lifecycle events per HubSpot design
        # Only lifecycle milestones are sent to HubSpot

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_plain,
            token_type="bearer",
            user={
                "public_id": user.public_id,
                "auth_provider": user.auth_provider,
                "email": user.email,
                "phone": user.phone,
                "name": user.display_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
        )

    except HTTPException as e:
        # PostHog: Capture OTP verify failure
        analytics = get_analytics_client()
        phone_last4 = get_phone_last4(payload.phone)
        analytics.capture(
            event="server.driver.otp.verify.fail",
            distinct_id=f"phone_{payload.phone}",
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "phone_last4": phone_last4,
                "error": e.detail,
                "status_code": e.status_code,
            },
        )
        raise
    except Exception as e:
        # PostHog: Capture OTP verify failure (exception)
        analytics = get_analytics_client()
        phone_last4 = get_phone_last4(payload.phone)
        analytics.capture(
            event="server.driver.otp.verify.fail",
            distinct_id=f"phone_{payload.phone}",
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "phone_last4": phone_last4,
                "error": str(e),
            },
        )
        logger.error(f"[Auth][OTP] OTP verify exception: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification service error. Please request a new code.",
        )


# ============================================
# Email OTP Endpoints (free via AWS SES)
# ============================================


@router.post("/email-otp/start", response_model=OTPStartResponse)
async def email_otp_start(
    payload: EmailOTPStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Start email OTP flow: generate and send OTP code via email.
    Uses AWS SES (free tier: 62K emails/month from App Runner).
    """
    import logging

    from ..services.email_otp_service import EmailOTPService

    logger = logging.getLogger(__name__)

    try:
        EmailOTPService.send_code(db, payload.email)
        db.commit()
        return OTPStartResponse(otp_sent=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Auth][EmailOTP] Send failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to send code. Try again later.",
        )


@router.post("/email-otp/verify", response_model=TokenResponse)
async def email_otp_verify(
    payload: EmailOTPVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Verify email OTP code and authenticate user.
    Creates user if email is new.
    """
    import logging

    from ..services.email_otp_service import EmailOTPService

    logger = logging.getLogger(__name__)

    try:
        EmailOTPService.verify_code(db, payload.email, payload.code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Auth][EmailOTP] Verify error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Verification service error. Please request a new code.",
        )

    # Find or create user by email
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    is_new_user = False

    if not user:
        import uuid

        user = User(
            public_id=str(uuid.uuid4()),
            email=email,
            auth_provider="email",
            role_flags="driver",
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        db.commit()
        db.refresh(user)
        is_new_user = True

        # Emit driver_signed_up event (non-blocking)
        try:
            from ..events.domain import DriverSignedUpEvent
            from ..events.outbox import store_outbox_event

            event = DriverSignedUpEvent(
                user_id=str(user.id),
                email=email,
                auth_provider="email",
                created_at=datetime.utcnow(),
            )
            store_outbox_event(db, event)
        except Exception as e:
            logger.warning(f"Failed to emit driver_signed_up event: {e}")

    # Create tokens
    access_token = create_access_token(
        user.public_id, auth_provider=user.auth_provider, role="driver"
    )
    refresh_token_plain, refresh_token_model = RefreshTokenService.create_refresh_token(db, user)

    db.commit()

    # PostHog
    analytics = get_analytics_client()
    analytics.capture(
        event="email_otp_verified",
        distinct_id=user.public_id,
        properties={"is_new_user": is_new_user, "source": "driver"},
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token_plain,
        token_type="bearer",
        user={
            "public_id": user.public_id,
            "auth_provider": user.auth_provider,
            "email": user.email,
            "phone": user.phone,
            "name": user.display_name,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    )


# ============================================
# Dev Mode Endpoints
# ============================================


@router.post("/dev/login", response_model=TokenResponse)
async def dev_login(
    db: Session = Depends(get_db),
):
    """
    Dev mode login - automatically logs in as dev@nerava.local user.
    Only available when ENV is explicitly set to dev/local, or DEMO_MODE is enabled.
    NEVER accessible in production.
    """
    import logging

    logger = logging.getLogger("nerava")

    env_lower = settings.ENV.lower() if settings.ENV else ""

    # SECURITY: Block dev login in production — no exceptions
    if env_lower in ("prod", "production", "staging"):
        logger.warning(f"Dev login rejected — ENV={settings.ENV}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    is_dev_env = env_lower in ("dev", "local", "test")

    if not settings.DEMO_MODE and not is_dev_env:
        logger.warning(f"Dev login rejected — ENV={settings.ENV}, DEMO_MODE={settings.DEMO_MODE}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    logger.info(f"Dev login allowed — ENV={settings.ENV}, DEMO_MODE={settings.DEMO_MODE}")

    try:
        # Find or create dev user
        dev_email = "dev@nerava.local"
        # Try to find by email first (for existing dev users)
        user = db.query(User).filter(User.email == dev_email).first()

        # If not found by email, try by auth_provider + provider_sub
        if not user:
            user = (
                db.query(User)
                .filter(User.auth_provider == "dev", User.provider_sub == "dev-user-001")
                .first()
            )

        if not user:
            # Create dev user
            import uuid

            logger.info("Creating new dev user")
            user = User(
                public_id=str(uuid.uuid4()),
                email=dev_email,
                auth_provider="dev",
                provider_sub="dev-user-001",  # Unique identifier for dev user
                is_active=True,
                password_hash=None,  # Explicitly set to None for OAuth/dev users
            )
            db.add(user)
            db.flush()

            # Create user preferences
            try:
                preferences = UserPreferences(user_id=user.id)
                db.add(preferences)
            except Exception as pref_error:
                logger.warning(
                    f"Could not create user preferences (may already exist): {pref_error}"
                )

            db.commit()
            db.refresh(user)
            logger.info(f"Created dev user: {user.public_id}")
        else:
            logger.info(f"Found existing dev user: {user.public_id}")

        # Create tokens
        access_token = create_access_token(user.public_id, auth_provider=user.auth_provider)
        refresh_token_plain, refresh_token_model = RefreshTokenService.create_refresh_token(
            db, user
        )

        db.commit()

        logger.info("Dev login successful - tokens created")

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_plain,
            token_type="bearer",
            user={
                "public_id": user.public_id,
                "auth_provider": user.auth_provider,
                "email": user.email,
                "phone": user.phone,
                "name": user.display_name,
                "created_at": user.created_at.isoformat() if user.created_at else None,
            },
        )
    except Exception as e:
        logger.error(f"Dev login error: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Dev login failed: {str(e)}"
        )


# ============================================
# Legacy Endpoints (behind DEMO_MODE flag)
# ============================================


@router.post("/register")
def register_legacy(payload, db: Session = Depends(get_db)):
    """Legacy registration endpoint - only available in DEMO_MODE"""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not available")
    # Keep old implementation for demo mode
    from ..core.security import create_access_token, hash_password
    from ..schemas import Token

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        return Token(access_token=create_access_token(existing.public_id))

    import uuid

    user = User(
        public_id=str(uuid.uuid4()),
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    db.add(UserPreferences(user_id=user.id))
    db.commit()
    return Token(access_token=create_access_token(user.public_id))


@router.post("/login")
def login_legacy(form, db: Session = Depends(get_db)):
    """Legacy login endpoint - only available in DEMO_MODE"""
    if not settings.DEMO_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not available")
    # Keep old implementation for demo mode
    from ..core.security import create_access_token, verify_password
    from ..schemas import Token

    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return Token(access_token=create_access_token(user.public_id))
