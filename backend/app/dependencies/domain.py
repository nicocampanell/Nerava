"""
Dependency injection for Domain Charge Party MVP
Role-based access control dependencies
"""
import os
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.env import is_local_env
from ..db import get_db
from ..models import User
from ..models.admin_role import AdminRole, has_permission
from ..services.auth_service import AuthService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

DEV_ALLOW_ANON_USER_ENABLED = (
    os.getenv("NERAVA_DEV_ALLOW_ANON_USER", "false").lower() == "true"
    and is_local_env()
)


def get_current_user_public_id(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> str:
    """
    Get current user public_id (UUID string) from JWT token.
    
    JWT sub claim now contains user.public_id (UUID string), not integer id.
    """
    # Try to get token from Authorization header first
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
    
    # Try to get from cookie if no header token
    if not token:
        token = request.cookies.get("access_token")
    
    # If we have a token, decode it and extract public_id
    if token:
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                audience="nerava-api", issuer="nerava",
                options={"verify_aud": True, "verify_iss": True},
            )
            public_id = payload.get("sub")
            if public_id:
                # public_id is now a UUID string, not an integer
                return public_id
        except jwt.ExpiredSignatureError:
            # Token expired - fall through to dev fallback or raise
            pass
        except Exception:
            # Invalid token - fall through to dev fallback or raise
            pass

    # Dev fallback: if NERAVA_DEV_ALLOW_ANON_USER=true AND in local env, use default user
    if DEV_ALLOW_ANON_USER_ENABLED:
        print("[AUTH][DEV] NERAVA_DEV_ALLOW_ANON_USER=true -> using default user")
        # Try to get user with id=1 and return its public_id
        user = AuthService.get_user_by_id(db, 1)
        if user:
            return user.public_id
        # If user doesn't exist, create it (will be handled in get_current_user)
        return "dev-user-public-id"  # Placeholder, will be replaced in get_current_user
    
    # Production: authentication required
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": "unauthorized", "message": "Sign in required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    public_id: str = Depends(get_current_user_public_id),
    db: Session = Depends(get_db)
) -> User:
    """Get current user object by public_id"""
    user = AuthService.get_user_by_public_id(db, public_id)
    
    # Dev fallback: create default user if it doesn't exist
    if not user and DEV_ALLOW_ANON_USER_ENABLED:
        try:
            import uuid

            from ..models import User as UserModel
            # Create a default user for dev
            default_user = UserModel(
                id=1,
                public_id=str(uuid.uuid4()),
                email="dev@nerava.local",
                password_hash="dev",  # Not used in dev mode
                is_active=True,
                role_flags="driver",
                auth_provider="local"
            )
            db.add(default_user)
            db.commit()
            db.refresh(default_user)
            print(f"[AUTH][DEV] Created default user (id=1, public_id={default_user.public_id})")
            user = default_user
        except Exception as e:
            # If creation fails (e.g., user already exists), try to fetch again
            db.rollback()
            # Try by id=1 as fallback
            user = AuthService.get_user_by_id(db, 1)
            if not user:
                print(f"[AUTH][DEV] Failed to create/fetch dev user: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Dev mode: could not create or fetch user: {str(e)}"
                )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "User not found"}
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "forbidden", "message": "User is inactive"}
        )
    return user


# Backward compatibility: keep get_current_user_id for legacy code
# But it now returns the integer id from the User object
def get_current_user_id(
    user: User = Depends(get_current_user)
) -> int:
    """Get current user ID (integer) - for backward compatibility"""
    return user.id


def get_current_user_optional(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Get current user if authenticated, None otherwise.
    Use this for endpoints that work for both authenticated and anonymous users.
    """
    # Try to get token from Authorization header first
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    # Try to get from cookie if no header token
    if not token:
        token = request.cookies.get("access_token")

    # If no token, return None (anonymous user)
    if not token:
        return None

    # Try to decode token and get user
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
            audience="nerava-api", issuer="nerava",
            options={"verify_aud": True, "verify_iss": True},
        )
        public_id = payload.get("sub")
        if public_id:
            user = AuthService.get_user_by_public_id(db, public_id)
            if user and user.is_active:
                return user
    except Exception:
        # Invalid or expired token - treat as anonymous
        pass

    return None


def require_role(role: str):
    """Dependency factory for requiring a specific role"""
    def role_checker(user: User = Depends(get_current_user)) -> User:
        if not AuthService.has_role(user, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {role}"
            )
        return user
    return role_checker


# Convenience dependencies for common roles
require_driver = require_role("driver")
require_merchant_admin = require_role("merchant_admin")
require_admin = require_role("admin")


def require_permission(resource: str, action: str):
    """Dependency factory for requiring a specific permission on a resource"""
    def permission_checker(user: User = Depends(get_current_user)) -> User:
        if not user.admin_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No admin role assigned"
            )

        try:
            role = AdminRole(user.admin_role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Invalid admin role: {user.admin_role}"
            )

        if not has_permission(role, resource, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {action} on {resource}"
            )
        return user
    return permission_checker

