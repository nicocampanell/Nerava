"""
EV Telemetry polling service
Polls Smartcar for vehicle telemetry and stores it
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models_vehicle import VehicleAccount, VehicleTelemetry, VehicleToken
from app.services.smartcar_service import (
    get_vehicle_charge,
    get_vehicle_location,
    refresh_tokens,
)

logger = logging.getLogger(__name__)


async def poll_vehicle_telemetry_for_account(
    db: Session, account: VehicleAccount
) -> VehicleTelemetry:
    """
    Poll Smartcar for vehicle telemetry and store it
    
    Args:
        db: Database session
        account: VehicleAccount to poll
        
    Returns:
        VehicleTelemetry record with fresh data
        
    Raises:
        Exception: If polling fails
    """
    # Check if account has any tokens before attempting refresh
    latest_token = (
        db.query(VehicleToken)
        .filter(VehicleToken.vehicle_account_id == account.id)
        .order_by(desc(VehicleToken.created_at))
        .first()
    )
    if not latest_token:
        raise ValueError(f"No token found for vehicle account {account.id}")
    
    # Get valid access token (refresh if needed)
    token = await refresh_tokens(db, account)
    
    # Decrypt access token before using (P0 security fix)
    from app.services.token_encryption import decrypt_token
    try:
        decrypted_access_token = decrypt_token(token.access_token)
    except Exception:
        # If decryption fails, assume it's plaintext (migration compatibility)
        decrypted_access_token = token.access_token
    
    # Poll charge and location
    charge_data = await get_vehicle_charge(decrypted_access_token, account.provider_vehicle_id)
    location_data = await get_vehicle_location(decrypted_access_token, account.provider_vehicle_id)
    
    # Map Smartcar fields to our schema
    # Smartcar charge API returns: stateOfCharge (0-100), isPluggedIn, state (CHARGING, FULLY_CHARGED, NOT_CHARGING)
    # Handle both nested {"value": X} format and direct value format
    state_of_charge = charge_data.get("stateOfCharge")
    if isinstance(state_of_charge, dict):
        soc_pct = state_of_charge.get("value")
    else:
        soc_pct = state_of_charge
    
    state = charge_data.get("state")
    if isinstance(state, dict):
        charging_state = state.get("value")
    else:
        charging_state = state  # Already a string like "CHARGING", "FULLY_CHARGED", "NOT_CHARGING"
    
    # Smartcar location API returns: latitude, longitude
    # Handle both nested {"value": X} format and direct value format
    lat = location_data.get("latitude")
    if isinstance(lat, dict):
        latitude = lat.get("value")
    else:
        latitude = lat
    
    lng = location_data.get("longitude")
    if isinstance(lng, dict):
        longitude = lng.get("value")
    else:
        longitude = lng
    
    # Create telemetry record
    telemetry = VehicleTelemetry(
        id=str(uuid.uuid4()),
        vehicle_account_id=account.id,
        recorded_at=datetime.utcnow(),
        soc_pct=soc_pct,
        charging_state=charging_state,
        latitude=latitude,
        longitude=longitude,
        raw_json={
            "charge": charge_data,
            "location": location_data,
        },
    )
    
    db.add(telemetry)
    db.commit()
    db.refresh(telemetry)
    
    logger.info(f"Polled telemetry for vehicle account {account.id}: SOC={soc_pct}%, state={charging_state}")
    
    return telemetry


async def poll_all_active_vehicles(db: Session) -> list[VehicleTelemetry]:
    """
    Poll all active vehicle accounts
    
    Args:
        db: Database session
        
    Returns:
        List of VehicleTelemetry records created
    """
    accounts = (
        db.query(VehicleAccount)
        .filter(VehicleAccount.is_active == True)
        .all()
    )
    
    results = []
    for account in accounts:
        try:
            telemetry = await poll_vehicle_telemetry_for_account(db, account)
            results.append(telemetry)
        except Exception as e:
            logger.error(f"Failed to poll vehicle {account.id}: {e}", exc_info=True)
            # Continue with other vehicles
    
    return results

