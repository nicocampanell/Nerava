"""
Public configuration endpoint

Returns non-sensitive configuration values that the frontend needs.
"""
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..models import FeatureFlag

router = APIRouter(prefix="/v1/public", tags=["config"])


class ConfigResponse(BaseModel):
    """Public configuration response"""
    google_client_id: str
    apple_client_id: str
    env: str
    api_base: str


class LocationEducationResponse(BaseModel):
    """Location permission education response"""
    title: str
    message: str
    privacy_note: str


@router.get("/config", response_model=ConfigResponse)
def get_public_config():
    """
    Get public configuration values for frontend.
    
    Returns Google Client ID, Apple Client ID, and other non-sensitive configuration.
    """
    # Get api_base from PUBLIC_BASE_URL or FRONTEND_URL
    api_base = os.getenv("PUBLIC_BASE_URL", "") or settings.FRONTEND_URL
    
    return ConfigResponse(
        google_client_id=settings.GOOGLE_CLIENT_ID or "",
        apple_client_id=settings.APPLE_CLIENT_ID or "",
        env=settings.ENV,
        api_base=api_base
    )


@router.get(
    "/location-education",
    response_model=LocationEducationResponse,
    summary="Get location permission education copy",
    description="""
    Get location permission education copy for frontend display.
    
    Returns editable copy that can be updated via FeatureFlag table without redeploy.
    Use this copy BEFORE requesting location permission from the user.
    """
)
def get_location_education(db: Session = Depends(get_db)):
    """
    Get location permission education copy.
    
    Returns editable copy that can be updated via FeatureFlag table without redeploy.
    """
    # Try to get from FeatureFlag table (editable without redeploy)
    location_education_flag = (
        db.query(FeatureFlag)
        .filter(FeatureFlag.key == "location_education_copy")
        .first()
    )
    
    if location_education_flag and location_education_flag.value:
        # Parse JSON value if stored as JSON
        try:
            import json
            copy_data = json.loads(location_education_flag.value)
            return LocationEducationResponse(
                title=copy_data.get("title", "Location Permission"),
                message=copy_data.get("message", ""),
                privacy_note=copy_data.get("privacy_note", ""),
            )
        except Exception:
            pass
    
    # Default copy (fallback)
    return LocationEducationResponse(
        title="Location Permission",
        message=(
            "We only use your location to see if you're near a public charger "
            "and show walkable places nearby."
        ),
        privacy_note="We do not track you.",
    )

