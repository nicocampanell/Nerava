"""
Feature flags and configuration management
"""
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_current_user, get_current_user_role
from app.security.rbac import Permission, Role, rbac_manager


def _get_environment() -> str:
    """Get environment name from settings, mapping to flag environment keys."""
    env = os.getenv("ENV", "dev").lower()
    env_map = {"prod": "production", "staging": "staging", "production": "production"}
    return env_map.get(env, "development")

logger = logging.getLogger(__name__)

router = APIRouter(tags=["flags"])

# Feature flags configuration
FEATURE_FLAGS = {
    "enable_sync_credit": {
        "description": "Enable synchronous wallet credit processing",
        "default": False,
        "environments": {
            "development": True,
            "staging": False,
            "production": False,
        }
    },
    "enable_green_hour": {
        "description": "Enable Green Hour feature",
        "default": True,
        "environments": {
            "development": True,
            "staging": True,
            "production": True,
        }
    },
    "enable_analytics": {
        "description": "Enable analytics collection",
        "default": True,
        "environments": {
            "development": True,
            "staging": True,
            "production": True,
        }
    },
    "enable_async_wallet": {
        "description": "Enable asynchronous wallet processing",
        "default": True,
        "environments": {
            "development": True,
            "staging": True,
            "production": True,
        }
    },
    "enable_circuit_breaker": {
        "description": "Enable circuit breaker for external services",
        "default": True,
        "environments": {
            "development": False,
            "staging": True,
            "production": True,
        }
    },
    "enable_rate_limiting": {
        "description": "Enable rate limiting",
        "default": True,
        "environments": {
            "development": False,
            "staging": True,
            "production": True,
        }
    },
    "enable_audit_logging": {
        "description": "Enable audit logging",
        "default": True,
        "environments": {
            "development": False,
            "staging": True,
            "production": True,
        }
    },
    "enable_multi_region": {
        "description": "Enable multi-region features",
        "default": False,
        "environments": {
            "development": False,
            "staging": False,
            "production": True,
        }
    },
}

@router.get("/v1/flags")
async def get_feature_flags(
    user_id: str = Depends(get_current_user),
    user_role: Role = Depends(get_current_user_role)
):
    """Get feature flags for the current user"""
    try:
        # Check permissions
        rbac_manager.require_permission(user_role, Permission.VIEW_ANALYTICS)
        
        # Get environment (in production, this would come from config)
        environment = _get_environment()
        
        # Build flags response
        flags = {}
        for flag_name, flag_config in FEATURE_FLAGS.items():
            flags[flag_name] = {
                "enabled": flag_config["environments"].get(environment, flag_config["default"]),
                "description": flag_config["description"],
            }
        
        return {
            "flags": flags,
            "environment": environment,
            "user_id": user_id,
            "user_role": user_role.value,
        }
        
    except Exception as e:
        logger.error(f"Error getting feature flags: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get feature flags"
        )

@router.get("/v1/flags/{flag_name}")
async def get_feature_flag(
    flag_name: str,
    user_id: str = Depends(get_current_user),
    user_role: Role = Depends(get_current_user_role)
):
    """Get a specific feature flag"""
    try:
        # Check permissions
        rbac_manager.require_permission(user_role, Permission.VIEW_ANALYTICS)
        
        if flag_name not in FEATURE_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Feature flag '{flag_name}' not found"
            )
        
        flag_config = FEATURE_FLAGS[flag_name]
        environment = _get_environment()
        
        return {
            "flag_name": flag_name,
            "enabled": flag_config["environments"].get(environment, flag_config["default"]),
            "description": flag_config["description"],
            "environment": environment,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting feature flag {flag_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get feature flag"
        )

@router.post("/v1/flags/{flag_name}/toggle")
async def toggle_feature_flag(
    flag_name: str,
    enabled: bool,
    user_id: str = Depends(get_current_user),
    user_role: Role = Depends(get_current_user_role)
):
    """Toggle a feature flag (admin only)"""
    try:
        # Check permissions - only admin can toggle flags
        rbac_manager.require_permission(user_role, Permission.MANAGE_SYSTEM)
        
        if flag_name not in FEATURE_FLAGS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Feature flag '{flag_name}' not found"
            )
        
        # In production, this would update the flag in a database or configuration service
        # For now, we'll just return the requested state
        logger.info(f"Feature flag {flag_name} toggled to {enabled} by user {user_id}")
        
        return {
            "flag_name": flag_name,
            "enabled": enabled,
            "toggled_by": user_id,
            "message": f"Feature flag '{flag_name}' set to {enabled}",
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling feature flag {flag_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to toggle feature flag"
        )

def is_feature_enabled(flag_name: str, environment: Optional[str] = None) -> bool:
    """Check if a feature flag is enabled"""
    if environment is None:
        environment = _get_environment()
    if flag_name not in FEATURE_FLAGS:
        return False

    flag_config = FEATURE_FLAGS[flag_name]
    return flag_config["environments"].get(environment, flag_config["default"])

def get_feature_flag_value(flag_name: str, environment: Optional[str] = None) -> Optional[Any]:
    """Get the value of a feature flag"""
    if environment is None:
        environment = _get_environment()
    if flag_name not in FEATURE_FLAGS:
        return None

    flag_config = FEATURE_FLAGS[flag_name]
    return flag_config["environments"].get(environment, flag_config["default"])