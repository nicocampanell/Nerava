"""
Admin Tesla Fleet API tooling.

Temporary admin endpoint for one-off Tesla Fleet API inspection calls
(e.g. verifying `nearby_charging_sites` response shape for a specific
user's Tesla). Requires admin role.
"""

import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.dependencies_domain import require_admin
from app.models.tesla_connection import TeslaConnection
from app.models.user import User
from app.services.tesla_oauth import get_tesla_oauth_service, get_valid_access_token
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-tesla"])


@router.get("/v1/admin/tesla/nearby-charging-sites")
async def admin_nearby_charging_sites(
    user_id: Optional[str] = Query(
        None,
        description="Target user UUID whose Tesla connection to use. Defaults to admin caller's own.",
    ),
    phone: Optional[str] = Query(
        None,
        description="Target user phone (any format). Used only if user_id is not provided.",
    ),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Call Tesla Fleet API `nearby_charging_sites` for a target user's Tesla.

    Precedence: `user_id` query param > `phone` query param > admin caller's own.
    Admin-only endpoint; cross-user lookup is intentional for multi-account
    owners (e.g. admin email account + separate driver phone account on the
    same physical Tesla).
    """
    # Resolve the target user
    target_user: Optional[User] = None
    if user_id:
        target_user = db.query(User).filter(User.id == user_id).first()
        if not target_user:
            raise HTTPException(
                status_code=404,
                detail=f"No user found with id {user_id}",
            )
    elif phone:
        try:
            normalized = normalize_phone(phone)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse phone number: {exc}",
            ) from exc
        target_user = db.query(User).filter(User.phone == normalized).first()
        if not target_user:
            raise HTTPException(
                status_code=404,
                detail=f"No user found with phone {normalized}",
            )
    else:
        target_user = current_user

    connection = (
        db.query(TeslaConnection)
        .filter(
            TeslaConnection.user_id == target_user.id,
            TeslaConnection.is_active == True,  # noqa: E712
        )
        .first()
    )

    if not connection:
        raise HTTPException(
            status_code=404,
            detail=f"No active Tesla connection for user {target_user.id}",
        )
    if not connection.vehicle_id:
        raise HTTPException(
            status_code=400,
            detail="Tesla connection has no vehicle_id selected",
        )

    oauth = get_tesla_oauth_service()
    access_token = await get_valid_access_token(db, connection, oauth)
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Tesla session expired — reconnect required",
        )

    vehicle_id = connection.vehicle_id

    # Best effort wake (endpoint needs an online vehicle). Ignore wake errors
    # and let the actual call surface a 408 if the car is still asleep.
    try:
        await oauth.wake_vehicle(access_token, vehicle_id)
    except Exception as exc:
        logger.info(
            "admin_nearby_charging_sites: wake_vehicle pre-call failed (continuing): %s",
            exc,
        )

    try:
        response = await oauth.get_nearby_charging_sites(access_token, vehicle_id)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "admin_nearby_charging_sites: Tesla API returned HTTP %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=f"Tesla Fleet API error: HTTP {exc.response.status_code}",
        ) from exc
    except Exception as exc:
        logger.exception("admin_nearby_charging_sites: unexpected error")
        raise HTTPException(
            status_code=502,
            detail=f"Tesla Fleet API call failed: {exc}",
        ) from exc

    superchargers = response.get("superchargers") or []
    destination = response.get("destination_charging") or []

    # Quick field audit so the caller can see at a glance whether the
    # available_stalls / total_stalls fields are still populated in 2026.
    sample_super = superchargers[0] if superchargers else None
    sample_fields = list(sample_super.keys()) if sample_super else []
    has_available_stalls = sample_super is not None and "available_stalls" in sample_super
    has_total_stalls = sample_super is not None and "total_stalls" in sample_super

    # Try to surface the Market Heights (Harker Heights) Supercharger if present.
    market_heights: Optional[Dict[str, Any]] = None
    for sc in superchargers:
        name = (sc.get("name") or "").lower()
        if "market heights" in name or "harker heights" in name:
            market_heights = sc
            break

    return {
        "target_user_id": target_user.id,
        "target_user_email": target_user.email,
        "target_user_phone_last4": (target_user.phone[-4:] if target_user.phone else None),
        "vehicle_id": vehicle_id,
        "vin_masked": (connection.vin[:5] + "…" + connection.vin[-4:]) if connection.vin else None,
        "timestamp": response.get("timestamp"),
        "congestion_sync_time_utc_secs": response.get("congestion_sync_time_utc_secs"),
        "supercharger_count": len(superchargers),
        "destination_count": len(destination),
        "sample_supercharger_fields": sample_fields,
        "has_available_stalls_field": has_available_stalls,
        "has_total_stalls_field": has_total_stalls,
        "market_heights_site": market_heights,
        "superchargers": superchargers,
        "destination_charging": destination,
    }
