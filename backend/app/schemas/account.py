"""Account schemas for profile update and stats."""
from typing import Optional

from pydantic import BaseModel


class ProfileUpdate(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None


class ProfileResponse(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    phone: Optional[str] = None
    vehicle_model: Optional[str] = None
    member_since: Optional[str] = None


class FavoriteChargerInfo(BaseModel):
    name: str
    sessions: int


class AccountStats(BaseModel):
    total_sessions: int = 0
    total_kwh: float = 0.0
    total_earned_cents: int = 0
    total_nova: int = 0
    favorite_charger: Optional[FavoriteChargerInfo] = None
    member_since: Optional[str] = None
    current_streak: int = 0
    co2_avoided_kg: float = 0.0
