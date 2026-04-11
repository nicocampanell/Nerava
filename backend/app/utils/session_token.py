"""
HMAC-signed session token utilities for phone-first checkin flow.

Tokens are used in SMS links and are:
- Signed with HMAC-SHA256
- Time-limited (30 minute TTL)
- Single-use for activation
"""
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Token TTL in seconds (30 minutes)
TOKEN_TTL_SECONDS = 1800


def _get_secret_key() -> bytes:
    """Get secret key for HMAC signing."""
    # Use JWT secret or dedicated token secret
    secret = getattr(settings, 'JWT_SECRET', None) or getattr(settings, 'SECRET_KEY', 'nerava-dev-secret')
    return secret.encode() if isinstance(secret, str) else secret


def _base64url_encode(data: bytes) -> str:
    """Base64 URL-safe encode without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _base64url_decode(data: str) -> bytes:
    """Base64 URL-safe decode with padding restoration."""
    # Add padding if needed
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


def generate_session_token(
    session_id: str,
    phone_hash: str,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    """
    Generate HMAC-signed session token.

    Token format: base64url(payload).base64url(signature)
    Payload: { "sid": session_id, "ph": phone_hash, "exp": expires_timestamp }

    Args:
        session_id: UUID of the arrival session
        phone_hash: Hashed phone number
        ttl_seconds: Token TTL in seconds (default 30 min)

    Returns:
        Signed token string
    """
    expires_at = int(time.time()) + ttl_seconds

    payload = {
        "sid": str(session_id),
        "ph": phone_hash[:16],  # Truncated hash
        "exp": expires_at,
    }

    # Encode payload
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = _base64url_encode(payload_json.encode())

    # Generate signature
    secret = _get_secret_key()
    signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
    signature_b64 = _base64url_encode(signature)

    return f"{payload_b64}.{signature_b64}"


def verify_session_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify HMAC-signed session token.

    Args:
        token: Token string to verify

    Returns:
        Decoded payload dict if valid, None if invalid or expired
    """
    try:
        # Split token
        parts = token.split('.')
        if len(parts) != 2:
            logger.warning("Invalid token format: wrong number of parts")
            return None

        payload_b64, signature_b64 = parts

        # Verify signature
        secret = _get_secret_key()
        expected_sig = hmac.new(secret, payload_b64.encode(), hashlib.sha256).digest()
        actual_sig = _base64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            logger.warning("Invalid token signature")
            return None

        # Decode payload
        payload_json = _base64url_decode(payload_b64).decode()
        payload = json.loads(payload_json)

        # Check expiration
        expires_at = payload.get('exp', 0)
        if time.time() > expires_at:
            logger.debug(f"Token expired at {expires_at}")
            return None

        return {
            'session_id': payload.get('sid'),
            'phone_hash': payload.get('ph'),
            'expires_at': expires_at,
        }

    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        return None


def hash_phone(phone: str) -> str:
    """
    Hash phone number for storage and comparison.

    Args:
        phone: E.164 formatted phone number

    Returns:
        SHA256 hash of phone number
    """
    return hashlib.sha256(phone.encode()).hexdigest()


def get_token_remaining_ttl(token: str) -> Optional[int]:
    """
    Get remaining TTL for a token in seconds.

    Args:
        token: Token string

    Returns:
        Remaining seconds, or None if invalid/expired
    """
    payload = verify_session_token(token)
    if not payload:
        return None

    expires_at = payload.get('expires_at', 0)
    remaining = int(expires_at - time.time())
    return max(0, remaining)
