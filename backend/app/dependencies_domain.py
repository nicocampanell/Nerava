# LEGACY: This file has been moved to app/dependencies/domain.py
# Import from new location for backward compatibility
from .dependencies.domain import (
    get_current_user,
    get_current_user_id,
    get_current_user_optional,
    oauth2_scheme,
    require_admin,
    require_driver,
    require_merchant_admin,
    require_role,
)

__all__ = [
    "oauth2_scheme",
    "get_current_user_id",
    "get_current_user",
    "get_current_user_optional",
    "require_role",
    "require_driver",
    "require_merchant_admin",
    "require_admin",
]

