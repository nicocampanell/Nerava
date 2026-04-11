from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.services.dual_zone import start_session, update_positions_and_verify

router = APIRouter(prefix="/v1/dual", tags=["dual_zone"])

def require_flag():
    if not getattr(settings, "feature_dual_radius_verification", False):
        raise HTTPException(status_code=404, detail="Feature not enabled")

class StartBody(BaseModel):
    user_id: str
    charger_id: str
    merchant_id: str
    charger_radius_m: int = 40
    merchant_radius_m: int = 100
    dwell_threshold_s: int = 300

class Pos(BaseModel):
    lat: float
    lng: float

class TickBody(BaseModel):
    session_id: int = Field(..., alias="session_id")
    user_pos: Pos
    charger_pos: Pos
    merchant_pos: Pos

@router.post("/start")
def start(b: StartBody, db: Session = Depends(get_db), _=Depends(require_flag)):
    s = start_session(db, **b.dict())
    return {"session_id": s.id, "status": s.status}

@router.post("/tick")
def tick(b: TickBody, db: Session = Depends(get_db), _=Depends(require_flag)):
    s = update_positions_and_verify(
        db, b.session_id,
        {"lat": b.user_pos.lat, "lng": b.user_pos.lng},
        {"lat": b.charger_pos.lat, "lng": b.charger_pos.lng},
        {"lat": b.merchant_pos.lat, "lng": b.merchant_pos.lng},
    )
    if not s: raise HTTPException(404, "Session not found")
    return {
        "status": s.status, "dwell_seconds": s.dwell_seconds,
        "verified_at": s.verified_at, "charger_entered_at": s.charger_entered_at,
        "merchant_entered_at": s.merchant_entered_at
    }
