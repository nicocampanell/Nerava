"""
Driver-specific dependency injection
Provides get_current_driver with dev fallback support
"""
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import jwt
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.env import is_local_env
from ..db import get_db
from ..models import User
from ..services.auth_service import AuthService
from .domain import oauth2_scheme

DEV_ALLOW_ANON_DRIVER_ENABLED = (
    os.getenv("NERAVA_DEV_ALLOW_ANON_DRIVER", "false").lower() == "true" 
    and is_local_env()
)


def get_current_driver_id(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> int:
    """
    Resolve the current driver ID from the request.
    
    In production, this requires valid authentication (JWT token).
    In local/dev, when NERAVA_DEV_ALLOW_ANON_DRIVER=true, uses driver_id=1 as fallback.
    
    Args:
        request: FastAPI Request object
        token: Optional OAuth2 token from header
        db: Database session
        
    Returns:
        int: Driver user ID
        
    Raises:
        HTTPException: 401 if authentication fails and dev fallback is not enabled
    """
    # 1. Try to get token from Authorization header first
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
    
    # 2. Try to get from cookie if no header token
    if not token:
        token = request.cookies.get("access_token")
    
    # 3. If we have a token, decode it and extract public_id (UUID string)
    if token:
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                audience="nerava-api", issuer="nerava",
                options={"verify_aud": True, "verify_iss": True},
            )
            public_id = payload.get("sub")
            if public_id:
                # public_id is a UUID string, not an integer
                # Lookup user by public_id to get integer id
                user = AuthService.get_user_by_public_id(db, public_id)
                if user:
                    return user.id
        except jwt.ExpiredSignatureError:
            # Token expired - fall through to dev fallback or raise
            pass
        except Exception as e:
            # Invalid token - fall through to dev fallback or raise
            print(f"[AUTH] Token decode failed: {e}")
            pass
    
    # 4. Dev fallback: if NERAVA_DEV_ALLOW_ANON_DRIVER=true AND in local env, use default driver
    if DEV_ALLOW_ANON_DRIVER_ENABLED:
        print("[AUTH][DEV] NERAVA_DEV_ALLOW_ANON_DRIVER=true (local env) -> using driver_id=1")
        return 1
    
    # 5. Production: authentication required
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "AUTHENTICATION_REQUIRED",
            "message": "Driver authentication required"
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_driver(
    driver_id: int = Depends(get_current_driver_id),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current driver User object.
    
    Uses get_current_driver_id to resolve the driver ID (with dev fallback),
    then fetches and validates the User object.
    
    In dev mode, if user doesn't exist, creates a default driver user.
    
    Args:
        driver_id: Driver user ID from get_current_driver_id
        db: Database session
        
    Returns:
        User: Driver user object
        
    Raises:
        HTTPException: 401 if user not found or inactive
    """
    user = AuthService.get_user_by_id(db, driver_id)
    
    # Dev fallback: create default driver user if it doesn't exist
    if not user and DEV_ALLOW_ANON_DRIVER_ENABLED and driver_id == 1:
        try:
            from ..models import User as UserModel
            # Create a default driver user for dev
            default_user = UserModel(
                id=1,
                email="dev@nerava.local",
                password_hash="dev",  # Not used in dev mode
                is_active=True,
                role_flags="driver",
                auth_provider="local"
            )
            db.add(default_user)
            db.commit()
            db.refresh(default_user)
            print("[AUTH][DEV] Created default driver user (id=1)")
            user = default_user
        except Exception as e:
            # If creation fails (e.g., user already exists), try to fetch again
            db.rollback()
            user = AuthService.get_user_by_id(db, driver_id)
            if not user:
                print(f"[AUTH][DEV] Failed to create/fetch dev user: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Dev mode: could not create or fetch driver user: {str(e)}"
                )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "USER_NOT_FOUND",
                "message": "Driver user not found"
            }
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "ACCOUNT_INACTIVE",
                "message": "Driver account is inactive"
            }
        )
    return user


def get_current_driver_optional(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Get current driver User object (optional - returns None if not authenticated).
    
    This is useful for public endpoints like QR code viewing where authentication
    is optional but provides additional features (like showing balance) if present.
    
    Args:
        request: FastAPI Request object
        token: Optional OAuth2 token from header
        db: Database session
        
    Returns:
        Optional[User]: Driver user object if authenticated, None otherwise
    """
    try:
        # Try to get driver ID
        driver_id = get_current_driver_id(request, token, db)
        # If we got a driver ID, get the user
        user = AuthService.get_user_by_id(db, driver_id)
        if user and user.is_active:
            return user
    except HTTPException:
        # Authentication failed - that's OK, return None
        pass
    except Exception:
        # Any other error - return None
        pass
    
    return None


