"""
Domain Charge Party MVP Auth Router
Extends existing auth with role-based access and session management
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email_sender import get_email_sender
from app.core.env import is_local_env
from app.core.security import hash_password, verify_password
from app.db import get_db
from app.dependencies.feature_flags import require_google_oauth
from app.dependencies_domain import (
    get_current_user,
)
from app.models import User, UserPreferences
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Mask email for logging: j***@example.com"""
    try:
        local, domain = email.split("@", 1)
        return f"{local[0]}***@{domain}" if local else f"***@{domain}"
    except (ValueError, IndexError):
        return "***"


def _should_use_secure_cookie() -> bool:
    """
    Determine if cookies should use Secure flag (P0-5: security hardening).

    Returns True if:
    - Not in local environment, OR
    - In local environment but using HTTPS (FRONTEND_URL starts with https:// or HTTPS=true)
    """
    if not is_local_env():
        return True  # Always secure in non-local

    # In local, check if HTTPS is being used
    frontend_url = getattr(settings, "FRONTEND_URL", None) or os.getenv("FRONTEND_URL", "") or ""
    return frontend_url.startswith("https://") or os.getenv("HTTPS", "").lower() == "true"


router = APIRouter(prefix="/v1/auth", tags=["auth-v1"])


# Import OTP handlers from auth router for aliases
from app.routers.auth import (
    EmailOTPStartRequest,
    EmailOTPVerifyRequest,
    OTPStartRequest,
    OTPStartResponse,
    OTPVerifyRequest,
    TokenResponse,
    email_otp_start,
    email_otp_verify,
    otp_start,
    otp_verify,
)


@router.post("/otp/start", response_model=OTPStartResponse, include_in_schema=True)
async def otp_start_v1_alias(
    payload: OTPStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Alias for /auth/otp/start to support /v1/auth/otp/start path"""
    return await otp_start(payload, request, db)


@router.post("/otp/verify", response_model=TokenResponse, include_in_schema=True)
async def otp_verify_v1_alias(
    payload: OTPVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Alias for /auth/otp/verify to support /v1/auth/otp/verify path"""
    return await otp_verify(payload, request, db)


@router.post("/email-otp/start", response_model=OTPStartResponse, include_in_schema=True)
async def email_otp_start_v1_alias(
    payload: EmailOTPStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Email OTP start — free via AWS SES"""
    return await email_otp_start(payload, request, db)


@router.post("/email-otp/verify", include_in_schema=True)
async def email_otp_verify_v1_alias(
    payload: EmailOTPVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Email OTP verify — free via AWS SES"""
    return await email_otp_verify(payload, request, db)


# Helper Functions
def create_magic_link_token(user_id: int, email: str) -> str:
    """Create a time-limited magic link token (expires in 15 minutes)"""
    expires_delta = timedelta(minutes=15)
    expire = datetime.utcnow() + expires_delta

    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": "magic_link",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# Request/Response Models
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    role_flags: Optional[str]
    linked_merchant: Optional[dict] = None


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerify(BaseModel):
    token: str


@router.post("/register", response_model=TokenResponse)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user (driver or merchant_admin)"""
    try:
        roles = ["driver"]
        user = AuthService.register_user(
            db=db,
            email=request.email,
            password=request.password,
            display_name=request.display_name,
            roles=roles,
        )

        token = AuthService.create_session_token(user)

        # P3: HubSpot tracking (dry run)
        try:
            from app.events.hubspot_adapter import adapt_user_signup_event
            from app.services.hubspot import track_event

            hubspot_payload = adapt_user_signup_event(
                {
                    "user_id": str(user.id),
                    "email": user.email,
                    "role_flags": user.role_flags,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                }
            )
            track_event(db, "user_signup", hubspot_payload)
            db.commit()
        except Exception as e:
            # Don't fail registration if HubSpot tracking fails
            import logging

            logging.getLogger(__name__).warning(f"HubSpot tracking failed: {e}")

        response = TokenResponse(access_token=token)
        return response
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        import traceback

        error_detail = str(e)
        error_traceback = traceback.format_exc()
        logger.error(f"Registration failed: {error_detail}\n{error_traceback}")
        # Include more detail in response for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed. Please try again.",
        )


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db), response: Response = None):
    """Login user and return JWT token (accepts JSON body)"""
    try:
        user = AuthService.authenticate_user(db, request.email, request.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
            )

        token = AuthService.create_session_token(user)

        # Set HTTP-only cookie for better security (P0-5: environment-aware secure flag)
        if response:
            response.set_cookie(
                key="access_token",
                value=token,
                httponly=True,
                secure=_should_use_secure_cookie(),
                samesite="lax",
                max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )

        return TokenResponse(access_token=token)
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        error_detail = str(e)
        error_traceback = traceback.format_exc()
        logger.error(f"Login failed: {error_detail}\n{error_traceback}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed. Please try again.",
        )


@router.post("/logout")
def logout(response: Response):
    """Logout user (clear cookie)"""
    response.delete_cookie(key="access_token")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Get current user info including linked merchant if merchant_admin"""
    linked_merchant = None

    if AuthService.has_role(user, "merchant_admin"):
        merchant = AuthService.get_user_merchant(db, user.id)
        if merchant:
            linked_merchant = {
                "id": merchant.id,
                "name": merchant.name,
                "nova_balance": merchant.nova_balance,
            }

    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role_flags=user.role_flags,
        linked_merchant=linked_merchant,
    )


@router.post("/magic_link/request")
async def request_magic_link(
    payload: MagicLinkRequest,
    db: Session = Depends(get_db),
):
    """
    Request a magic link for email-only authentication.

    - Looks up or creates user (without password)
    - Generates time-limited token
    - Sends email with magic link (console logger for dev)
    """
    try:
        email = payload.email.lower().strip()

        logger.info(f"[Auth][MagicLink] Request for {_mask_email(email)}")

        # Lookup or create user (without password requirement)
        user = db.query(User).filter(User.email == email).first()

        if not user:
            try:
                # Create new user with placeholder password (magic-link only)
                # Use a dummy password hash - user can set real password later if needed
                placeholder_password = "magic-link-user-no-password"
                user = User(
                    email=email,
                    password_hash=hash_password(placeholder_password),
                    is_active=True,
                    auth_provider="local",  # Required field with default, but explicit is safer
                )
                db.add(user)
                db.flush()
                db.add(UserPreferences(user_id=user.id))
                db.commit()
                db.refresh(user)
                logger.info(f"[Auth][MagicLink] Created new user_id={user.id}")
            except Exception as e:
                db.rollback()
                logger.error(f"[Auth][MagicLink] Failed to create user: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create user. Please try again.",
                )

        # Generate magic link token
        magic_token = create_magic_link_token(user.id, email)

        # Get frontend URL from settings (default to localhost:8001/app for mobile)
        frontend_url = settings.FRONTEND_URL.rstrip("/")
        # If frontend_url doesn't include /app/, add it for mobile app hash routing
        if "/app" not in frontend_url:
            frontend_url = f"{frontend_url}/app"
        magic_link_url = f"{frontend_url}/#/auth/magic?token={magic_token}"

        # Send email via email sender abstraction
        email_sender = get_email_sender()
        email_sender.send_email(
            to_email=email,
            subject="Sign in to Nerava",
            body_text=f"Click this link to sign in to Nerava:\n\n{magic_link_url}\n\nThis link expires in 15 minutes.\n\nIf you didn't request this link, you can safely ignore this email.",
            body_html=f"""
            <html>
            <body>
                <h2>Sign in to Nerava</h2>
                <p>Click this link to sign in:</p>
                <p><a href="{magic_link_url}">{magic_link_url}</a></p>
                <p><small>This link expires in 15 minutes.</small></p>
                <p><small>If you didn't request this link, you can safely ignore this email.</small></p>
            </body>
            </html>
            """,
        )

        # In dev/staging or when DEBUG_RETURN_MAGIC_LINK is enabled, log and return the link
        if not settings.is_prod or settings.DEBUG_RETURN_MAGIC_LINK:
            # CRITICAL: Log the magic link URL so it appears in Railway logs for easy copy-paste
            logger.info("MAGIC_LINK DEBUG URL for %s: %s", email, magic_link_url)
            print(f"MAGIC_LINK DEBUG URL for {email}: {magic_link_url}", flush=True)
            return {
                "message": "Magic link generated (DEBUG MODE)",
                "email": email,
                "magic_link_url": magic_link_url,
            }

        # Production behavior: don't expose token in response
        # But still log it in non-prod for debugging
        if not settings.is_prod:
            logger.info("MAGIC_LINK DEBUG URL for %s: %s", email, magic_link_url)
            print(f"MAGIC_LINK DEBUG URL for {email}: {magic_link_url}", flush=True)

        return {"message": "Magic link sent to your email", "email": email}
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        error_detail = str(e)
        error_traceback = traceback.format_exc()
        logger.error(f"[Auth][MagicLink] Request failed: {error_detail}\n{error_traceback}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send magic link. Please try again.",
        )


@router.post("/magic_link/verify", response_model=TokenResponse)
async def verify_magic_link(
    payload: MagicLinkVerify,
    db: Session = Depends(get_db),
    response: Response = None,
):
    """
    Verify a magic link token and create a session.

    - Verifies token signature and expiration
    - Checks token purpose is "magic_link"
    - Creates access token (same format as password login)
    - Returns TokenResponse for session creation
    """
    token = payload.token

    try:
        # Decode and verify token
        payload_data = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )

        # Verify token purpose
        if payload_data.get("purpose") != "magic_link":
            logger.warning("[Auth][MagicLink] Verify failed: Invalid token purpose")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token purpose",
            )

        # Get user ID from token
        user_id_str = payload_data.get("sub")
        if not user_id_str:
            logger.warning("[Auth][MagicLink] Verify failed: Missing user ID in token")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token: missing user ID",
            )

        user_id = int(user_id_str)

        # Verify user exists
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.warning(f"[Auth][MagicLink] Verify failed: User not found (user_id={user_id})")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Create access token using AuthService (consistent with login/register)
        access_token = AuthService.create_session_token(user)

        # Set HTTP-only cookie for better security (consistent with login, P0-5: environment-aware secure flag)
        if response:
            response.set_cookie(
                key="access_token",
                value=access_token,
                httponly=True,
                secure=_should_use_secure_cookie(),
                samesite="lax",
                max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            )

        logger.info(f"[Auth][MagicLink] Verify success for user_id={user.id}")
        return TokenResponse(access_token=access_token)

    except jwt.ExpiredSignatureError:
        logger.warning("[Auth][MagicLink] Verify failed: Token expired")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Magic link has expired. Please request a new one.",
        )
    except jwt.JWTError as e:
        logger.warning(f"[Auth][MagicLink] Verify failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid magic link token: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback

        error_detail = str(e)
        error_traceback = traceback.format_exc()
        logger.error(f"[Auth][MagicLink] Verify failed: {error_detail}\n{error_traceback}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Magic link verification failed. Please try again.",
        )


# Merchant Auth Endpoints
class MerchantGoogleAuthRequest(BaseModel):
    id_token: str
    place_id: Optional[str] = None  # Google Business Profile place ID


@router.post(
    "/merchant/google", response_model=TokenResponse, dependencies=[Depends(require_google_oauth)]
)
async def merchant_google_auth(
    payload: MerchantGoogleAuthRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Authenticate merchant with Google Business Profile SSO.
    Verifies ID token, checks GBP access, and creates merchant user with role=merchant.
    """
    from ..core.config import settings
    from ..core.security import create_access_token
    from ..services.analytics import get_analytics_client
    from ..services.auth.audit import AuditService
    from ..services.auth.google_oauth import GoogleOAuthService

    client_ip = request.client.host if request.client else "unknown"
    request_id = getattr(request.state, "request_id", None)

    try:
        # Check if mock mode is enabled (for backward compatibility)
        mock_gbp_mode = os.getenv("MOCK_GBP_MODE", "false").lower() == "true"

        if mock_gbp_mode:
            # Mock mode: accept any token, create/return merchant user
            logger.info("[Auth][Merchant] Mock GBP mode enabled")
            email = f"merchant-{payload.place_id or 'mock'}@example.com"
            provider_sub = f"mock_gbp_{payload.place_id or 'default'}"
            locations = []
        else:
            # Verify Google ID token and check GBP access
            google_user_info, locations = await GoogleOAuthService.verify_and_check_gbp(
                payload.id_token,
                access_token=None,  # TODO: If using code exchange flow, pass access_token here
            )
            email = google_user_info.get("email")
            provider_sub = google_user_info.get("sub")

            # Check GBP access if required
            if settings.GOOGLE_GBP_REQUIRED and not mock_gbp_mode:
                if not locations:
                    email_domain = email.split("@")[-1] if email else "unknown"
                    AuditService.log_merchant_gbp_access_denied(
                        request_id=request_id,
                        email_domain=email_domain,
                        ip=client_ip,
                        user_agent=request.headers.get("user-agent"),
                        env=settings.ENV,
                        reason="No GBP locations found",
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Google Business Profile access required. Please ensure you have access to at least one business location.",
                    )

                # Log GBP access granted
                email_domain = email.split("@")[-1] if email else "unknown"
                AuditService.log_merchant_gbp_access_granted(
                    request_id=request_id,
                    email_domain=email_domain,
                    ip=client_ip,
                    user_agent=request.headers.get("user-agent"),
                    env=settings.ENV,
                    location_count=len(locations),
                )

        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email not found in Google token"
            )

        # Find or create merchant user
        user = db.query(User).filter(User.email == email, User.auth_provider == "google").first()

        is_new_user = False
        if not user:
            # Create new merchant user
            import uuid

            user = User(
                public_id=str(uuid.uuid4()),
                email=email,
                auth_provider="google",
                provider_sub=provider_sub,
                role_flags="merchant_admin",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserPreferences(user_id=user.id))
            db.commit()
            db.refresh(user)
            is_new_user = True
            logger.info(f"[Auth][Merchant] Created new merchant user_id={user.public_id}")
        else:
            # Update provider_sub if changed
            if user.provider_sub != provider_sub:
                user.provider_sub = provider_sub
                db.commit()

        # Create access token with role=merchant
        access_token = create_access_token(
            user.public_id, auth_provider=user.auth_provider, role="merchant"
        )

        # Audit log: success
        email_domain = email.split("@")[-1] if email else "unknown"
        AuditService.log_merchant_sso_login_success(
            request_id=request_id,
            email_domain=email_domain,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            env=settings.ENV,
            user_id=user.public_id,
        )

        # PostHog: Capture merchant SSO login success
        analytics = get_analytics_client()
        analytics.capture(
            event="server.merchant.sso.login.success",
            distinct_id=user.public_id,
            request_id=request_id,
            user_id=user.public_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "email_domain": email_domain,
                "is_new_user": is_new_user,
                "location_count": len(locations) if locations else 0,
            },
        )

        logger.info(f"[Auth][Merchant] Google auth success for merchant user_id={user.public_id}")
        return TokenResponse(access_token=access_token)

    except HTTPException:
        # Audit log: failure
        email_domain = "unknown"
        try:
            # Try to extract email from token for logging
            from ..services.auth.google_oauth import GoogleOAuthService

            google_user_info, _ = await GoogleOAuthService.verify_and_check_gbp(payload.id_token)
            email_domain = google_user_info.get("email", "unknown").split("@")[-1]
        except:
            pass

        AuditService.log_merchant_sso_login_fail(
            request_id=request_id,
            email_domain=email_domain,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            env=settings.ENV,
            error="HTTPException",
        )
        raise
    except Exception as e:
        logger.error(f"[Auth][Merchant] Google auth failed: {str(e)}", exc_info=True)

        # Audit log: failure
        email_domain = "unknown"
        AuditService.log_merchant_sso_login_fail(
            request_id=request_id,
            email_domain=email_domain,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            env=settings.ENV,
            error=str(e),
        )

        # PostHog: Capture failure
        analytics = get_analytics_client()
        analytics.capture(
            event="server.merchant.sso.login.fail",
            distinct_id="unknown",
            request_id=request_id,
            ip=client_ip,
            user_agent=request.headers.get("user-agent"),
            properties={
                "error": str(e),
            },
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Merchant Google authentication failed. Please try again.",
        )


# Admin Auth Endpoints
class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/admin/login", response_model=TokenResponse)
async def admin_login(
    payload: AdminLoginRequest,
    db: Session = Depends(get_db),
):
    """
    Admin login endpoint (email/password).
    Requires admin role flag.
    """
    # Find user by email
    user = db.query(User).filter(User.email == payload.email).first()

    if not user:
        logger.warning(f"[Auth][Admin] Login failed: User not found ({_mask_email(payload.email)})")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Check password
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        logger.warning(
            f"[Auth][Admin] Login failed: Invalid password ({_mask_email(payload.email)})"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )

    # Check admin role
    if not user.role_flags or "admin" not in user.role_flags:
        logger.warning(f"[Auth][Admin] Login failed: Not an admin ({_mask_email(payload.email)})")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    # Create access token
    access_token = AuthService.create_session_token(user)

    logger.info(f"[Auth][Admin] Login success for admin user_id={user.id}")
    return TokenResponse(access_token=access_token)


class AdminResetPasswordRequest(BaseModel):
    email: EmailStr
    new_password: str
    secret: str


@router.post("/admin/reset-password")
async def admin_reset_password(
    payload: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Reset admin password. Requires JWT_SECRET as the secret field for authorization.
    """
    from app.core.config import settings

    if payload.secret != settings.JWT_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid secret")

    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(payload.new_password)
    db.commit()
    logger.info(f"[Auth][Admin] Password reset for {payload.email}")
    return {"status": "ok", "message": "Password updated"}


@router.post("/admin/google", response_model=TokenResponse)
async def admin_google_auth(
    payload: MerchantGoogleAuthRequest,  # Reuse same model
    db: Session = Depends(get_db),
):
    """
    Admin Google SSO authentication.
    Requires admin role flag.
    """
    from ..services.google_auth import verify_google_id_token

    try:
        # Verify Google ID token
        google_user_info = verify_google_id_token(payload.id_token)
        email = google_user_info.get("email")
        provider_sub = google_user_info.get("sub")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Email not found in Google token"
            )

        # Find user
        user = db.query(User).filter(User.email == email, User.auth_provider == "google").first()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found"
            )

        # Check admin role
        if not user.role_flags or "admin" not in user.role_flags:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
            )

        # Create access token
        access_token = AuthService.create_session_token(user)

        logger.info(f"[Auth][Admin] Google auth success for admin user_id={user.id}")
        return TokenResponse(access_token=access_token)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Auth][Admin] Google auth failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin Google authentication failed. Please try again.",
        )
