"""
Token creation with role claims
"""
from datetime import datetime, timedelta
from typing import Optional

from ...core.config import settings


def create_token_with_role(
    subject: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    auth_provider: Optional[str] = None
) -> str:
    """
    Create a JWT access token with role claim.
    
    Args:
        subject: User's public_id (UUID string) - used as JWT sub claim
        role: User role (driver, merchant, admin)
        expires_delta: Optional expiration time delta
        auth_provider: Optional auth provider (google, apple, phone) for debugging
        
    Returns:
        JWT token string with role claim
    """
    # Use existing create_access_token but we'll need to extend it
    # For now, create token and add role claim manually
    from jose import jwt
    
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    expire = datetime.utcnow() + expires_delta
    
    payload = {
        "sub": subject,
        "role": role,
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    
    if auth_provider:
        payload["auth_provider"] = auth_provider
    
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

