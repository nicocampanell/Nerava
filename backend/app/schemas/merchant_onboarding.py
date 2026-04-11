"""
Schemas for Merchant Onboarding API
"""
from typing import List, Optional

from pydantic import BaseModel


class GoogleAuthStartRequest(BaseModel):
    """Request to start Google Business Profile OAuth"""
    pass


class GoogleAuthStartResponse(BaseModel):
    """Response for starting Google OAuth"""
    auth_url: str
    state: str  # OAuth state for CSRF protection


class GoogleAuthCallbackRequest(BaseModel):
    """Request for Google OAuth callback"""
    code: str
    state: str


class GoogleAuthCallbackResponse(BaseModel):
    """Response for Google OAuth callback"""
    success: bool
    merchant_account_id: Optional[str] = None


class LocationSummary(BaseModel):
    """Summary of a Google Business Profile location"""
    location_id: str
    name: str
    address: str
    place_id: Optional[str] = None  # Google Places place_id if available


class LocationsListResponse(BaseModel):
    """Response listing available locations"""
    locations: List[LocationSummary]


class ClaimLocationRequest(BaseModel):
    """Request to claim a location"""
    place_id: str
    name: Optional[str] = None
    address: Optional[str] = None


class ClaimLocationResponse(BaseModel):
    """Response for claiming a location"""
    claim_id: str
    place_id: str
    status: str
    merchant_id: Optional[str] = None


class SetupIntentRequest(BaseModel):
    """Request to create Stripe SetupIntent"""
    pass


class SetupIntentResponse(BaseModel):
    """Response for SetupIntent creation"""
    client_secret: str
    setup_intent_id: str


class UpdatePlacementRequest(BaseModel):
    """Request to update placement rules"""
    place_id: str
    daily_cap_cents: Optional[int] = None
    boost_weight: Optional[float] = None
    perks_enabled: Optional[bool] = None


class UpdatePlacementResponse(BaseModel):
    """Response for updating placement rules"""
    rule_id: str
    place_id: str
    status: str
    daily_cap_cents: int
    boost_weight: float
    perks_enabled: bool



