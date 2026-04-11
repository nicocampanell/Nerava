"""
Phone number normalization and validation utilities
"""
from typing import Tuple

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def normalize_phone(phone: str, default_region: str = "US") -> str:
    """
    Normalize phone number to E.164 format.
    
    Args:
        phone: Phone number string (can be in various formats)
        default_region: Default region code if no country code present (default: US)
        
    Returns:
        Normalized phone number in E.164 format (e.g., +14155551234)
        
    Raises:
        ValueError: If phone number is invalid or unsupported
    """
    try:
        # Parse phone number
        parsed = phonenumbers.parse(phone, default_region)
        
        # Validate
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError("Invalid phone number")
        
        # Check if country is supported (start with US +1)
        country_code = parsed.country_code
        if country_code != 1:
            # For now, only support US (+1)
            # Can be extended later to support other countries
            raise ValueError(f"Unsupported country code: +{country_code}. Only US (+1) is currently supported.")
        
        # Format as E.164
        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        
    except NumberParseException as e:
        raise ValueError(f"Invalid phone number format: {str(e)}")


def validate_phone(phone: str, default_region: str = "US") -> bool:
    """
    Validate phone number without raising exception.
    
    Args:
        phone: Phone number string
        default_region: Default region code
        
    Returns:
        True if valid, False otherwise
    """
    try:
        normalize_phone(phone, default_region)
        return True
    except (ValueError, NumberParseException):
        return False


def get_phone_last4(phone: str) -> str:
    """
    Get last 4 digits of phone number for safe logging.
    
    Args:
        phone: Phone number (can be in any format)
        
    Returns:
        Last 4 digits as string, or full phone if less than 4 digits
    """
    # Remove all non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    
    if len(digits) >= 4:
        return digits[-4:]
    return digits


def parse_phone(phone: str, default_region: str = "US") -> Tuple[str, int, str, str]:
    """
    Parse phone number and return components.
    
    Args:
        phone: Phone number string
        default_region: Default region code
        
    Returns:
        Tuple of (normalized_phone, country_code, national_number, last4)
        
    Raises:
        ValueError: If phone number is invalid
    """
    normalized = normalize_phone(phone, default_region)
    
    try:
        parsed = phonenumbers.parse(normalized, default_region)
        country_code = parsed.country_code
        national_number = str(parsed.national_number)
        last4 = get_phone_last4(normalized)
        
        return normalized, country_code, national_number, last4
    except NumberParseException:
        raise ValueError(f"Failed to parse phone number: {phone}")







