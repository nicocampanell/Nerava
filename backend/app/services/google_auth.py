"""
Google OAuth ID token verification service
"""
from typing import Any, Dict

from fastapi import HTTPException, status

from ..core.config import settings


def verify_google_id_token(id_token: str) -> Dict[str, Any]:
    """
    Verify Google ID token and extract user information.
    
    Args:
        id_token: Google ID token string
        
    Returns:
        Dict with user info: email, sub, name, etc.
        
    Raises:
        HTTPException 503: If Google client ID not configured
        HTTPException 401: If token verification fails
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google authentication is not configured. Set GOOGLE_CLIENT_ID environment variable."
        )
    
    try:
        # Use google-auth library for verification
        from google.auth.transport import requests
        from google.oauth2 import id_token
        
        # Verify the token
        request = requests.Request()
        user_info = id_token.verify_oauth2_token(
            id_token,
            request,
            settings.GOOGLE_CLIENT_ID
        )
        
        # Verify audience matches
        if user_info.get("aud") != settings.GOOGLE_CLIENT_ID:
            raise ValueError("Token audience mismatch")
        
        return {
            "sub": user_info.get("sub"),
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture"),
            "email_verified": user_info.get("email_verified", False)
        }
        
    except ImportError:
        # google-auth library not installed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google authentication library not installed. Install with: pip install google-auth"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google ID token: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google token verification failed: {str(e)}"
        )








