"""
Email OTP provider — sends verification codes via email (SES/SendGrid/console).

Replaces Twilio SMS OTP ($0.05/verification) with AWS SES ($0.10/1000 emails,
free tier: 62K/month from EC2/App Runner). Essentially free.
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ...core.email_sender import get_email_sender
from ...core.security import hash_password, verify_password
from ...models.email_otp_challenge import EmailOTPChallenge
from .otp_provider import OTPProvider

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5


class EmailOTPProvider(OTPProvider):
    """
    Email-based OTP provider using the existing EmailOTPChallenge model
    and SES/SendGrid email sender infrastructure.

    Implements the same OTPProvider interface as Twilio providers,
    but the 'phone' parameter is treated as an email address.
    """

    def __init__(self, db: Optional[Session] = None):
        self._db = db
        logger.info("[OTP][Email] Email OTP provider initialized")

    def set_db(self, db: Session):
        """Set DB session for this request."""
        self._db = db

    async def send_otp(self, phone: str) -> bool:
        """
        Send OTP code to email address.

        Note: parameter is named 'phone' to match OTPProvider interface,
        but for this provider it contains an email address.
        """
        email = phone.strip().lower()
        db = self._db
        if not db:
            logger.error("[OTP][Email] No database session available")
            return False

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
            subject="Your Nerava verification code",
            body_text=f"Your verification code is: {code}\n\nThis code expires in {CODE_TTL_MINUTES} minutes.",
            body_html=(
                '<div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 400px; margin: 0 auto; padding: 24px;">'
                '<div style="text-align: center; margin-bottom: 24px;">'
                '<span style="font-size: 24px; font-weight: 700;">NERAVA</span>'
                '</div>'
                '<p style="color: #333; font-size: 16px;">Your verification code is:</p>'
                f'<h1 style="letter-spacing: 8px; font-family: monospace; font-size: 32px; text-align: center; '
                f'background: #f5f5f5; padding: 16px; border-radius: 12px; margin: 16px 0;">{code}</h1>'
                f'<p style="color: #666; font-size: 14px;">This code expires in {CODE_TTL_MINUTES} minutes.</p>'
                '</div>'
            ),
        )
        if not sent:
            logger.error("[OTP][Email] Failed to send email to %s", email)
            return False

        logger.info("[OTP][Email] Code sent to %s", email)
        return True

    async def verify_otp(self, phone: str, code: str) -> bool:
        """
        Verify OTP code for the given email.

        Note: parameter is named 'phone' to match OTPProvider interface,
        but for this provider it contains an email address.
        """
        email = phone.strip().lower()
        db = self._db
        if not db:
            logger.error("[OTP][Email] No database session available")
            return False

        # Find latest non-consumed, non-expired challenge
        from sqlalchemy import and_
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
            logger.warning("[OTP][Email] No active challenge for %s", email)
            return False

        # Check attempt limit
        if challenge.attempts >= MAX_ATTEMPTS:
            challenge.consumed = True
            db.flush()
            logger.warning("[OTP][Email] Max attempts exceeded for %s", email)
            return False

        challenge.attempts += 1

        if not verify_password(code, challenge.code_hash):
            db.flush()
            logger.warning("[OTP][Email] Invalid code for %s", email)
            return False

        # Success
        challenge.consumed = True
        db.flush()
        logger.info("[OTP][Email] Verified successfully for %s", email)
        return True
