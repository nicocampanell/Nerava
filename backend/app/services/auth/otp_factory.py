"""
OTP provider factory
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ...core.config import settings
from .email_otp_provider import EmailOTPProvider
from .otp_provider import OTPProvider
from .stub_provider import StubOTPProvider
from .twilio_sms import TwilioSMSProvider
from .twilio_verify import TwilioVerifyProvider

logger = logging.getLogger(__name__)

_provider_instance: Optional[OTPProvider] = None


def get_otp_provider(db: Optional[Session] = None) -> OTPProvider:
    """
    Get OTP provider instance based on configuration.

    Args:
        db: Database session (required for twilio_sms and email providers)

    Returns:
        OTPProvider instance
    """
    global _provider_instance

    provider_type = settings.OTP_PROVIDER.lower()

    if provider_type == "twilio_verify":
        if _provider_instance is None or not isinstance(_provider_instance, TwilioVerifyProvider):
            try:
                _provider_instance = TwilioVerifyProvider()
                logger.info("[OTP] Using Twilio Verify provider")
            except ValueError as e:
                logger.error(f"[OTP] Failed to initialize Twilio Verify: {e}")
                raise
        return _provider_instance

    elif provider_type == "twilio_sms":
        if db is None:
            raise ValueError("Database session required for Twilio SMS provider")
        # Create new instance per request (needs DB session)
        try:
            return TwilioSMSProvider(db)
        except ValueError as e:
            logger.error(f"[OTP] Failed to initialize Twilio SMS: {e}")
            raise

    elif provider_type == "email":
        # Email OTP via SES — essentially free ($0.10/1000 emails, 62K/mo free tier)
        if db is None:
            raise ValueError("Database session required for Email OTP provider")
        provider = EmailOTPProvider(db)
        logger.info("[OTP] Using Email OTP provider (SES)")
        return provider

    elif provider_type == "stub":
        if _provider_instance is None or not isinstance(_provider_instance, StubOTPProvider):
            _provider_instance = StubOTPProvider()
            logger.info("[OTP] Using stub provider")
        return _provider_instance

    else:
        raise ValueError(f"Unknown OTP provider: {provider_type}. Must be one of: twilio_verify, twilio_sms, email, stub")

