# LEGACY: This file has been moved to app/models/vehicle.py
# Import from new location for backward compatibility
from .models.vehicle import (
    VehicleAccount,
    VehicleTelemetry,
    VehicleToken,
)

__all__ = [
    "VehicleAccount",
    "VehicleToken",
    "VehicleTelemetry",
]
