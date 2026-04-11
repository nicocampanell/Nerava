"""
Pydantic schemas for user preferences.
"""
from typing import Optional

from pydantic import BaseModel


class PreferencesIn(BaseModel):
    """Input schema for user preferences."""
    notifications_enabled: Optional[bool] = None
    email_frequency: Optional[str] = None
    theme: Optional[str] = None

class PreferencesOut(BaseModel):
    """Output schema for user preferences."""
    id: int
    user_id: int
    notifications_enabled: bool
    email_frequency: str
    theme: str
