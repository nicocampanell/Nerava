"""
Mock Tesla Fleet API Router for Local Development.

Provides endpoints to simulate Tesla Fleet API behavior for testing.
Only available when TESLA_MOCK_MODE=true or DEBUG=true.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.services.mock_tesla_fleet_api import (
    get_mock_tesla_client,
    is_mock_mode_enabled,
    reset_mock_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mock-tesla", tags=["mock-tesla"])


def require_mock_mode():
    """Dependency to ensure mock mode is enabled."""
    if not is_mock_mode_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mock Tesla API is only available in development/testing mode",
        )


# ─── Request Models ────────────────────────────────────────────────

class SimulatePairingRequest(BaseModel):
    provisioning_token: str
    vehicle_id: str = "MOCK_VEHICLE_001"


class SimulateArrivalRequest(BaseModel):
    vehicle_id: str = "MOCK_VEHICLE_001"
    lat: float
    lng: float


class SetVehicleLocationRequest(BaseModel):
    vehicle_id: str = "MOCK_VEHICLE_001"
    lat: float
    lng: float


class SetVehicleBatteryRequest(BaseModel):
    vehicle_id: str = "MOCK_VEHICLE_001"
    battery_level: int
    charging_state: str = "Charging"


class AddVehicleRequest(BaseModel):
    vehicle_id: str
    vin: str
    display_name: str


# ─── Endpoints ──────────────────────────────────────────────────────

@router.get("/status", dependencies=[Depends(require_mock_mode)])
async def mock_status():
    """Check if mock Tesla API is active."""
    client = get_mock_tesla_client()
    return {
        "mock_mode": True,
        "vehicles": list(client.state.vehicles.keys()),
        "pending_pairings": len(client.state.pending_pairings),
        "webhook_callbacks_sent": len(client.state.webhook_callbacks),
    }


@router.post("/register-pairing", dependencies=[Depends(require_mock_mode)])
async def register_mock_pairing(req: SimulatePairingRequest):
    """
    Register a pairing request to be simulated.

    Call this after calling POST /v1/virtual-key/provision to set up
    the mock to respond when pairing is completed.
    """
    client = get_mock_tesla_client()
    client.register_pairing_request(req.provisioning_token, req.vehicle_id)
    return {
        "status": "registered",
        "provisioning_token": req.provisioning_token[:8] + "...",
        "vehicle_id": req.vehicle_id,
    }


@router.post("/complete-pairing", dependencies=[Depends(require_mock_mode)])
async def complete_mock_pairing(req: SimulatePairingRequest):
    """
    Simulate Tesla app completing the Virtual Key pairing.

    This triggers a webhook call to the Virtual Key webhook endpoint,
    as if Tesla sent it.
    """
    import httpx

    client = get_mock_tesla_client()

    try:
        # Get the webhook payload
        webhook_payload = await client.simulate_pairing_complete(
            provisioning_token=req.provisioning_token,
            callback_url=f"{settings.PUBLIC_BASE_URL}/v1/virtual-key/webhook/tesla",
        )

        # Actually call the webhook endpoint
        webhook_url = "http://localhost:8000/v1/virtual-key/webhook/tesla"
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(webhook_url, json=webhook_payload)
            webhook_result = response.json() if response.status_code == 200 else None

        return {
            "status": "pairing_completed",
            "webhook_payload": webhook_payload,
            "webhook_response": webhook_result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/simulate-arrival", dependencies=[Depends(require_mock_mode)])
async def simulate_vehicle_arrival(req: SimulateArrivalRequest):
    """
    Simulate vehicle arriving at a location.

    Updates the mock vehicle's position and sends a location webhook.
    """
    import httpx

    client = get_mock_tesla_client()

    try:
        webhook_payload = await client.simulate_vehicle_arrival(
            vehicle_id=req.vehicle_id,
            lat=req.lat,
            lng=req.lng,
            callback_url=f"{settings.PUBLIC_BASE_URL}/v1/virtual-key/webhook/tesla",
        )

        # Call the webhook endpoint
        webhook_url = "http://localhost:8000/v1/virtual-key/webhook/tesla"
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(webhook_url, json=webhook_payload)
            webhook_result = response.json() if response.status_code == 200 else None

        return {
            "status": "arrival_simulated",
            "vehicle_id": req.vehicle_id,
            "location": {"lat": req.lat, "lng": req.lng},
            "webhook_response": webhook_result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/set-vehicle-location", dependencies=[Depends(require_mock_mode)])
async def set_vehicle_location(req: SetVehicleLocationRequest):
    """Set mock vehicle location (without triggering webhook)."""
    client = get_mock_tesla_client()
    client.set_vehicle_location(req.vehicle_id, req.lat, req.lng)
    return {
        "status": "location_updated",
        "vehicle_id": req.vehicle_id,
        "location": {"lat": req.lat, "lng": req.lng},
    }


@router.post("/set-vehicle-battery", dependencies=[Depends(require_mock_mode)])
async def set_vehicle_battery(req: SetVehicleBatteryRequest):
    """Set mock vehicle battery state."""
    client = get_mock_tesla_client()
    client.set_vehicle_battery(req.vehicle_id, req.battery_level, req.charging_state)
    return {
        "status": "battery_updated",
        "vehicle_id": req.vehicle_id,
        "battery_level": req.battery_level,
        "charging_state": req.charging_state,
    }


@router.post("/add-vehicle", dependencies=[Depends(require_mock_mode)])
async def add_mock_vehicle(req: AddVehicleRequest):
    """Add a new mock vehicle for testing."""
    client = get_mock_tesla_client()
    vehicle = client.add_mock_vehicle(req.vehicle_id, req.vin, req.display_name)
    return {
        "status": "vehicle_added",
        "vehicle_id": vehicle.vehicle_id,
        "vin": vehicle.vin,
        "display_name": vehicle.display_name,
    }


@router.get("/vehicles", dependencies=[Depends(require_mock_mode)])
async def list_mock_vehicles():
    """List all mock vehicles."""
    client = get_mock_tesla_client()
    return {
        "vehicles": [
            {
                "vehicle_id": v.vehicle_id,
                "vin": v.vin,
                "display_name": v.display_name,
                "location": {"lat": v.lat, "lng": v.lng},
                "battery_level": v.battery_level,
                "charging_state": v.charging_state,
                "is_paired": v.is_paired,
            }
            for v in client.state.vehicles.values()
        ]
    }


@router.get("/vehicle/{vehicle_id}/data", dependencies=[Depends(require_mock_mode)])
async def get_mock_vehicle_data(vehicle_id: str):
    """Get mock vehicle telemetry data."""
    client = get_mock_tesla_client()
    try:
        data = await client.get_vehicle_data(vehicle_id, "mock_token")
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/webhooks", dependencies=[Depends(require_mock_mode)])
async def get_webhook_history():
    """Get history of webhook callbacks sent."""
    client = get_mock_tesla_client()
    return {
        "webhooks": client.get_webhook_callbacks(),
    }


@router.post("/reset", dependencies=[Depends(require_mock_mode)])
async def reset_mock_state_endpoint():
    """Reset all mock state (vehicles, pairings, webhooks)."""
    reset_mock_state()
    return {"status": "reset", "message": "Mock state has been reset"}
