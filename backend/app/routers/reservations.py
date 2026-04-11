# app/routers/reservations.py
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["reservations"])  # no internal prefix

class SoftReserveIn(BaseModel):
    hub_id: str
    user_id: str
    minutes: Optional[int] = Field(default=30, ge=15, le=120)

class SoftReserveOut(BaseModel):
    id: str
    hub_id: str
    user_id: str
    type: str = "soft"
    status: str = "held"
    window_start_iso: str
    window_end_iso: str
    human: str

@router.post("/soft", response_model=SoftReserveOut)
async def soft_reserve(payload: SoftReserveIn):
    now = datetime.now(timezone.utc)
    dur = timedelta(minutes=payload.minutes or 30)
    start = now + timedelta(minutes=10)  # 10-min lead time
    end = start + dur
    human = f"Held {start.astimezone().strftime('%-I:%M %p')}–{end.astimezone().strftime('%-I:%M %p')}"
    return SoftReserveOut(
        id=f"r_{abs(hash((payload.hub_id, payload.user_id, now.timestamp()))):08x}",
        hub_id=payload.hub_id,
        user_id=payload.user_id,
        window_start_iso=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end_iso=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        human=human,
    )
