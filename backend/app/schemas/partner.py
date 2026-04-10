"""
Pydantic schemas for the Partner Incentive API.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

# --- Session Ingest ---

class PartnerSessionIngestRequest(BaseModel):
    partner_session_id: str = Field(..., description="Partner's unique session identifier")
    partner_driver_id: str = Field(..., description="Partner's driver identifier")
    status: str = Field(..., description="Session status: candidate, charging, or completed")
    session_start: datetime
    session_end: Optional[datetime] = None
    charger_id: Optional[str] = None
    charger_network: Optional[str] = None
    connector_type: Optional[str] = None
    power_kw: Optional[float] = None
    kwh_delivered: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    vehicle_vin: Optional[str] = None
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_year: Optional[int] = None
    battery_start_pct: Optional[int] = None
    battery_end_pct: Optional[int] = None
    signal_confidence: Optional[float] = Field(None, ge=0, le=1, description="Partner's confidence that charging is happening (0-1)")
    charging_state_hint: Optional[str] = Field(None, description="Partner's hint about the charging state")


class PartnerSessionUpdateRequest(BaseModel):
    status: Optional[str] = None
    session_end: Optional[datetime] = None
    kwh_delivered: Optional[float] = None
    power_kw: Optional[float] = None
    battery_end_pct: Optional[int] = None


class GrantResponse(BaseModel):
    grant_id: str
    campaign_id: str
    campaign_name: Optional[str] = None
    amount_cents: int
    platform_fee_cents: Optional[int] = None
    net_reward_cents: Optional[int] = None
    reward_destination: str


class PartnerSessionResponse(BaseModel):
    session_event_id: str
    partner_session_id: Optional[str] = None
    status: str
    verified: bool
    quality_score: Optional[int] = None
    session_start: Optional[str] = None
    session_end: Optional[str] = None
    duration_minutes: Optional[int] = None
    kwh_delivered: Optional[float] = None
    charger_id: Optional[str] = None
    grant: Optional[GrantResponse] = None


class PartnerGrantListItem(BaseModel):
    grant_id: str
    session_event_id: str
    campaign_id: str
    campaign_name: Optional[str] = None
    amount_cents: int
    reward_destination: str
    status: str
    granted_at: Optional[str] = None


# --- Campaign Discovery ---

class PartnerCampaignAvailable(BaseModel):
    campaign_id: str
    name: str
    sponsor_name: str
    cost_per_session_cents: int
    rule_min_duration_minutes: int
    charger_networks: Optional[List[str]] = None
    connector_types: Optional[List[str]] = None
    start_date: str
    end_date: Optional[str] = None
    allow_partner_sessions: bool
    rule_min_trust_tier: Optional[int] = None


# --- Admin: Partner Management ---

class PartnerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    partner_type: str = Field(..., description="charging_network, driver_app, fleet_platform, oem_app, hardware_mfr, utility")
    trust_tier: int = Field(default=3, ge=1, le=3)
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    webhook_url: Optional[str] = None
    rate_limit_rpm: int = Field(default=60, ge=1, le=10000)


class PartnerUpdateRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    trust_tier: Optional[int] = Field(default=None, ge=1, le=3)
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_enabled: Optional[bool] = None
    rate_limit_rpm: Optional[int] = Field(default=None, ge=1, le=10000)
    quality_score_modifier: Optional[int] = None


class PartnerResponse(BaseModel):
    id: str
    name: str
    slug: str
    partner_type: str
    trust_tier: int
    status: str
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_enabled: bool
    rate_limit_rpm: int
    quality_score_modifier: int
    created_at: str
    updated_at: str


class PartnerAPIKeyCreateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    scopes: List[str] = Field(
        default=["sessions:write", "sessions:read", "grants:read", "campaigns:read"]
    )


class PartnerAPIKeyCreateResponse(BaseModel):
    id: str
    key_prefix: str
    plaintext_key: str  # shown once only
    name: Optional[str] = None
    scopes: List[str]
    created_at: str


class PartnerAPIKeyResponse(BaseModel):
    id: str
    key_prefix: str
    name: Optional[str] = None
    scopes: List[str]
    is_active: bool
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: str


class PartnerProfileResponse(BaseModel):
    id: str
    name: str
    slug: str
    partner_type: str
    trust_tier: int
    status: str
    rate_limit_rpm: int
    total_sessions: int
    total_grants: int
