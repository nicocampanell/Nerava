"""
Schemas for Tesla Fleet Telemetry webhook payloads.

Fleet Telemetry dispatches vehicle data via HTTP POST when field values change.
"""
from typing import Any, Optional

from pydantic import BaseModel


class TelemetryValue(BaseModel):
    """A single telemetry field from the vehicle."""
    key: str          # e.g. "DetailedChargeState", "BatteryLevel"
    value: Any = None


class TelemetryPayload(BaseModel):
    """Payload dispatched by Fleet Telemetry server."""
    vin: str
    data: list[TelemetryValue] = []
    created_at: Optional[str] = None
    msg_type: Optional[str] = None
