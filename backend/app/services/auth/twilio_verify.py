"""
Twilio Verify OTP provider implementation
"""
import asyncio
import logging

from twilio.base.exceptions import TwilioException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from ...core.config import settings
from .otp_provider import OTPProvider

logger = logging.getLogger(__name__)


class TwilioVerifyProvider(OTPProvider):
    """
    Twilio Verify OTP provider.
    
    Uses Twilio Verify API which handles code generation, TTL, retries, and fraud tooling.
    """
    
    def __init__(self):
        if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
            raise ValueError("Twilio credentials not configured")
        
        if not settings.TWILIO_VERIFY_SERVICE_SID:
            raise ValueError("TWILIO_VERIFY_SERVICE_SID not configured")
        
        # Create custom HTTP client with explicit timeout to prevent hanging
        custom_http_client = TwilioHttpClient()
        custom_http_client.timeout = settings.TWILIO_TIMEOUT_SECONDS
        
        self.client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
            http_client=custom_http_client
        )
        self.service_sid = settings.TWILIO_VERIFY_SERVICE_SID
        self.timeout_seconds = settings.TWILIO_TIMEOUT_SECONDS
    
    async def send_otp(self, phone: str) -> bool:
        """
        Send OTP via Twilio Verify.
        
        Args:
            phone: Normalized phone number in E.164 format
            
        Returns:
            True if OTP was sent successfully
        """
        from ...utils.phone import get_phone_last4
        
        phone_last4 = get_phone_last4(phone)
        
        def _send_verification():
            """Synchronous Twilio API call - runs in executor thread"""
            return self.client.verify.v2.services(self.service_sid).verifications.create(
                to=phone,
                channel='sms'
            )
        
        try:
            # Run blocking Twilio call in executor to avoid blocking event loop
            verification = await asyncio.wait_for(
                asyncio.to_thread(_send_verification),
                timeout=self.timeout_seconds + 5  # Add buffer for executor overhead
            )
            
            logger.info(f"[OTP][TwilioVerify] Verification sent to {phone_last4}, SID: {verification.sid}")
            return verification.status in ['pending', 'approved']
            
        except asyncio.TimeoutError:
            logger.error(f"[OTP][TwilioVerify] Timeout sending verification to {phone_last4} (>{self.timeout_seconds}s)")
            raise Exception(f"Timeout: Failed to send OTP within {self.timeout_seconds} seconds")
        except TwilioException as e:
            error_type = type(e).__name__
            logger.error(f"[OTP][TwilioVerify] Twilio error sending to {phone_last4}: {error_type}: {e}")
            # Provide more specific error messages
            if "Invalid" in str(e) or "not found" in str(e).lower():
                raise Exception(f"Twilio configuration error: {str(e)}")
            elif "timeout" in str(e).lower() or "connection" in str(e).lower():
                raise Exception(f"Network error connecting to Twilio: {str(e)}")
            else:
                raise Exception(f"Failed to send OTP: {str(e)}")
        except Exception as e:
            logger.error(f"[OTP][TwilioVerify] Unexpected error sending to {phone_last4}: {type(e).__name__}: {e}", exc_info=True)
            raise Exception(f"Failed to send OTP: {str(e)}")
    
    async def verify_otp(self, phone: str, code: str) -> bool:
        """
        Verify OTP code via Twilio Verify.

        Args:
            phone: Normalized phone number in E.164 format
            code: OTP code to verify

        Returns:
            True if code is valid, False otherwise

        Raises:
            Exception: If Twilio API is unreachable (timeout or service error)
        """
        from ...utils.phone import get_phone_last4

        phone_last4 = get_phone_last4(phone)

        logger.info(f"[OTP][TwilioVerify] Attempting verification for {phone_last4}, service_sid={self.service_sid[:8]}..., code_length={len(code)}")

        def _verify_code():
            """Synchronous Twilio API call - runs in executor thread"""
            return self.client.verify.v2.services(self.service_sid).verification_checks.create(
                to=phone,
                code=code
            )

        try:
            # Run blocking Twilio call in executor to avoid blocking event loop
            verification_check = await asyncio.wait_for(
                asyncio.to_thread(_verify_code),
                timeout=self.timeout_seconds + 5  # Add buffer for executor overhead
            )

            is_valid = verification_check.status == 'approved'

            if is_valid:
                logger.info(f"[OTP][TwilioVerify] Verification successful for {phone_last4}")
            else:
                logger.warning(f"[OTP][TwilioVerify] Verification failed for {phone_last4}: status={verification_check.status}, sid={verification_check.sid}")

            return is_valid

        except asyncio.TimeoutError:
            logger.error(f"[OTP][TwilioVerify] TIMEOUT verifying code for {phone_last4} (>{self.timeout_seconds}s). Twilio Verify API may be unreachable.")
            # Raise instead of returning False so the caller can show a proper error
            raise Exception(f"Verification timed out after {self.timeout_seconds}s. Please try again.")
        except TwilioException as e:
            error_type = type(e).__name__
            error_str = str(e)
            logger.error(f"[OTP][TwilioVerify] Twilio error verifying code for {phone_last4}: {error_type}: {error_str}")
            # Check for specific Twilio error codes
            if "20404" in error_str:
                logger.error(f"[OTP][TwilioVerify] ERROR 20404: Verify service SID not found: {self.service_sid}")
                raise Exception("Verification service misconfigured. Please contact support.")
            elif "60200" in error_str or "Max check attempts" in error_str:
                logger.warning(f"[OTP][TwilioVerify] Max verification attempts exceeded for {phone_last4}")
                return False  # Code was entered wrong too many times on Twilio's side
            elif "60202" in error_str or "expired" in error_str.lower():
                logger.warning(f"[OTP][TwilioVerify] Verification expired for {phone_last4}")
                return False  # Code expired
            else:
                raise Exception(f"Verification service error: {error_str}")
        except Exception as e:
            if "timed out" in str(e).lower() or "try again" in str(e).lower() or "misconfigured" in str(e).lower() or "service error" in str(e).lower():
                raise  # Re-raise our own exceptions from above
            logger.error(f"[OTP][TwilioVerify] Unexpected error verifying code for {phone_last4}: {type(e).__name__}: {e}", exc_info=True)
            raise Exception(f"Verification failed: {str(e)}")

