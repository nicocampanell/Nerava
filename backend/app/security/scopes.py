"""
Security scopes and authorization utilities.
"""
from typing import Any, Dict, List

from fastapi import Depends, HTTPException, Request

from app.security.jwt import jwt_manager


def get_current_user(request: Request) -> Dict[str, Any]:
    """Get current user with scopes from JWT token."""
    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header"
        )
    
    token = auth_header.split(" ")[1]
    try:
        payload = jwt_manager.verify_token(token)
        return {
            "user_id": payload.get("user_id"),
            "scopes": payload.get("scopes", [])
        }
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail="Invalid token"
        )

def require_scopes(required: List[str]):
    """Dependency to require specific scopes."""
    def scope_checker(current_user: Dict[str, Any] = Depends(get_current_user)):
        user_scopes = current_user.get("scopes", [])
        
        # Check if user has any of the required scopes
        if not any(scope in user_scopes for scope in required):
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scopes: {required}. User has: {user_scopes}"
            )
        
        return current_user
    
    return scope_checker
