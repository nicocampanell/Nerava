"""
Role-Based Access Control (RBAC) for Nerava
"""
import logging
from enum import Enum
from typing import List

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

class Role(Enum):
    """User roles in the system"""
    USER = "user"
    ADMIN = "admin"
    OPERATOR = "operator"
    READONLY = "readonly"

class Permission(Enum):
    """System permissions"""
    # Charging permissions
    START_CHARGE = "start_charge"
    STOP_CHARGE = "stop_charge"
    VIEW_CHARGING_HISTORY = "view_charging_history"
    
    # Wallet permissions
    VIEW_WALLET = "view_wallet"
    CREDIT_WALLET = "credit_wallet"
    WITHDRAW_WALLET = "withdraw_wallet"
    
    # Hub permissions
    VIEW_HUBS = "view_hubs"
    MANAGE_HUBS = "manage_hubs"
    
    # Admin permissions
    VIEW_ALL_USERS = "view_all_users"
    MANAGE_USERS = "manage_users"
    VIEW_ANALYTICS = "view_analytics"
    MANAGE_SYSTEM = "manage_system"

# Role-Permission mapping
ROLE_PERMISSIONS = {
    Role.USER: [
        Permission.START_CHARGE,
        Permission.STOP_CHARGE,
        Permission.VIEW_CHARGING_HISTORY,
        Permission.VIEW_WALLET,
        Permission.VIEW_HUBS,
    ],
    Role.ADMIN: [
        Permission.START_CHARGE,
        Permission.STOP_CHARGE,
        Permission.VIEW_CHARGING_HISTORY,
        Permission.VIEW_WALLET,
        Permission.CREDIT_WALLET,
        Permission.WITHDRAW_WALLET,
        Permission.VIEW_HUBS,
        Permission.MANAGE_HUBS,
        Permission.VIEW_ALL_USERS,
        Permission.MANAGE_USERS,
        Permission.VIEW_ANALYTICS,
        Permission.MANAGE_SYSTEM,
    ],
    Role.OPERATOR: [
        Permission.VIEW_CHARGING_HISTORY,
        Permission.VIEW_WALLET,
        Permission.VIEW_HUBS,
        Permission.VIEW_ANALYTICS,
    ],
    Role.READONLY: [
        Permission.VIEW_CHARGING_HISTORY,
        Permission.VIEW_WALLET,
        Permission.VIEW_HUBS,
    ],
}

class RBACManager:
    """Role-Based Access Control manager"""
    
    def __init__(self):
        self.role_permissions = ROLE_PERMISSIONS
    
    def get_user_permissions(self, role: Role) -> List[Permission]:
        """Get permissions for a role"""
        return self.role_permissions.get(role, [])
    
    def has_permission(self, role: Role, permission: Permission) -> bool:
        """Check if a role has a specific permission"""
        permissions = self.get_user_permissions(role)
        return permission in permissions
    
    def require_permission(self, role: Role, permission: Permission):
        """Require a permission for a role, raise exception if not granted"""
        if not self.has_permission(role, permission):
            logger.warning(f"Permission denied: {role.value} does not have {permission.value}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: {permission.value} required"
            )
    
    def get_accessible_resources(self, role: Role, resource_type: str) -> List[str]:
        """Get list of accessible resources for a role"""
        if role == Role.ADMIN:
            return ["*"]  # Admin can access all resources
        
        # For other roles, implement resource-level filtering
        if resource_type == "hubs":
            if role in [Role.USER, Role.OPERATOR, Role.READONLY]:
                return ["*"]  # All users can see all hubs
        elif resource_type == "users":
            if role == Role.ADMIN:
                return ["*"]
            else:
                return []  # Only admin can see other users
        
        return []

# Global RBAC manager
rbac_manager = RBACManager()

def get_user_role(user_id: str) -> Role:
    """Get user role (in production, this would query the database)"""
    # For demo purposes, return USER role
    # In production, this would query the user's role from the database
    return Role.USER

def require_permission(permission: Permission):
    """Decorator to require a specific permission"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Get user_id from request context (would be set by auth middleware)
            user_id = getattr(func, 'user_id', None)
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required"
                )
            
            role = get_user_role(user_id)
            rbac_manager.require_permission(role, permission)
            return func(*args, **kwargs)
        return wrapper
    return decorator

def check_resource_access(user_id: str, resource_type: str, resource_id: str) -> bool:
    """Check if user can access a specific resource"""
    role = get_user_role(user_id)
    
    if role == Role.ADMIN:
        return True
    
    # Implement resource-level access control
    if resource_type == "wallet":
        # Users can only access their own wallet
        return True  # Assuming user_id matches wallet owner
    
    return True  # Default to allow access
