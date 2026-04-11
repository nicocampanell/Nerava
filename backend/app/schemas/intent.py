"""
Schemas for Intent Capture API
"""
from typing import List, Optional

from pydantic import BaseModel


class CaptureIntentRequest(BaseModel):
    """Request schema for capturing intent"""
    lat: float
    lng: float
    accuracy_m: Optional[float] = None
    client_ts: Optional[str] = None  # ISO string


class ChargerSummary(BaseModel):
    """Summary of charger information"""
    id: str
    name: str
    distance_m: float
    network_name: Optional[str] = None
    campaign_reward_cents: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    num_evse: Optional[int] = None
    power_kw: Optional[float] = None
    pricing_per_kwh: Optional[float] = None
    has_merchant_perk: bool = False
    merchant_perk_title: Optional[str] = None

    class Config:
        from_attributes = True


class MerchantSummary(BaseModel):
    """Summary of merchant information"""
    place_id: str
    name: str
    lat: float
    lng: float
    distance_m: float
    types: List[str]
    photo_url: Optional[str] = None
    icon_url: Optional[str] = None
    badges: Optional[List[str]] = None  # e.g., ["Boosted"], ["Perks available"]
    daily_cap_cents: Optional[int] = None  # Internal use only (not shown as "ad")
    
    class Config:
        from_attributes = True


class NextActions(BaseModel):
    """Next actions for the user"""
    request_wallet_pass: bool = False
    require_vehicle_onboarding: bool = False


class CaptureIntentResponse(BaseModel):
    """Response schema for capturing intent"""
    session_id: Optional[str] = None  # None for anonymous users
    confidence_tier: str  # "A", "B", "C"
    charger_summary: Optional[ChargerSummary] = None  # Nearest charger (backward compat)
    chargers: List[ChargerSummary] = []  # Up to 5 nearest chargers within 25km
    merchants: List[MerchantSummary] = []
    fallback_message: Optional[str] = None
    next_actions: NextActions

