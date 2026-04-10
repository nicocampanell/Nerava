"""
Authentication middleware for FastAPI
"""
import logging
import os

from app.config import settings
from app.security.jwt import jwt_manager
from app.security.rbac import Role, get_user_role
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Redis client for kill switch check
_redis_client = None

def _get_redis_client():
    """Get Redis client for kill switch"""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            if settings.redis_url:
                _redis_client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
                _redis_client.ping()  # Test connection
        except Exception as e:
            logger.warning(f"Failed to initialize Redis for kill switch: {e}")
            _redis_client = None
    return _redis_client

def _is_system_paused() -> bool:
    """Check if system is paused via Redis flag"""
    redis_client = _get_redis_client()
    if not redis_client:
        return False  # If Redis unavailable, don't pause system
    
    try:
        paused = redis_client.get("system:paused")
        return paused == "1" or paused == "true"
    except Exception as e:
        logger.warning(f"Failed to check system pause status: {e}")
        return False  # If check fails, don't pause system

security = HTTPBearer()

# Check if dev mode allows anonymous users
DEV_ALLOW_ANON_USER = os.getenv("NERAVA_DEV_ALLOW_ANON_USER", "false").lower() == "true"

class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware"""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.excluded_paths = {
            "/healthz",
            "/readyz",
            "/metrics",
            "/docs",
            "/openapi.json",
            "/v1/energyhub/windows",  # Public endpoint
            "/v1/energyhub/events/charge-start",  # Public endpoint
            "/v1/energyhub/events/charge-stop",  # Public endpoint
            "/oauth/smartcar/callback",  # Smartcar OAuth callback (no auth required)
            "/v1/drivers/merchants/open",  # Temporarily excluded for Asadas Grill demo
            "/v1/exclusive/complete",  # Temporarily excluded for Asadas Grill demo
            "/v1/pilot/party/cluster",  # Public party cluster endpoint
        }
        # Exclude all /app paths (UI static files)
        # Check if path starts with /app to allow all UI routes
        self.excluded_path_prefixes = [
            "/app",
            "/static",  # Static files (merchant photos, etc.)
            "/v1/auth",  # Auth endpoints are public
            "/v1/merchants",  # Merchant endpoints - auth handled at dependency level
            "/v1/exclusive",  # Exclusive endpoints - auth handled at dependency level
            "/v1/intent",  # Intent endpoints - auth handled at dependency level (supports anonymous)
        ]
    
    async def dispatch(self, request: Request, call_next):
        # Check kill switch: if system is paused, block non-admin endpoints
        if _is_system_paused():
            # Allow admin endpoints and health/metrics endpoints
            is_admin_endpoint = request.url.path.startswith("/v1/admin")
            is_health_endpoint = request.url.path in ["/healthz", "/readyz", "/metrics"]
            
            if not (is_admin_endpoint or is_health_endpoint):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="System is temporarily paused. Please try again later."
                )
        
        # Skip authentication for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)
        
        # Skip authentication for paths starting with excluded prefixes (e.g., /app for UI)
        for prefix in getattr(self, 'excluded_path_prefixes', []):
            if request.url.path.startswith(prefix):
                return await call_next(request)
        
        # In dev mode with NERAVA_DEV_ALLOW_ANON_USER, allow requests through
        # The endpoint dependency will handle creating a default user
        if DEV_ALLOW_ANON_USER:
            # Allow request through - endpoint dependency will handle dev user fallback
            return await call_next(request)
        
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization header"
            )
        
        token = auth_header[7:]  # Remove "Bearer " prefix
        
        try:
            # Verify token and extract user info
            payload = jwt_manager.verify_token(token)
            user_id = payload.get("user_id")
            
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token payload"
                )
            
            # Set user context in request state
            request.state.user_id = user_id
            request.state.user_role = get_user_role(user_id)
            
            logger.debug(f"Authenticated user: {user_id} with role: {request.state.user_role.value}")
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )
        
        response = await call_next(request)
        return response

def get_current_user(request: Request) -> str:
    """Get current user from request state"""
    if not hasattr(request.state, 'user_id'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    return request.state.user_id

def get_current_user_role(request: Request) -> Role:
    """Get current user role from request state"""
    if not hasattr(request.state, 'user_role'):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated"
        )
    return request.state.user_role

def require_role(required_role: Role):
    """Decorator to require a specific role"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Get request from kwargs (FastAPI dependency injection)
            request = kwargs.get('request')
            if not request:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Request context not available"
                )
            
            user_role = get_current_user_role(request)
            if user_role != required_role and user_role != Role.ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Role {required_role.value} required"
                )
            
            return func(*args, **kwargs)
        return wrapper
    return decorator
