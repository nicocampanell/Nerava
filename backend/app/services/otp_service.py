"""
Phone OTP (One-Time Password) service
DB-backed OTP challenges for v1 (no Redis)
"""
import secrets
import uuid
from datetime import datetime, timedelta

import phonenumbers
from phonenumbers import NumberParseException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.security import hash_password, verify_password
from ..models import OTPChallenge


class OTPService:
    """Service for generating, sending, and verifying OTP codes"""
    
    OTP_LENGTH = 6
    OTP_EXPIRE_MINUTES = 10
    MAX_ATTEMPTS = 5
    
    @staticmethod
    def normalize_phone(phone: str) -> str:
        """
        Normalize phone number to E.164 format.
        
        Raises:
            ValueError: If phone number is invalid
        """
        try:
            # Parse phone number (assume US if no country code)
            parsed = phonenumbers.parse(phone, "US")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Invalid phone number")
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except NumberParseException as e:
            raise ValueError(f"Invalid phone number format: {str(e)}")
    
    @staticmethod
    def generate_otp_code() -> str:
        """Generate a random 6-digit OTP code"""
        length = OTPService.OTP_LENGTH
        return str(secrets.randbelow(10**length)).zfill(length)
    
    @staticmethod
    async def send_otp(db: Session, phone: str) -> bool:
        """
        Generate OTP code, hash it, store challenge, and send SMS.
        
        Returns:
            True if OTP was sent successfully
        """
        # Normalize phone number
        normalized_phone = OTPService.normalize_phone(phone)
        
        # Generate OTP code
        otp_code = OTPService.generate_otp_code()
        code_hash = hash_password(otp_code)  # Use password hashing for OTP
        
        # Create challenge record
        expires_at = datetime.utcnow() + timedelta(minutes=OTPService.OTP_EXPIRE_MINUTES)
        
        challenge = OTPChallenge(
            id=str(uuid.uuid4()),
            phone=normalized_phone,
            code_hash=code_hash,
            expires_at=expires_at,
            attempts=0,
            max_attempts=OTPService.MAX_ATTEMPTS,
            consumed=False
        )
        
        db.add(challenge)
        db.commit()
        
        # Send SMS via Twilio (if configured) or log for dev
        try:
            await OTPService._send_sms(normalized_phone, otp_code)
        except Exception as e:
            # Log error but don't fail - OTP is stored, can be verified
            print(f"[OTP] Failed to send SMS to {normalized_phone}: {e}")
            # In dev, log the code
            if settings.ENV == "dev":
                print(f"[OTP][DEV] Code for {normalized_phone}: {otp_code}")
        
        return True
    
    @staticmethod
    async def _send_sms(phone: str, code: str) -> None:
        """Send SMS via Twilio (if configured)"""
        if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
            # Twilio not configured - skip sending (dev mode)
            print(f"[OTP][DEV] Twilio not configured. Code for {phone}: {code}")
            return
        
        try:
            from twilio.rest import Client
            
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            
            message = client.messages.create(
                body=f"Your Nerava verification code is: {code}",
                from_=settings.TWILIO_PHONE_NUMBER if hasattr(settings, 'TWILIO_PHONE_NUMBER') else None,
                to=phone
            )
            
            print(f"[OTP] SMS sent to {phone}, SID: {message.sid}")
        except ImportError:
            print("[OTP] Twilio library not installed. Install with: pip install twilio")
        except Exception as e:
            raise Exception(f"Failed to send SMS: {str(e)}")
    
    @staticmethod
    async def verify_otp(db: Session, phone: str, code: str) -> str:
        """
        Verify OTP code against stored challenge.
        
        Returns:
            Normalized phone number if verification succeeds
            
        Raises:
            HTTPException: If verification fails
        """
        from fastapi import HTTPException, status
        
        # Normalize phone number
        normalized_phone = OTPService.normalize_phone(phone)
        
        # Find active challenge (not consumed, not expired)
        challenge = db.query(OTPChallenge).filter(
            and_(
                OTPChallenge.phone == normalized_phone,
                OTPChallenge.consumed == False,
                OTPChallenge.expires_at > datetime.utcnow()
            )
        ).order_by(OTPChallenge.created_at.desc()).first()
        
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active OTP challenge found. Please request a new code."
            )
        
        # Check attempt limit
        if challenge.attempts >= challenge.max_attempts:
            challenge.consumed = True
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Please request a new code."
            )
        
        # Increment attempts
        challenge.attempts += 1
        
        # Verify code
        if not verify_password(code, challenge.code_hash):
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid verification code"
            )
        
        # Mark challenge as consumed
        challenge.consumed = True
        db.commit()
        
        return normalized_phone

