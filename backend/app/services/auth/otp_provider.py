"""
Abstract OTP provider interface
"""
from abc import ABC, abstractmethod


class OTPProvider(ABC):
    """Abstract base class for OTP providers"""
    
    @abstractmethod
    async def send_otp(self, phone: str) -> bool:
        """
        Send OTP code to phone number.
        
        Args:
            phone: Normalized phone number in E.164 format
            
        Returns:
            True if OTP was sent successfully
        """
        pass
    
    @abstractmethod
    async def verify_otp(self, phone: str, code: str) -> bool:
        """
        Verify OTP code for phone number.
        
        Args:
            phone: Normalized phone number in E.164 format
            code: OTP code to verify
            
        Returns:
            True if code is valid, False otherwise
        """
        pass







