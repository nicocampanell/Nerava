"""
JWT token utilities for verify flow (short-lived, one-time use)
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict

import jwt
from fastapi import HTTPException, status

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = os.getenv("JWT_ALG", "HS256")


def create_verify_token(*, user_id: int, session_id: str, ttl_seconds: int = 600) -> str:
    """
    Create a short-lived JWT token for verify flow.
    
    Claims:
    - sub: user_id
    - sid: session_id
    - iat: issued at
    - exp: expiration
    - jti: random token ID (for one-time use tracking)
    """
    now = datetime.utcnow()
    expire = now + timedelta(seconds=ttl_seconds)
    jti = secrets.token_urlsafe(16)  # Random token ID
    
    payload = {
        "sub": str(user_id),  # Subject (user ID)
        "sid": session_id,    # Session ID
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": jti,           # JWT ID (for one-time use)
        "type": "verify"      # Token type
    }
    
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
    return token


def decode_verify_token(token: str) -> Dict[str, Any]:
    """
    Decode and verify a verify token.
    
    Returns:
        dict with sub (user_id), sid (session_id), jti, exp, iat
    
    Raises:
        HTTPException if token is invalid or expired
    """
    try:
        # Decode with leeway for clock skew
        # Disable iat validation to avoid "not yet valid" errors
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALG],
            options={"verify_exp": True, "verify_iat": False, "leeway": 5}
        )
        
        # Verify token type
        if payload.get("type") != "verify":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )
        
        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}"
        )

