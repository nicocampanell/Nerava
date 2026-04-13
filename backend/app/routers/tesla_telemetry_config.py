"""
Tesla Fleet Telemetry Configuration — Subscribe vehicles to telemetry streaming.

POST /v1/tesla/configure-telemetry

Called after Tesla OAuth vehicle selection to configure real-time telemetry
streaming from the vehicle to our Fleet Telemetry server.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.domain import get_current_user
from ..models.tesla_connection import TeslaConnection
from ..models.user import User
from ..services.tesla_oauth import (
    get_tesla_oauth_service,
    get_valid_access_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tesla", tags=["tesla-telemetry"])


@router.post("/configure-telemetry")
async def configure_telemetry(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Configure Fleet Telemetry streaming for the user's Tesla vehicle.

    This enables real-time charging detection without requiring the app
    to be open. The vehicle streams telemetry data (charge state, battery,
    location) to our Fleet Telemetry server via WebSocket.
    """
    # Get user's active Tesla connection
    tesla_conn = (
        db.query(TeslaConnection)
        .filter(
            TeslaConnection.user_id == current_user.id,
            TeslaConnection.is_active == True,
            TeslaConnection.deleted_at.is_(None),
        )
        .first()
    )
    if not tesla_conn:
        raise HTTPException(status_code=404, detail="No active Tesla connection")

    if not tesla_conn.vin:
        raise HTTPException(status_code=400, detail="No VIN associated with Tesla connection")

    # Get valid access token (refresh if needed)
    oauth_service = get_tesla_oauth_service()
    access_token = await get_valid_access_token(db, tesla_conn, oauth_service)
    if not access_token:
        raise HTTPException(status_code=401, detail="Tesla token expired — please reconnect")

    # Configure Fleet Telemetry
    try:
        result = await oauth_service.subscribe_vehicle_telemetry(
            access_token,
            tesla_conn.vin,
        )
    except Exception as e:
        detail_msg = str(e)
        # Extract response body from httpx errors for debugging
        if hasattr(e, "response") and e.response is not None:
            detail_msg = f"{e} — body: {e.response.text}"
        logger.error(
            "Failed to configure telemetry for user %s (VIN %s): %s",
            current_user.id,
            tesla_conn.vin,
            detail_msg,
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to configure telemetry with Tesla — charging detection will fall back to polling",
        )

    # Mark telemetry as enabled
    tesla_conn.telemetry_enabled = True
    tesla_conn.telemetry_configured_at = datetime.utcnow()
    tesla_conn.updated_at = datetime.utcnow()
    db.commit()

    logger.info(
        "Telemetry configured for user %s (VIN %s)",
        current_user.id,
        tesla_conn.vin,
    )
    return {
        "status": "configured",
        "vin": tesla_conn.vin,
        "telemetry_enabled": True,
    }
