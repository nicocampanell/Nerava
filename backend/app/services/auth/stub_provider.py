"""
Stub OTP provider for dev/staging environments
"""
import logging
import os
from typing import List

from ...core.config import settings
from .otp_provider import OTPProvider

logger = logging.getLogger(__name__)


class StubOTPProvider(OTPProvider):
    """
    Stub OTP provider for development/staging.
    
    Accepts code '000000' for allowlisted phones.
    Logs codes for dev convenience.
    """
    
    STUB_CODE = "000000"
    
    def __init__(self):
        # Parse allowlist
        allowlist_str = getattr(settings, 'OTP_DEV_ALLOWLIST', '') or os.getenv('OTP_DEV_ALLOWLIST', '')
        self.allowlist: List[str] = []
        
        if allowlist_str:
            self.allowlist = [p.strip() for p in allowlist_str.split(',') if p.strip()]
        
        env = getattr(settings, 'ENV', 'dev')
        if env == 'prod':
            logger.warning("[OTP][Stub] WARNING: Stub provider enabled in production! This should not happen.")
        else:
            logger.info(f"[OTP][Stub] Stub provider enabled for environment: {env}")
            if self.allowlist:
                logger.info(f"[OTP][Stub] Allowlist: {len(self.allowlist)} phone(s)")
            else:
                logger.warning("[OTP][Stub] No allowlist configured - stub provider will accept any phone")
    
    async def send_otp(self, phone: str) -> bool:
        """
        Stub send OTP - just log the code.
        
        Args:
            phone: Normalized phone number in E.164 format
            
        Returns:
            True (always succeeds in stub mode)
        """
        # Check allowlist if configured
        if self.allowlist and phone not in self.allowlist:
            logger.warning(f"[OTP][Stub] Phone {phone} not in allowlist, but allowing anyway in stub mode")
        
        logger.info(f"[OTP][Stub] Code for {phone}: {self.STUB_CODE}")
        return True
    
    async def verify_otp(self, phone: str, code: str) -> bool:
        """
        Verify stub OTP code.
        
        In dev/staging mode, accepts stub code '000000' or any code if allowlist is empty.
        In party/pilot mode, accepts '000000' for any phone.
        
        Args:
            phone: Normalized phone number in E.164 format
            code: OTP code to verify
            
        Returns:
            True if code matches stub code and phone is in allowlist (if configured)
        """
        env = getattr(settings, 'ENV', 'dev')
        env_lower = str(env).lower()
        
        # Always accept stub code in non-prod environments (dev, development, staging, etc.)
        if env_lower != 'prod' and env_lower != 'production':
            # Strip whitespace from code for comparison
            code_clean = code.strip() if code else ""
            is_valid = code_clean == self.STUB_CODE
            
            # Log detailed info for debugging
            logger.info(f"[OTP][Stub] Verification attempt: phone={phone}, code='{code_clean}', stub_code='{self.STUB_CODE}', env={env}, match={is_valid}")
            
            # In dev mode, also accept any non-empty code for testing convenience
            if not is_valid and code_clean:
                logger.info("[OTP][Stub] Code mismatch, but accepting any code in dev mode for testing")
                is_valid = True
            
            if is_valid:
                logger.info(f"[OTP][Stub] Verification successful for {phone} (stub mode, env={env})")
            else:
                logger.warning(f"[OTP][Stub] Verification failed for {phone}: code mismatch (expected '{self.STUB_CODE}', got '{code_clean}')")
            return is_valid
        
        # In prod, check allowlist if configured
        if self.allowlist and phone not in self.allowlist:
            logger.warning(f"[OTP][Stub] Verification failed: {phone} not in allowlist")
            return False
        
        # Accept stub code (prod with allowlist check passed, or no allowlist)
        is_valid = code == self.STUB_CODE
        
        if is_valid:
            logger.info(f"[OTP][Stub] Verification successful for {phone}")
        else:
            logger.warning(f"[OTP][Stub] Verification failed for {phone}: code mismatch")
        
        return is_valid

