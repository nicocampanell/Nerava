"""
Vehicle Onboarding Router
Handles vehicle onboarding endpoints
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.schemas.vehicle_onboarding import (
    VehicleOnboardingCompleteRequest,
    VehicleOnboardingCompleteResponse,
    VehicleOnboardingStartRequest,
    VehicleOnboardingStartResponse,
    VehicleOnboardingStatusResponse,
)
from app.services.vehicle_onboarding_service import (
    complete_onboarding,
    get_onboarding_status,
    start_onboarding,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/vehicle/onboarding", tags=["vehicle_onboarding"])


@router.post(
    "/start",
    response_model=VehicleOnboardingStartResponse,
    summary="Start vehicle onboarding",
    description="""
    Start vehicle onboarding process for trust reinforcement.
    
    Returns S3 signed URLs for uploading ~5 photos of the user's EV plugged in at a charger.
    Photos are stored securely in a private bucket with 90-day retention.
    """
)
async def start_vehicle_onboarding(
    request: VehicleOnboardingStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Start vehicle onboarding process.
    
    Returns S3 signed URLs for photo uploads.
    """
    try:
        onboarding = start_onboarding(
            db=db,
            user_id=current_user.id,
            intent_session_id=request.intent_session_id,
            charger_id=request.charger_id,
        )
        
        # Parse upload URLs from JSON
        upload_urls = json.loads(onboarding.photo_urls) if onboarding.photo_urls else []
        
        return VehicleOnboardingStartResponse(
            onboarding_id=onboarding.id,
            upload_urls=upload_urls,
            expires_at=onboarding.expires_at.isoformat() if onboarding.expires_at else "",
        )
    except Exception as e:
        logger.error(f"Error starting vehicle onboarding: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start vehicle onboarding",
        )


@router.post(
    "/complete",
    response_model=VehicleOnboardingCompleteResponse,
    summary="Complete vehicle onboarding",
    description="""
    Complete vehicle onboarding by submitting photo URLs.
    
    Validates that photos were uploaded and stores the URLs for manual review.
    Optional license plate extraction (no ML required).
    """
)
async def complete_vehicle_onboarding(
    request: VehicleOnboardingCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Complete vehicle onboarding by submitting photos.
    
    Validates that photos were uploaded and stores the URLs.
    """
    try:
        onboarding = complete_onboarding(
            db=db,
            onboarding_id=request.onboarding_id,
            user_id=current_user.id,
            photo_urls=request.photo_urls,
            license_plate=request.license_plate,
        )
        
        from app.core.copy import VEHICLE_ONBOARDING_EXPLANATION
        
        return VehicleOnboardingCompleteResponse(
            onboarding_id=onboarding.id,
            status=onboarding.status,
            message=VEHICLE_ONBOARDING_EXPLANATION["submitted"],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error completing vehicle onboarding: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete vehicle onboarding",
        )


@router.get(
    "/status",
    response_model=VehicleOnboardingStatusResponse,
    summary="Get vehicle onboarding status",
    description="""
    Get the current vehicle onboarding status for the authenticated user.
    
    Returns whether onboarding is required, and if so, the current status.
    """
)
async def get_vehicle_onboarding_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get vehicle onboarding status for the current user.
    
    Returns status and whether onboarding is required.
    """
    try:
        status_result = get_onboarding_status(db, current_user.id)
        
        return VehicleOnboardingStatusResponse(
            status=status_result["status"],
            required=status_result["required"],
        )
    except Exception as e:
        logger.error(f"Error getting vehicle onboarding status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get vehicle onboarding status",
        )

