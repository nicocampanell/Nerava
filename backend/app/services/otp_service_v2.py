"""
Production-ready OTP service using provider pattern
"""
import hashlib
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..core.config import settings
from ..services.analytics import get_analytics_client
from ..utils.phone import get_phone_last4, normalize_phone
from .auth import get_otp_provider, get_rate_limit_service
from .auth.audit import AuditService

logger = logging.getLogger(__name__)


class OTPServiceV2:
    """
    Production-ready OTP service with rate limiting, audit logging, and provider abstraction.
    """
    
    @staticmethod
    async def send_otp(
        db: Session,
        phone: str,
        request_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> bool:
        """
        Send OTP code to phone number with rate limiting and audit logging.
        
        Args:
            db: Database session
            phone: Phone number (will be normalized)
            request_id: Request ID from middleware
            ip: Client IP address
            user_agent: User agent string
            
        Returns:
            True if OTP was sent successfully
            
        Raises:
            ValueError: If phone number is invalid
            HTTPException: If rate limited
        """
        from fastapi import HTTPException, status
        
        # Normalize phone
        try:
            normalized_phone = normalize_phone(phone)
        except ValueError as e:
            logger.warning(f"[OTP] Invalid phone number: {phone}")
            raise ValueError(f"Invalid phone number: {str(e)}")
        
        phone_last4 = get_phone_last4(normalized_phone)
        
        # Rate limiting check
        rate_limit_service = get_rate_limit_service()
        allowed, error_msg = rate_limit_service.check_rate_limit_start(normalized_phone, ip or "unknown")
        
        if not allowed:
            # Audit log
            AuditService.log_otp_start_rate_limited(
                request_id=request_id,
                phone_last4=phone_last4,
                ip=ip,
                user_agent=user_agent,
                env=settings.ENV,
                reason=error_msg,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Unable to send code. Try again later."
            )
        
        # Record attempt
        rate_limit_service.record_start_attempt(normalized_phone, ip or "unknown")
        
        # Audit log: request
        AuditService.log_otp_start_requested(
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=settings.ENV,
        )
        
        # Get provider and send OTP
        try:
            provider = get_otp_provider(db)
            success = await provider.send_otp(normalized_phone)
            
            if success:
                # Audit log: sent
                AuditService.log_otp_start_sent(
                    request_id=request_id,
                    phone_last4=phone_last4,
                    ip=ip,
                    user_agent=user_agent,
                    env=settings.ENV,
                )
                logger.info(f"[OTP] OTP sent successfully to {phone_last4}")
                
                # PostHog: Track OTP sent
                analytics = get_analytics_client()
                phone_hash = hashlib.sha256(normalized_phone.encode()).hexdigest()[:16]
                provider_name = provider.__class__.__name__.replace('Provider', '').lower()
                analytics.capture(
                    event="server.otp.sent",
                    distinct_id=f"phone:{phone_hash}",
                    request_id=request_id,
                    ip=ip,
                    user_agent=user_agent,
                    properties={
                        "phone_hash": phone_hash,
                        "provider": provider_name,
                        "purpose": "login",  # Default, can be extended if needed
                    }
                )
            else:
                logger.warning(f"[OTP] OTP send failed for {phone_last4}")
            
            return success
            
        except Exception as e:
            logger.error(f"[OTP] Failed to send OTP to {phone_last4}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to send code. Try again later."
            )
    
    @staticmethod
    async def verify_otp(
        db: Session,
        phone: str,
        code: str,
        request_id: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> str:
        """
        Verify OTP code with rate limiting and audit logging.
        
        Args:
            db: Database session
            phone: Phone number (will be normalized)
            code: OTP code to verify
            request_id: Request ID from middleware
            ip: Client IP address
            user_agent: User agent string
            
        Returns:
            Normalized phone number if verification succeeds
            
        Raises:
            ValueError: If phone number is invalid
            HTTPException: If verification fails or rate limited
        """
        from fastapi import HTTPException, status
        
        # Normalize phone
        try:
            normalized_phone = normalize_phone(phone)
        except ValueError as e:
            logger.warning(f"[OTP] Invalid phone number: {phone}")
            raise ValueError(f"Invalid phone number: {str(e)}")
        
        phone_last4 = get_phone_last4(normalized_phone)
        
        # Rate limiting check
        rate_limit_service = get_rate_limit_service()
        allowed, error_msg = rate_limit_service.check_rate_limit_verify(normalized_phone)
        
        if not allowed:
            # Audit log
            if rate_limit_service.is_locked_out(normalized_phone):
                AuditService.log_otp_blocked(
                    request_id=request_id,
                    phone_last4=phone_last4,
                    ip=ip,
                    user_agent=user_agent,
                    env=settings.ENV,
                    reason=error_msg,
                )
            else:
                AuditService.log_otp_verify_rate_limited(
                    request_id=request_id,
                    phone_last4=phone_last4,
                    ip=ip,
                    user_agent=user_agent,
                    env=settings.ENV,
                    reason=error_msg,
                )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please wait a few minutes and try again."
            )
        
        # Get provider and verify OTP
        try:
            provider = get_otp_provider(db)
            is_valid = await provider.verify_otp(normalized_phone, code)
            
            # Record attempt
            rate_limit_service.record_verify_attempt(normalized_phone, is_valid)
            
            if is_valid:
                # Audit log: success
                AuditService.log_otp_verify_success(
                    request_id=request_id,
                    phone_last4=phone_last4,
                    ip=ip,
                    user_agent=user_agent,
                    env=settings.ENV,
                )
                logger.info(f"[OTP] Verification successful for {phone_last4}")
                
                # PostHog: Track OTP verified
                analytics = get_analytics_client()
                phone_hash = hashlib.sha256(normalized_phone.encode()).hexdigest()[:16]
                provider_name = provider.__class__.__name__.replace('Provider', '').lower()
                analytics.capture(
                    event="server.otp.verified",
                    distinct_id=f"phone:{phone_hash}",
                    request_id=request_id,
                    ip=ip,
                    user_agent=user_agent,
                    properties={
                        "phone_hash": phone_hash,
                        "provider": provider_name,
                    }
                )
                
                return normalized_phone
            else:
                # Audit log: fail
                AuditService.log_otp_verify_fail(
                    request_id=request_id,
                    phone_last4=phone_last4,
                    ip=ip,
                    user_agent=user_agent,
                    env=settings.ENV,
                    error="Invalid code",
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid code."
                )
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[OTP] Error verifying OTP for {phone_last4}: {e}", exc_info=True)
            # Record failed attempt
            rate_limit_service.record_verify_attempt(normalized_phone, False)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Verification service error. Please try again."
            )







