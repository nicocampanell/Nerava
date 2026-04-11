"""
Authentication dependencies for role-based access control
"""
from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from ..core.config import settings


def get_current_user_role(request: Request) -> str:
    """
    Extract role from JWT token in Authorization header.
    
    Args:
        request: FastAPI request object
        
    Returns:
        User role (driver, merchant, admin)
        
    Raises:
        HTTPException: If token is missing, invalid, or role is missing
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
            audience="nerava-api", issuer="nerava",
            options={"verify_aud": True, "verify_iss": True},
        )
        role = payload.get("role")
        
        if not role:
            # Fallback: try to determine role from user_id or other claims
            # For backward compatibility, default to "driver" if no role claim
            role = "driver"
        
        return role
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )


def require_role(required_role: str):
    """
    Dependency factory to require a specific role.
    
    Usage:
        @router.get("/endpoint")
        async def endpoint(role: str = Depends(require_role("merchant"))):
            ...
    
    Args:
        required_role: Required role (driver, merchant, admin)
        
    Returns:
        Dependency function
    """
    def role_checker(request: Request) -> str:
        role = get_current_user_role(request)
        
        # Admin can access everything
        if role == "admin":
            return role
        
        if role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required"
            )
        
        return role
    
    return role_checker


def require_driver_role(request: Request) -> str:
    """Dependency to require driver role"""
    return require_role("driver")(request)


def require_merchant_role(request: Request) -> str:
    """Dependency to require merchant role"""
    return require_role("merchant")(request)


def require_admin_role(request: Request) -> str:
    """Dependency to require admin role"""
    return require_role("admin")(request)







