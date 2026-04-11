from datetime import time
from typing import List, Optional

from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class UserPublic(BaseModel):
    id: int
    email: EmailStr
    class Config:
        from_attributes = True

class PreferencesIn(BaseModel):
    food_tags: List[str] = []
    max_detour_minutes: int = 10
    preferred_networks: List[str] = []
    typical_start: time = time(18,0)
    typical_end: time = time(22,0)
    home_zip: Optional[str] = None

class PreferencesOut(PreferencesIn):
    user_id: int
    class Config:
        from_attributes = True
