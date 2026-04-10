# LEGACY: This file has been moved to app/dependencies/driver.py
# Import from new location for backward compatibility
from .dependencies.driver import (
    get_current_driver,
    get_current_driver_id,
    get_current_driver_optional,
)

__all__ = [
    "get_current_driver_id",
    "get_current_driver",
    "get_current_driver_optional",
]

