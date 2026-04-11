"""
Structured audit logging service for authentication events
"""
import json
import logging
from datetime import datetime
from typing import Optional

# Note: Audit logging is separate from PostHog analytics
# PostHog events are sent from route handlers, not from audit service

logger = logging.getLogger(__name__)


class AuditService:
    """
    Structured audit logging service for authentication events.
    
    Never logs full codes or full phone numbers.
    """
    
    @staticmethod
    def _log_audit_event(
        event_type: str,
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        outcome: Optional[str] = None,
        error: Optional[str] = None,
        **kwargs
    ):
        """
        Log structured audit event.
        
        Args:
            event_type: Event type (e.g., 'otp_start_requested')
            request_id: Request ID from middleware
            phone_last4: Last 4 digits of phone number
            ip: Client IP address
            user_agent: User agent string
            env: Environment (dev/staging/prod)
            outcome: Outcome (success/fail/rate_limited/blocked)
            error: Error message (if any)
            **kwargs: Additional event-specific fields
        """
        audit_data = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "outcome": outcome,
        }
        
        if request_id:
            audit_data["request_id"] = request_id
        if phone_last4:
            audit_data["phone_last4"] = phone_last4
        if ip:
            audit_data["ip"] = ip
        if user_agent:
            audit_data["user_agent"] = user_agent
        if env:
            audit_data["env"] = env
        if error:
            audit_data["error"] = error
        
        # Add any additional fields
        audit_data.update(kwargs)
        
        # Log as JSON for structured logging
        logger.info(f"[Auth][Audit] {json.dumps(audit_data)}")
    
    @staticmethod
    def log_otp_start_requested(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
    ):
        """Log OTP start request"""
        AuditService._log_audit_event(
            "otp_start_requested",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="requested",
        )
    
    @staticmethod
    def log_otp_start_sent(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
    ):
        """Log successful OTP send"""
        AuditService._log_audit_event(
            "otp_start_sent",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="success",
        )
    
    @staticmethod
    def log_otp_start_rate_limited(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        """Log OTP start rate limit"""
        AuditService._log_audit_event(
            "otp_start_rate_limited",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="rate_limited",
            error=reason,
        )
    
    @staticmethod
    def log_otp_verify_success(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        user_id: Optional[str] = None,
        is_new_user: bool = False,
    ):
        """Log successful OTP verification"""
        AuditService._log_audit_event(
            "otp_verify_success",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="success",
            user_id=user_id,
            is_new_user=is_new_user,
        )
    
    @staticmethod
    def log_otp_verify_fail(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Log failed OTP verification"""
        AuditService._log_audit_event(
            "otp_verify_fail",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="fail",
            error=error,
        )
    
    @staticmethod
    def log_otp_verify_rate_limited(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        """Log OTP verify rate limit"""
        AuditService._log_audit_event(
            "otp_verify_rate_limited",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="rate_limited",
            error=reason,
        )
    
    @staticmethod
    def log_otp_blocked(
        request_id: Optional[str] = None,
        phone_last4: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        """Log OTP blocked (lockout)"""
        AuditService._log_audit_event(
            "otp_blocked",
            request_id=request_id,
            phone_last4=phone_last4,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="blocked",
            error=reason,
        )
    
    @staticmethod
    def log_merchant_sso_login_success(
        request_id: Optional[str] = None,
        email_domain: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        """Log successful merchant SSO login"""
        AuditService._log_audit_event(
            "merchant_sso_login_success",
            request_id=request_id,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="success",
            email_domain=email_domain,
            user_id=user_id,
        )
    
    @staticmethod
    def log_merchant_sso_login_fail(
        request_id: Optional[str] = None,
        email_domain: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Log failed merchant SSO login"""
        AuditService._log_audit_event(
            "merchant_sso_login_fail",
            request_id=request_id,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="fail",
            email_domain=email_domain,
            error=error,
        )
    
    @staticmethod
    def log_merchant_gbp_access_granted(
        request_id: Optional[str] = None,
        email_domain: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        location_count: int = 0,
    ):
        """Log GBP access granted"""
        AuditService._log_audit_event(
            "merchant_gbp_access_granted",
            request_id=request_id,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="success",
            email_domain=email_domain,
            location_count=location_count,
        )
    
    @staticmethod
    def log_merchant_gbp_access_denied(
        request_id: Optional[str] = None,
        email_domain: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        env: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        """Log GBP access denied"""
        AuditService._log_audit_event(
            "merchant_gbp_access_denied",
            request_id=request_id,
            ip=ip,
            user_agent=user_agent,
            env=env,
            outcome="denied",
            email_domain=email_domain,
            error=reason,
        )

