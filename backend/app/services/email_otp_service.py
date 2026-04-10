"""
Email OTP Service — generate and verify 6-digit codes for console email auth.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..core.email_sender import get_email_sender
from ..core.security import hash_password, verify_password
from ..models.email_otp_challenge import EmailOTPChallenge

logger = logging.getLogger(__name__)

# Rate limit: max 3 sends per email per 10 minutes
RATE_LIMIT_WINDOW_MINUTES = 10
RATE_LIMIT_MAX_SENDS = 3

# Code valid for 10 minutes
CODE_TTL_MINUTES = 10


class EmailOTPService:

    @staticmethod
    def send_code(db: Session, email: str) -> bool:
        """
        Generate a 6-digit OTP, store hashed, and email it.
        Returns True on success, raises on rate limit.
        """
        email = email.strip().lower()

        # Rate limit check
        window_start = datetime.utcnow() - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)
        recent_count = (
            db.query(func.count(EmailOTPChallenge.id))
            .filter(
                and_(
                    EmailOTPChallenge.email == email,
                    EmailOTPChallenge.created_at >= window_start,
                )
            )
            .scalar()
        )
        if recent_count >= RATE_LIMIT_MAX_SENDS:
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail="Too many code requests. Try again in a few minutes.")

        # Generate code
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hashed = hash_password(code)
        expires_at = datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES)

        challenge = EmailOTPChallenge(
            id=str(uuid.uuid4()),
            email=email,
            code_hash=code_hashed,
            expires_at=expires_at,
        )
        db.add(challenge)
        db.flush()

        # Send email
        sender = get_email_sender()
        sent = sender.send_email(
            to_email=email,
            subject="Your Nerava Console verification code",
            body_text=f"Your verification code is: {code}\n\nThis code expires in {CODE_TTL_MINUTES} minutes.",
            body_html=(
                f"<p>Your verification code is:</p>"
                f"<h1 style='letter-spacing:6px;font-family:monospace'>{code}</h1>"
                f"<p>This code expires in {CODE_TTL_MINUTES} minutes.</p>"
            ),
        )
        if not sent:
            logger.error("Failed to send email OTP to %s", email)
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail="Failed to send verification email")

        logger.info("Email OTP sent to %s", email)
        return True

    @staticmethod
    def verify_code(db: Session, email: str, code: str) -> bool:
        """
        Verify a 6-digit code for the given email.
        Returns True if valid, raises HTTPException otherwise.
        """
        email = email.strip().lower()

        # Find latest non-consumed, non-expired challenge for this email
        challenge = (
            db.query(EmailOTPChallenge)
            .filter(
                and_(
                    EmailOTPChallenge.email == email,
                    EmailOTPChallenge.consumed == False,
                    EmailOTPChallenge.expires_at > datetime.utcnow(),
                )
            )
            .order_by(EmailOTPChallenge.created_at.desc())
            .first()
        )

        if not challenge:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="No active verification code. Please request a new one.")

        # Check attempt limit
        if challenge.attempts >= challenge.max_attempts:
            challenge.consumed = True
            db.flush()
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail="Too many attempts. Please request a new code.")

        challenge.attempts += 1

        if not verify_password(code, challenge.code_hash):
            db.flush()
            remaining = challenge.max_attempts - challenge.attempts
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Invalid code. {remaining} attempts remaining.")

        # Success — consume the challenge
        challenge.consumed = True
        db.flush()

        logger.info("Email OTP verified for %s", email)
        return True
