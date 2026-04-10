"""
Apple Sign In ID token verification service
"""
from typing import Any, Dict

import httpx
import jwt
from fastapi import HTTPException, status
from jwt.algorithms import RSAAlgorithm

from ..core.config import settings

# Cache for Apple JWKS
_apple_jwks_cache: Dict[str, Any] = {}
_apple_jwks_url = "https://appleid.apple.com/auth/keys"


def get_apple_jwks() -> Dict[str, Any]:
    """Fetch Apple's public keys (JWKS)"""
    global _apple_jwks_cache
    
    # Return cached if available
    if _apple_jwks_cache:
        return _apple_jwks_cache
    
    try:
        response = httpx.get(_apple_jwks_url, timeout=10.0)
        response.raise_for_status()
        _apple_jwks_cache = response.json()
        return _apple_jwks_cache
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch Apple JWKS: {str(e)}"
        )


def verify_apple_id_token(id_token: str) -> Dict[str, Any]:
    """
    Verify Apple ID token and extract user information.
    
    Args:
        id_token: Apple ID token string
        
    Returns:
        Dict with user info: email, sub, etc.
        
    Raises:
        HTTPException 503: If Apple config not set
        HTTPException 401: If token verification fails
    """
    if not settings.APPLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apple authentication is not configured. Set APPLE_CLIENT_ID environment variable."
        )
    
    try:
        # Decode token header to get key ID
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        
        if not kid:
            raise ValueError("Token missing key ID")
        
        # Get Apple JWKS
        jwks = get_apple_jwks()
        
        # Find the matching key
        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                # Construct RSA public key
                key = RSAAlgorithm.from_jwk(jwk)
                break
        
        if not key:
            raise ValueError(f"Key {kid} not found in Apple JWKS")
        
        # Verify and decode token
        payload = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=settings.APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com"
        )
        
        return {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "email_verified": payload.get("email_verified", False)
        }
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Apple ID token has expired"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Apple ID token: {str(e)}"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Apple token verification failed: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Apple authentication error: {str(e)}"
        )








