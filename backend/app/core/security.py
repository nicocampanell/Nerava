import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from jose import jwt
from passlib.context import CryptContext

from .config import settings

# Use PBKDF2-SHA256 (no 72-byte limit like bcrypt)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Use PBKDF2-SHA256 for refresh tokens too (avoids bcrypt's 72-byte limit)
# This is secure and doesn't have the length restriction
refresh_token_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(subject: str, expires_delta: Optional[timedelta] = None, auth_provider: Optional[str] = None, role: Optional[str] = None) -> str:
    """
    Create a JWT access token.
    
    Args:
        subject: User's public_id (UUID string) - used as JWT sub claim
        expires_delta: Optional expiration time delta
        auth_provider: Optional auth provider (google, apple, phone) for debugging
        role: Optional user role (driver, merchant, admin)
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.utcnow() + expires_delta
    payload: Dict[str, Any] = {
        "sub": subject,  # public_id (UUID string)
        "exp": expire,
        "iat": datetime.utcnow(),
        "iss": "nerava",
        "aud": "nerava-api",
    }
    if auth_provider:
        payload["auth_provider"] = auth_provider
    if role:
        payload["role"] = role
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def generate_refresh_token() -> str:
    """Generate a random 256-bit (32-byte) refresh token"""
    # Generate 32 bytes = 256 bits, URL-safe encoding produces ~43 chars (well under 72-byte bcrypt limit)
    token = secrets.token_urlsafe(32)
    # Ensure token is not longer than 72 bytes for bcrypt compatibility
    if len(token.encode('utf-8')) > 72:
        # Truncate to 72 bytes (shouldn't happen with token_urlsafe(32), but safety check)
        token_bytes = token.encode('utf-8')[:72]
        token = token_bytes.decode('utf-8', errors='ignore')
    return token

def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage"""
    # Bcrypt has a 72-byte limit. Ensure token doesn't exceed this.
    # token_urlsafe(32) produces ~43 chars (~43 bytes), but we'll truncate to be safe
    token_bytes = token.encode('utf-8')
    token_len = len(token_bytes)
    
    # Debug logging
    import logging
    logger = logging.getLogger("nerava")
    logger.debug(f"Hash refresh token: length={token_len} bytes, token={token[:20]}...")
    
    if token_len > 72:
        # Truncate to exactly 72 bytes
        logger.warning(f"Token too long ({token_len} bytes), truncating to 72 bytes")
        token_bytes = token_bytes[:72]
        token = token_bytes.decode('utf-8', errors='ignore')
    
    try:
        # Pass as string to passlib (it handles encoding internally)
        return refresh_token_context.hash(token)
    except ValueError as e:
        error_msg = str(e)
        logger.error(f"Hash refresh token error: {error_msg}, token_len={len(token.encode('utf-8'))}")
        # If still fails, truncate more aggressively
        if "72 bytes" in error_msg.lower() or "too long" in error_msg.lower():
            logger.warning("Truncating token to 64 bytes as fallback")
            token_bytes = token.encode('utf-8')[:64]
            token = token_bytes.decode('utf-8', errors='ignore')
            return refresh_token_context.hash(token)
        raise

def verify_refresh_token(plain: str, hashed: str) -> bool:
    """Verify a refresh token against its hash"""
    return refresh_token_context.verify(plain, hashed)

def create_smartcar_state_jwt(user_public_id: str, nonce: Optional[str] = None) -> str:
    """
    Create a signed state JWT for Smartcar OAuth flow.
    
    Args:
        user_public_id: User's public_id (UUID string)
        nonce: Optional nonce UUID for additional security
    
    Returns:
        Signed JWT token string
    """
    import uuid
    if nonce is None:
        nonce = str(uuid.uuid4())
    
    expires_delta = timedelta(minutes=15)
    expire = datetime.utcnow() + expires_delta
    
    payload: Dict[str, Any] = {
        "user_public_id": user_public_id,
        "nonce": nonce,
        "purpose": "smartcar_oauth",
        "exp": expire,
        "iat": datetime.utcnow()
    }
    
    if not settings.SMARTCAR_STATE_SECRET:
        raise ValueError("SMARTCAR_STATE_SECRET not configured")
    
    return jwt.encode(payload, settings.SMARTCAR_STATE_SECRET, algorithm=settings.ALGORITHM)

def verify_smartcar_state_jwt(token: str) -> Dict[str, Any]:
    """
    Verify and decode a Smartcar state JWT.
    
    Returns:
        Decoded payload with user_public_id and nonce
    
    Raises:
        jwt.ExpiredSignatureError: If token is expired
        jwt.JWTError: If token is invalid
    """
    if not settings.SMARTCAR_STATE_SECRET:
        raise ValueError("SMARTCAR_STATE_SECRET not configured")
    
    payload = jwt.decode(token, settings.SMARTCAR_STATE_SECRET, algorithms=[settings.ALGORITHM])
    
    if payload.get("purpose") != "smartcar_oauth":
        raise jwt.JWTError("Invalid token purpose")
    
    user_public_id = payload.get("user_public_id")
    if not user_public_id:
        raise jwt.JWTError("Missing user_public_id in token")
    
    return payload
