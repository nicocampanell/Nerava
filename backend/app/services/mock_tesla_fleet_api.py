"""
Mock Tesla Fleet API Server for Local Development.

Simulates Tesla Fleet API responses for Virtual Key testing without a real Tesla Developer Account.
Enable with TESLA_MOCK_MODE=true in environment.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MockVehicle:
    """Mock Tesla vehicle state."""
    vehicle_id: str
    vin: str
    display_name: str
    lat: float = 30.3979  # Default: Canyon Ridge Supercharger
    lng: float = -97.7044
    battery_level: int = 65
    charging_state: str = "Charging"
    is_paired: bool = False
    paired_at: Optional[datetime] = None


@dataclass
class MockFleetState:
    """Mock Tesla Fleet API state for testing."""
    vehicles: Dict[str, MockVehicle] = field(default_factory=dict)
    pending_pairings: Dict[str, str] = field(default_factory=dict)  # token -> vehicle_id
    webhook_callbacks: List[Dict[str, Any]] = field(default_factory=list)


# Global mock state
_mock_state: Optional[MockFleetState] = None


def get_mock_state() -> MockFleetState:
    """Get or create global mock state."""
    global _mock_state
    if _mock_state is None:
        _mock_state = MockFleetState()
        # Initialize with a default test vehicle
        _mock_state.vehicles["MOCK_VEHICLE_001"] = MockVehicle(
            vehicle_id="MOCK_VEHICLE_001",
            vin="5YJ3E1EA1NF000001",
            display_name="James's Model Y",
        )
    return _mock_state


def reset_mock_state():
    """Reset mock state (for testing)."""
    global _mock_state
    _mock_state = None


class MockTeslaFleetAPIClient:
    """
    Mock Tesla Fleet API client for local development and testing.

    Simulates all Tesla Fleet API operations without network calls.
    """

    def __init__(self):
        self.state = get_mock_state()
        self.base_url = "http://localhost:8000/mock-tesla"  # Local mock endpoint
        logger.info("Initialized MockTeslaFleetAPIClient for local testing")

    async def generate_partner_token(self) -> str:
        """Generate mock partner token."""
        token = f"mock_partner_token_{uuid.uuid4().hex[:16]}"
        logger.info(f"Generated mock partner token: {token[:20]}...")
        return token

    async def register_partner(self) -> Dict[str, Any]:
        """Mock partner registration."""
        return {
            "status": "registered",
            "partner_id": "mock_partner_nerava",
            "registered_at": datetime.utcnow().isoformat(),
        }

    async def get_vehicle_data(self, vehicle_id: str, token: str) -> Dict[str, Any]:
        """Get mock vehicle telemetry data."""
        vehicle = self.state.vehicles.get(vehicle_id)
        if not vehicle:
            raise ValueError(f"Vehicle {vehicle_id} not found")

        return {
            "response": {
                "id": vehicle.vehicle_id,
                "vin": vehicle.vin,
                "display_name": vehicle.display_name,
                "state": "online",
                "charge_state": {
                    "battery_level": vehicle.battery_level,
                    "charging_state": vehicle.charging_state,
                    "time_to_full_charge": 0.5,
                    "charge_limit_soc": 80,
                },
                "drive_state": {
                    "latitude": vehicle.lat,
                    "longitude": vehicle.lng,
                    "heading": 180,
                    "speed": 0,
                    "timestamp": int(datetime.utcnow().timestamp() * 1000),
                },
                "vehicle_state": {
                    "locked": True,
                    "odometer": 12500.5,
                    "car_version": "2025.44.6",
                },
            }
        }

    async def get_vehicle_location(self, vehicle_id: str, token: str) -> Dict[str, Any]:
        """Get mock vehicle location."""
        vehicle = self.state.vehicles.get(vehicle_id)
        if not vehicle:
            raise ValueError(f"Vehicle {vehicle_id} not found")

        return {
            "response": {
                "latitude": vehicle.lat,
                "longitude": vehicle.lng,
                "heading": 180,
                "speed": 0,
                "timestamp": int(datetime.utcnow().timestamp() * 1000),
            }
        }

    async def send_command(self, vehicle_id: str, command: str, token: str) -> bool:
        """Send mock command to vehicle."""
        vehicle = self.state.vehicles.get(vehicle_id)
        if not vehicle:
            raise ValueError(f"Vehicle {vehicle_id} not found")

        logger.info(f"Mock command '{command}' sent to vehicle {vehicle_id}")
        return True

    # ─── Mock-Specific Methods for Testing ────────────────────────────

    def register_pairing_request(self, provisioning_token: str, vehicle_id: str = "MOCK_VEHICLE_001"):
        """
        Register a pairing request for simulation.

        Call this to set up a pairing that will be "completed" when simulate_pairing_complete is called.
        """
        self.state.pending_pairings[provisioning_token] = vehicle_id
        logger.info(f"Mock: Registered pairing request {provisioning_token[:8]}... for vehicle {vehicle_id}")

    async def simulate_pairing_complete(self, provisioning_token: str, callback_url: str) -> Dict[str, Any]:
        """
        Simulate Tesla app completing the pairing.

        This sends a webhook callback to the Nerava backend as if Tesla sent it.
        """
        vehicle_id = self.state.pending_pairings.get(provisioning_token)
        if not vehicle_id:
            raise ValueError(f"No pending pairing for token {provisioning_token[:8]}...")

        vehicle = self.state.vehicles.get(vehicle_id)
        if not vehicle:
            raise ValueError(f"Vehicle {vehicle_id} not found")

        # Mark vehicle as paired
        vehicle.is_paired = True
        vehicle.paired_at = datetime.utcnow()

        # Build webhook payload
        webhook_payload = {
            "type": "vehicle_paired",
            "token": provisioning_token,
            "vehicle_id": vehicle_id,
            "vin": vehicle.vin,
            "vehicle_name": vehicle.display_name,
        }

        # Store callback for testing
        self.state.webhook_callbacks.append(webhook_payload)

        # Remove from pending
        del self.state.pending_pairings[provisioning_token]

        logger.info(f"Mock: Simulated pairing complete for {provisioning_token[:8]}... -> {vehicle_id}")

        return webhook_payload

    async def simulate_vehicle_arrival(
        self,
        vehicle_id: str,
        lat: float,
        lng: float,
        callback_url: str
    ) -> Dict[str, Any]:
        """
        Simulate vehicle arriving at a location.

        Updates vehicle position and sends location webhook.
        """
        vehicle = self.state.vehicles.get(vehicle_id)
        if not vehicle:
            raise ValueError(f"Vehicle {vehicle_id} not found")

        # Update vehicle location
        vehicle.lat = lat
        vehicle.lng = lng

        # Build webhook payload
        webhook_payload = {
            "type": "vehicle_location",
            "vehicle_id": vehicle_id,
            "latitude": lat,
            "longitude": lng,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self.state.webhook_callbacks.append(webhook_payload)

        logger.info(f"Mock: Vehicle {vehicle_id} arrived at ({lat}, {lng})")

        return webhook_payload

    def set_vehicle_location(self, vehicle_id: str, lat: float, lng: float):
        """Set mock vehicle location."""
        vehicle = self.state.vehicles.get(vehicle_id)
        if vehicle:
            vehicle.lat = lat
            vehicle.lng = lng
            logger.info(f"Mock: Set vehicle {vehicle_id} location to ({lat}, {lng})")

    def set_vehicle_battery(self, vehicle_id: str, battery_level: int, charging_state: str = "Charging"):
        """Set mock vehicle battery state."""
        vehicle = self.state.vehicles.get(vehicle_id)
        if vehicle:
            vehicle.battery_level = battery_level
            vehicle.charging_state = charging_state
            logger.info(f"Mock: Set vehicle {vehicle_id} battery to {battery_level}% ({charging_state})")

    def add_mock_vehicle(self, vehicle_id: str, vin: str, display_name: str) -> MockVehicle:
        """Add a new mock vehicle."""
        vehicle = MockVehicle(
            vehicle_id=vehicle_id,
            vin=vin,
            display_name=display_name,
        )
        self.state.vehicles[vehicle_id] = vehicle
        logger.info(f"Mock: Added vehicle {vehicle_id} ({display_name})")
        return vehicle

    def get_webhook_callbacks(self) -> List[Dict[str, Any]]:
        """Get list of webhook callbacks sent (for testing assertions)."""
        return self.state.webhook_callbacks.copy()

    def clear_webhook_callbacks(self):
        """Clear webhook callback history."""
        self.state.webhook_callbacks.clear()


# ─── Factory Function ────────────────────────────────────────────────

_mock_client: Optional[MockTeslaFleetAPIClient] = None


def get_mock_tesla_client() -> MockTeslaFleetAPIClient:
    """Get singleton mock Tesla client."""
    global _mock_client
    if _mock_client is None:
        _mock_client = MockTeslaFleetAPIClient()
    return _mock_client


def is_mock_mode_enabled() -> bool:
    """Check if Tesla mock mode is enabled."""
    return getattr(settings, 'TESLA_MOCK_MODE', False) or \
           getattr(settings, 'TESTING', False) or \
           getattr(settings, 'DEBUG', False)
