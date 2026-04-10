from __future__ import annotations

from datetime import datetime, time
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---- Merchant & Offer
class Offer(BaseModel):
    offer_id: str
    type: Literal["percent", "fixed"]
    value: int
    window_start: Optional[time] = None
    window_end: Optional[time] = None
    source: Literal["affiliate", "local", "square"] = "affiliate"
    tracking_template: Optional[str] = None


class MerchantUpsert(BaseModel):
    name: str
    category: str
    lat: float
    lng: float
    radius_m: int = 500
    pos_provider: Optional[str] = None
    pos_account_id: Optional[str] = None
    pos_location_ids: Optional[List[str]] = None
    affiliate_partner: Optional[str] = None
    affiliate_merchant_ref: Optional[str] = None
    green_hour_commit_pct: Optional[float] = None
    status: Literal["draft", "active", "paused"] = "active"
    offers: Optional[List[Offer]] = None


# ---- Event
class EventCreate(BaseModel):
    host_type: Literal["merchant", "activator"]
    host_id: int
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    city: str
    lat: float
    lng: float
    radius_m: int = 120
    starts_at: datetime
    ends_at: datetime
    green_window_start: Optional[time] = None
    green_window_end: Optional[time] = None
    join_fee_cents: int = 0
    pool_commit_pct: Optional[float] = None
    capacity: Optional[int] = None
    verification_mode: Literal["geo", "qr", "photo"] = "geo"
    min_dwell_sec: int = 0


class EventNearby(BaseModel):
    id: int
    title: str
    distance_m: float
    starts_at: datetime
    ends_at: datetime
    price_cents: int
    green_window: Optional[Dict[str, str]] = None
    capacity_left: Optional[int] = None


# ---- Discover
class DiscoverReq(BaseModel):
    user_id: Optional[int] = None
    lat: float
    lng: float
    radius_m: int = 2000
    categories: Optional[List[str]] = None
    time: Optional[datetime] = None


class DiscoverItem(BaseModel):
    kind: Literal["event", "merchant"]
    id: int
    title: str
    subtitle: Optional[str] = None
    distance_m: float
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    offer: Optional[Dict] = None
    ctas: List[Dict] = Field(default_factory=list)


class DiscoverRes(BaseModel):
    items: List[DiscoverItem] = Field(default_factory=list)


# ---- Affiliate
class TrackClickReq(BaseModel):
    user_id: int
    merchant_id: int
    offer_id: str


class AffiliateNotifyReq(BaseModel):
    network: str
    click_id: str
    amount_cents: int
    merchant_ref: Optional[str] = None
    meta: Optional[Dict] = None


# ---- Insights
class InsightsReq(BaseModel):
    merchant_id: Optional[int] = None
    event_id: Optional[int] = None
    from_ts: Optional[datetime] = None
    to_ts: Optional[datetime] = None


