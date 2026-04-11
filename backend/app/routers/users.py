
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.db_user import DEFAULT_PREFS, get_user_by_id, upsert_user_prefs

router = APIRouter(prefix="/v1/users", tags=["users"])

class UserPrefs(BaseModel):
    pref_coffee: bool = False
    pref_food: bool = False
    pref_dog: bool = False
    pref_kid: bool = False
    pref_shopping: bool = False
    pref_exercise: bool = False

@router.get("/{user_id}/prefs", response_model=UserPrefs)
def get_prefs(user_id: str):
    user = get_user_by_id(user_id)
    if not user:
        return UserPrefs(**DEFAULT_PREFS)
    return UserPrefs(**{k: bool(user.get(k, False)) for k in DEFAULT_PREFS.keys()})

@router.post("/{user_id}/prefs", response_model=UserPrefs)
def set_prefs(user_id: str, prefs: UserPrefs):
    ok = upsert_user_prefs(user_id, prefs.dict())
    if not ok:
        raise HTTPException(status_code=500, detail="failed_to_save_prefs")
    return prefs

@router.get("/me/profile")
def get_my_profile(user_id: str = "demo-user-123"):
    """Get current user profile"""
    return {
        "id": user_id,
        "email": f"{user_id}@nerava.app",
        "name": "Demo User",
        "tier": "Silver",
        "followers": 12,
        "following": 8
    }
