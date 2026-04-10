from typing import Dict

from fastapi import APIRouter, Query

from app.services.db_user import get_prefs_dict
from app.services.routing import recommend_hub
from app.services.seed_hubs import list_hubs

router = APIRouter()

@router.get("/recommend", response_model=Dict)
def rec(lat: float = Query(...), lng: float = Query(...), user_id: str = Query("anon@nerava.app")):
    prefs = get_prefs_dict(user_id)
    hubs = list_hubs()
    best, meta = recommend_hub(lat, lng, prefs, hubs)
    out = dict(best)
    out.update(meta)
    return out
