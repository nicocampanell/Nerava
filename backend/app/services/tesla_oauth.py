"""
Tesla OAuth2 service for user authentication and token management.

Handles the complete OAuth2 flow for connecting user's Tesla account.
"""

import asyncio
import logging
import httpx
import secrets
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from urllib.parse import urlencode
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.token_encryption import encrypt_token, decrypt_token
from app.models.tesla_connection import TeslaConnection

logger = logging.getLogger(__name__)

# Tesla OAuth endpoints
# Authorization: auth.tesla.com (user-facing consent screen)
# Token exchange: fleet-auth.prd.vn.cloud.tesla.com (server-to-server)
TESLA_AUTH_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TESLA_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
TESLA_FLEET_API_URL = "https://fleet-api.prd.na.vn.cloud.tesla.com"

# OAuth scopes for charging verification
TESLA_SCOPES = [
    "openid",
    "offline_access",
    "user_data",
    "vehicle_device_data",
    "vehicle_location",
    "vehicle_charging_cmds",
]


class TeslaOAuthService:
    """Service for Tesla OAuth2 authentication."""

    def __init__(self):
        self.client_id = settings.TESLA_CLIENT_ID
        self.client_secret = settings.TESLA_CLIENT_SECRET
        self.redirect_uri = f"{settings.API_BASE_URL}/v1/auth/tesla/callback"

    def get_authorization_url(self, state: str, redirect_uri: Optional[str] = None) -> str:
        """
        Generate Tesla OAuth authorization URL.

        Args:
            state: CSRF state token to verify callback
            redirect_uri: Override redirect URI (e.g. for login vs connect flow)

        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(TESLA_SCOPES),
            "state": state,
        }
        return f"{TESLA_AUTH_URL}?{urlencode(params)}"

    async def exchange_code_for_tokens(
        self, code: str, redirect_uri: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from callback
            redirect_uri: Override redirect URI (must match the one used in authorize)

        Returns:
            Token response with access_token, refresh_token, expires_in

        Raises:
            httpx.HTTPStatusError: If token exchange fails
        """
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TESLA_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh an expired access token.

        Args:
            refresh_token: Refresh token from previous auth

        Returns:
            New token response

        Raises:
            httpx.HTTPStatusError: If refresh fails
        """
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TESLA_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()

    async def get_vehicles(self, access_token: str) -> list:
        """
        Get list of vehicles associated with the Tesla account.

        Args:
            access_token: Valid Tesla access token

        Returns:
            List of vehicle objects

        Raises:
            httpx.HTTPStatusError: If API call fails
        """
        url = f"{TESLA_FLEET_API_URL}/api/1/vehicles"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("response", [])

    async def get_vehicle_data(self, access_token: str, vehicle_id: str) -> Dict[str, Any]:
        """
        Get vehicle telemetry data including charge state.

        Args:
            access_token: Valid Tesla access token
            vehicle_id: Tesla vehicle ID

        Returns:
            Vehicle data including charge_state, drive_state, etc.
        """
        url = f"{TESLA_FLEET_API_URL}/api/1/vehicles/{vehicle_id}/vehicle_data"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        params = {"endpoints": "charge_state;drive_state;location_data;vehicle_config"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            vehicle_resp = data.get("response", {})
            # Debug: log FULL raw charge_state from Fleet API
            # Captures all ~41 fields so we can compare adapter vs non-adapter sessions
            import json as _json

            raw_charge = vehicle_resp.get("charge_state", {})
            logger.info(
                f"Fleet API FULL charge_state for vehicle {vehicle_id}: "
                f"{_json.dumps(raw_charge, default=str)}"
            )
            return vehicle_resp

    async def get_nearby_charging_sites(
        self,
        access_token: str,
        vehicle_id: str,
    ) -> Dict[str, Any]:
        """
        Get Tesla Supercharger + Destination charging sites near the vehicle.

        Calls GET /api/1/vehicles/{vehicle_id}/nearby_charging_sites.
        Response is geographically scoped to the vehicle's current GPS location —
        there is no lat/lon override parameter. The response contains up to
        ~10-20 of the closest superchargers with live `available_stalls` and
        `total_stalls` fields (destination chargers do NOT have stall counts).

        Args:
            access_token: Valid Tesla access token with vehicle_device_data scope
            vehicle_id: Tesla vehicle ID (not VIN)

        Returns:
            Raw response dict with 'superchargers', 'destination_charging',
            'timestamp', 'congestion_sync_time_utc_secs'.

        Raises:
            httpx.HTTPStatusError: If the API call fails (408 if vehicle asleep).
        """
        url = f"{TESLA_FLEET_API_URL}/api/1/vehicles/{vehicle_id}/nearby_charging_sites"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("response", {})

    async def wake_vehicle(self, access_token: str, vehicle_id: str) -> bool:
        """
        Wake up a sleeping vehicle.

        Args:
            access_token: Valid Tesla access token
            vehicle_id: Tesla vehicle ID

        Returns:
            True if vehicle is awake
        """
        url = f"{TESLA_FLEET_API_URL}/api/1/vehicles/{vehicle_id}/wake_up"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("response", {}).get("state") == "online"

    async def subscribe_vehicle_telemetry(
        self,
        access_token: str,
        vin: str,
        hostname: str = "telemetry.nerava.network",
        port: int = 443,
    ) -> Dict[str, Any]:
        """
        Configure Fleet Telemetry streaming for a vehicle.

        Calls the Tesla Fleet API to set up the vehicle's telemetry config
        so it streams data to our Fleet Telemetry server via WebSocket.

        Args:
            access_token: Valid Tesla access token
            vin: Vehicle Identification Number
            hostname: Fleet Telemetry server hostname
            port: Fleet Telemetry server port

        Returns:
            API response dict

        Raises:
            httpx.HTTPStatusError: If API call fails
        """
        # Must go through Vehicle Command HTTP Proxy (signs requests with fleet key)
        proxy_url = settings.VEHICLE_COMMAND_PROXY_URL
        if proxy_url:
            base_url = proxy_url.rstrip("/")
        else:
            base_url = TESLA_FLEET_API_URL
        url = f"{base_url}/api/1/vehicles/fleet_telemetry_config"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # The ca field needs the full certificate PEM, not just the public key
        # Env vars may store PEM with escaped \n — convert back to real newlines
        ca_cert = settings.TESLA_FLEET_TELEMETRY_CA_CERT or settings.TESLA_EC_PUBLIC_KEY_PEM or ""
        if "\\n" in ca_cert:
            ca_cert = ca_cert.replace("\\n", "\n")

        config = {
            "vins": [vin],
            "config": {
                "hostname": hostname,
                "port": port,
                "ca": ca_cert,
                "fields": {
                    "DetailedChargeState": {"interval_seconds": 30},
                    "BatteryLevel": {"interval_seconds": 60},
                    "ACChargingPower": {"interval_seconds": 30},
                    "DCChargingPower": {"interval_seconds": 30},
                    "ACChargingEnergyIn": {"interval_seconds": 60},
                    "DCChargingEnergyIn": {"interval_seconds": 60},
                    "Location": {"interval_seconds": 60},
                    "ChargePortDoorOpen": {"interval_seconds": 30},
                    "FastChargerPresent": {"interval_seconds": 300},
                    "FastChargerType": {"interval_seconds": 300},
                },
                "alert_types": ["service"],
            },
        }

        # Proxy uses self-signed TLS — skip verification for internal calls
        verify_ssl = not bool(proxy_url)
        async with httpx.AsyncClient(timeout=30.0, verify=verify_ssl) as client:
            response = await client.post(url, headers=headers, json=config)
            if response.status_code >= 400:
                logger.error(
                    "Fleet Telemetry config failed for VIN %s: HTTP %s — %s",
                    vin,
                    response.status_code,
                    response.text,
                )
            response.raise_for_status()
            result = response.json()
            logger.info("Fleet Telemetry configured for VIN %s: %s", vin, result)
            return result

    # States the Tesla Fleet API reports when the vehicle is actively charging
    # or about to start (e.g. during initial power negotiation).
    CHARGING_STATES = {"Charging", "Starting"}

    async def verify_charging(
        self, access_token: str, vehicle_id: str
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Verify if vehicle is currently charging.

        Args:
            access_token: Valid Tesla access token
            vehicle_id: Tesla vehicle ID

        Returns:
            Tuple of (is_charging, charge_data)

        Raises:
            httpx.HTTPStatusError: Propagated so callers can retry on 408.
        """
        vehicle_data = await self.get_vehicle_data(access_token, vehicle_id)
        charge_state = vehicle_data.get("charge_state", {})

        charging_state_str = charge_state.get("charging_state")
        is_charging = charging_state_str in self.CHARGING_STATES
        charge_data = {
            "is_charging": is_charging,
            "charging_state": charging_state_str,
            "battery_level": charge_state.get("battery_level"),
            "charge_rate": charge_state.get("charge_rate"),
            "charger_power": charge_state.get("charger_power"),
            "minutes_to_full": charge_state.get("minutes_to_full_charge"),
            "supercharger": charge_state.get("fast_charger_present", False),
        }

        # Also get location
        drive_state = vehicle_data.get("drive_state", {})
        charge_data["latitude"] = drive_state.get("latitude")
        charge_data["longitude"] = drive_state.get("longitude")

        return is_charging, charge_data

    async def verify_charging_all_vehicles(
        self,
        access_token: str,
    ) -> Tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Check ALL vehicles on the account for charging status.

        Returns:
            Tuple of (is_charging, charge_data, vehicle_info).
            vehicle_info is the vehicle dict for the one found charging, or None.
        """
        vehicles = await self.get_vehicles(access_token)
        if not vehicles:
            return False, {"error": "No vehicles found on Tesla account"}, None

        last_charge_data: Dict[str, Any] = {}
        for vehicle in vehicles:
            vehicle_id = str(vehicle.get("id"))
            vin = vehicle.get("vin", "unknown")

            # Wake then verify, with retries on timeout or unknown state
            for attempt in range(3):
                try:
                    try:
                        await self.wake_vehicle(access_token, vehicle_id)
                    except Exception:
                        pass  # Continue even if wake fails

                    is_charging, charge_data = await self.verify_charging(access_token, vehicle_id)
                    last_charge_data = charge_data

                    if is_charging:
                        logger.info(f"Vehicle {vin} is charging")
                        return True, charge_data, vehicle

                    # If charging_state is None, vehicle may still be waking up
                    # — retry with a longer delay to let it fully come online
                    if charge_data.get("charging_state") is None and attempt < 2:
                        logger.warning(
                            f"Vehicle {vin} returned unknown state "
                            f"(attempt {attempt + 1}), retrying in 5s"
                        )
                        await asyncio.sleep(5)
                        continue

                    # Got a definitive state — move to next vehicle
                    break

                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code
                    if status_code == 408 and attempt < 2:
                        logger.warning(
                            f"Timeout checking vehicle {vin} "
                            f"(attempt {attempt + 1}), retrying in 5s"
                        )
                        await asyncio.sleep(5)
                        continue
                    if status_code == 429 and attempt < 2:
                        retry_after = int(e.response.headers.get("Retry-After", "10"))
                        wait_secs = min(retry_after, 30)
                        logger.warning(
                            f"Tesla API rate limited (429) for vehicle {vin} "
                            f"(attempt {attempt + 1}), retrying in {wait_secs}s"
                        )
                        await asyncio.sleep(wait_secs)
                        continue
                    logger.warning(f"HTTP error checking vehicle {vin}: {e}")
                    break
                except Exception as e:
                    logger.warning(f"Error checking vehicle {vin}: {e}")
                    break

        return False, last_charge_data, None


def generate_ev_code() -> str:
    """Generate a unique EV-XXXX verification code."""
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # Exclude confusing chars
    code_part = "".join(secrets.choice(chars) for _ in range(4))
    return f"EV-{code_part}"


async def get_valid_access_token(
    db: Session, connection: TeslaConnection, oauth_service: TeslaOAuthService
) -> Optional[str]:
    """
    Get a valid access token, refreshing if expired.

    Args:
        db: Database session
        connection: TeslaConnection record
        oauth_service: OAuth service instance

    Returns:
        Valid access token or None if refresh fails
    """
    # Check if token is still valid (with 5 min buffer)
    if connection.token_expires_at > datetime.utcnow() + timedelta(minutes=5):
        return decrypt_token(connection.access_token)

    # Token expired, refresh it
    try:
        raw_refresh = decrypt_token(connection.refresh_token)
        token_response = await oauth_service.refresh_access_token(raw_refresh)

        connection.access_token = encrypt_token(token_response["access_token"])
        new_refresh = token_response.get("refresh_token")
        if new_refresh:
            connection.refresh_token = encrypt_token(new_refresh)
        connection.token_expires_at = datetime.utcnow() + timedelta(
            seconds=token_response.get("expires_in", 3600)
        )
        connection.updated_at = datetime.utcnow()
        db.commit()

        return decrypt_token(connection.access_token)

    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            # Token revoked — mark connection inactive
            logger.warning(f"Tesla token revoked for user (HTTP {e.response.status_code})")
            connection.is_active = False
            connection.updated_at = datetime.utcnow()
            db.commit()
            return None
        # Transient error (5xx, timeout) — raise so caller can return 502
        logger.error(f"Tesla API temporarily unavailable: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to refresh Tesla token: {e}")
        return None


# Singleton instance
_tesla_oauth_service: Optional[TeslaOAuthService] = None


def get_tesla_oauth_service() -> TeslaOAuthService:
    """Get singleton Tesla OAuth service instance."""
    global _tesla_oauth_service
    if _tesla_oauth_service is None:
        _tesla_oauth_service = TeslaOAuthService()
    return _tesla_oauth_service
