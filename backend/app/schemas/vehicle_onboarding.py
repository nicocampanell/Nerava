"""
Schemas for Vehicle Onboarding API
"""
from typing import List, Optional

from pydantic import BaseModel


class VehicleOnboardingStartRequest(BaseModel):
    """Request to start vehicle onboarding"""
    intent_session_id: Optional[str] = None
    charger_id: Optional[str] = None


class VehicleOnboardingStartResponse(BaseModel):
    """Response for starting vehicle onboarding"""
    onboarding_id: str
    upload_urls: List[str]  # S3 signed URLs for photo uploads
    expires_at: str  # ISO string


class VehicleOnboardingCompleteRequest(BaseModel):
    """Request to complete vehicle onboarding"""
    onboarding_id: str
    photo_urls: List[str]  # S3 URLs of uploaded photos
    license_plate: Optional[str] = None


class VehicleOnboardingCompleteResponse(BaseModel):
    """Response for completing vehicle onboarding"""
    onboarding_id: str
    status: str  # SUBMITTED, APPROVED, REJECTED, PENDING_REVIEW
    message: str


class VehicleOnboardingStatusResponse(BaseModel):
    """Response for vehicle onboarding status"""
    status: str  # "not_required", "required", "submitted", "approved", "rejected"
    required: bool

