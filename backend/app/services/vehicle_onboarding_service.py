"""
Vehicle Onboarding Service
Handles vehicle onboarding photo uploads and status tracking
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import VehicleOnboarding
from app.services.s3_storage import generate_upload_urls

logger = logging.getLogger(__name__)


def start_onboarding(
    db: Session,
    user_id: int,
    intent_session_id: Optional[str] = None,
    charger_id: Optional[str] = None,
) -> VehicleOnboarding:
    """
    Start vehicle onboarding process.
    
    Args:
        db: Database session
        user_id: User ID
        intent_session_id: Optional intent session ID
        charger_id: Optional charger ID
    
    Returns:
        Created VehicleOnboarding record
    """
    # Generate upload URLs (5 photos)
    upload_urls = generate_upload_urls(count=5, prefix=f"vehicle-onboarding/{user_id}")
    
    # Calculate expiration (90 days retention)
    expires_at = datetime.utcnow() + timedelta(days=settings.VEHICLE_ONBOARDING_RETENTION_DAYS)
    
    # Create onboarding record
    onboarding = VehicleOnboarding(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status="SUBMITTED",
        photo_urls=json.dumps(upload_urls),  # Store as JSON string
        intent_session_id=intent_session_id,
        charger_id=charger_id,
        expires_at=expires_at,
    )
    
    db.add(onboarding)
    db.commit()
    db.refresh(onboarding)
    
    logger.info(f"Started vehicle onboarding {onboarding.id} for user {user_id}")
    
    return onboarding


def complete_onboarding(
    db: Session,
    onboarding_id: str,
    user_id: int,
    photo_urls: list,
    license_plate: Optional[str] = None,
) -> VehicleOnboarding:
    """
    Complete vehicle onboarding by submitting photos.
    
    Args:
        db: Database session
        onboarding_id: Onboarding ID
        user_id: User ID (for authorization check)
        photo_urls: List of S3 URLs of uploaded photos
        license_plate: Optional license plate
    
    Returns:
        Updated VehicleOnboarding record
    """
    # Find onboarding record
    onboarding = (
        db.query(VehicleOnboarding)
        .filter(
            VehicleOnboarding.id == onboarding_id,
            VehicleOnboarding.user_id == user_id,
        )
        .first()
    )
    
    if not onboarding:
        raise ValueError(f"Onboarding {onboarding_id} not found for user {user_id}")
    
    # Update with submitted photos
    onboarding.photo_urls = json.dumps(photo_urls)
    onboarding.license_plate = license_plate
    onboarding.status = "SUBMITTED"  # Ready for manual review
    onboarding.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(onboarding)
    
    logger.info(f"Completed vehicle onboarding {onboarding_id} with {len(photo_urls)} photos")
    
    return onboarding


def get_onboarding_status(db: Session, user_id: int) -> dict:
    """
    Get vehicle onboarding status for a user.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Dict with:
        - status: "not_required", "required", "submitted", "approved", "rejected"
        - required: bool
    """
    from app.core.config import settings
    from app.services.intent_service import get_intent_session_count
    
    # Get latest onboarding record
    latest_onboarding = (
        db.query(VehicleOnboarding)
        .filter(VehicleOnboarding.user_id == user_id)
        .order_by(VehicleOnboarding.created_at.desc())
        .first()
    )
    
    # Check session count
    session_count = get_intent_session_count(db, user_id)
    threshold = settings.INTENT_SESSION_ONBOARDING_THRESHOLD
    
    # If user has completed onboarding, return approved status
    if latest_onboarding and latest_onboarding.status == "APPROVED":
        return {
            "status": "approved",
            "required": False,
        }
    
    # If user has submitted but not approved, return submitted status
    if latest_onboarding and latest_onboarding.status in ["SUBMITTED", "PENDING_REVIEW"]:
        return {
            "status": "submitted",
            "required": True,
        }
    
    # If user was rejected, return rejected status
    if latest_onboarding and latest_onboarding.status == "REJECTED":
        return {
            "status": "rejected",
            "required": True,
        }
    
    # Check if onboarding is required (>= threshold sessions)
    if session_count >= threshold:
        return {
            "status": "required",
            "required": True,
        }
    
    # Not required yet
    return {
        "status": "not_required",
        "required": False,
    }

