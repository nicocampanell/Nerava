from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.domain import get_current_user_id
from ..models import UserPreferences
from ..schemas import PreferencesIn, PreferencesOut

router = APIRouter(prefix="/users/me/preferences", tags=["users"])

@router.get("", response_model=PreferencesOut)
def get_my_preferences(user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()
    if not prefs:
        raise HTTPException(status_code=404, detail="Preferences not found")
    return prefs

@router.put("", response_model=PreferencesOut)
def update_my_preferences(payload: PreferencesIn, user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user_id).first()
    if not prefs:
        raise HTTPException(status_code=404, detail="Preferences not found")
    prefs.food_tags = payload.food_tags
    prefs.max_detour_minutes = payload.max_detour_minutes
    prefs.preferred_networks = payload.preferred_networks
    prefs.typical_start = payload.typical_start
    prefs.typical_end = payload.typical_end
    prefs.home_zip = payload.home_zip
    db.commit()
    db.refresh(prefs)
    return prefs
