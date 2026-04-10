"""
Console Auth — email OTP registration/login for the sponsor console.

POST /v1/console/auth/email-otp/start   → send 6-digit code
POST /v1/console/auth/email-otp/verify  → verify code, return JWT
"""

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..core.security import create_access_token
from ..db import get_db
from ..models import User, UserPreferences
from ..services.email_otp_service import EmailOTPService
from ..services.refresh_token_service import RefreshTokenService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/auth", tags=["console-auth"])


# ── Schemas ──────────────────────────────────────────────────

class EmailOTPStartRequest(BaseModel):
    email: EmailStr


class EmailOTPStartResponse(BaseModel):
    otp_sent: bool


class EmailOTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class EmailOTPVerifyResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


# ── Endpoints ────────────────────────────────────────────────

@router.post("/email-otp/start", response_model=EmailOTPStartResponse)
async def email_otp_start(
    payload: EmailOTPStartRequest,
    db: Session = Depends(get_db),
):
    """Send a 6-digit verification code to the given email."""
    EmailOTPService.send_code(db, payload.email)
    db.commit()
    return EmailOTPStartResponse(otp_sent=True)


@router.post("/email-otp/verify", response_model=EmailOTPVerifyResponse)
async def email_otp_verify(
    payload: EmailOTPVerifyRequest,
    db: Session = Depends(get_db),
):
    """Verify code, find-or-create user with super_admin role, return JWT."""
    email = payload.email.strip().lower()

    EmailOTPService.verify_code(db, email, payload.code)

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    is_new = False

    if user:
        # Existing user — preserve their current role (don't auto-upgrade)
        pass
    else:
        # New user — create as sponsor (not admin)
        user = User(
            public_id=str(uuid.uuid4()),
            email=email,
            auth_provider="email_otp",
            role_flags="sponsor",
            admin_role=None,
            is_active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserPreferences(user_id=user.id))
        is_new = True

    # Issue tokens
    role = "admin" if user.admin_role in ("super_admin", "admin") else "sponsor"
    access_token = create_access_token(
        user.public_id,
        auth_provider="email_otp",
        role=role,
    )
    refresh_plain, _ = RefreshTokenService.create_refresh_token(db, user)

    db.commit()
    db.refresh(user)

    logger.info(
        "Console email OTP verified email=%s user_id=%s new=%s",
        email, user.id, is_new,
    )

    return EmailOTPVerifyResponse(
        access_token=access_token,
        refresh_token=refresh_plain,
        user={
            "id": user.id,
            "public_id": user.public_id,
            "email": user.email,
            "admin_role": user.admin_role,
            "is_new": is_new,
        },
    )
