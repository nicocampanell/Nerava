"""Events API router."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import events as events_service
from app.services import verifier as verifier_service
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/events", tags=["events"])
logger = get_logger("events_api")


def resolve_user_id(request: Request, body_user_id: Optional[int]) -> int:
    """Resolve user_id from JSON body or legacy X-User-Id header."""
    if body_user_id is not None:
        return int(body_user_id)
    header_val = request.headers.get("X-User-Id")
    if header_val and header_val.isdigit():
        return int(header_val)
    raise HTTPException(status_code=401, detail="user_id required")


class CreateEventRequest(BaseModel):
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    city: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    starts_at: str  # ISO format
    ends_at: str
    green_window_start: Optional[str] = None
    green_window_end: Optional[str] = None
    price_cents: int = 0
    revenue_split: Optional[dict] = None
    capacity: Optional[int] = None
    visibility: str = "public"
    status: str = "scheduled"


class EventCreateReq(BaseModel):
    user_id: int
    event: CreateEventRequest


class EventJoinReq(BaseModel):
    user_id: int


class StartVerificationRequest(BaseModel):
    user_id: int
    mode: str = "geo"
    charger_id: Optional[str] = None


class CompleteVerificationRequest(BaseModel):
    lat: float
    lng: float


class EndEventReq(BaseModel):
    user_id: int


@router.post("")
def create_event(req: EventCreateReq, request_obj: Request, db: Session = Depends(get_db)):
    """Create a new event."""
    user_id = resolve_user_id(request_obj, req.user_id)
    payload = req.event.dict()

    # Parse timestamps
    payload["starts_at"] = datetime.fromisoformat(req.event.starts_at.replace("Z", "+00:00"))
    payload["ends_at"] = datetime.fromisoformat(req.event.ends_at.replace("Z", "+00:00"))

    event = events_service.create_event(db, user_id, payload)
    return event


@router.get("/nearby")
def list_events_nearby(
    lat: float = Query(...),
    lng: float = Query(...),
    radius_m: int = Query(2000, ge=0, le=50000),
    now: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List events near a location."""
    now_utc = None
    if now:
        now_utc = datetime.fromisoformat(now.replace("Z", "+00:00"))

    events = events_service.list_events_nearby(db, lat, lng, radius_m, now_utc)
    return events


@router.post("/{event_id}/join")
def join_event(
    event_id: int, body: EventJoinReq, request_obj: Request, db: Session = Depends(get_db)
):
    """Join an event."""
    user_id = resolve_user_id(request_obj, getattr(body, "user_id", None))
    attendance = events_service.join_event(db, event_id, user_id)
    return attendance


@router.post("/{event_id}/verify/start")
def start_verification(
    event_id: int,
    request: StartVerificationRequest,
    request_obj: Request,
    db: Session = Depends(get_db),
):
    """Start verification for an event."""
    user_id = resolve_user_id(request_obj, request.user_id)
    verification = verifier_service.start_verification(
        db, user_id, event_id, request.mode, request.charger_id
    )
    return verification


@router.post("/verify/{verification_id}/complete")
def complete_verification(
    verification_id: int, request: CompleteVerificationRequest, db: Session = Depends(get_db)
):
    """Complete verification."""
    result = verifier_service.complete_verification(db, verification_id, request.lat, request.lng)
    return result


@router.post("/{event_id}/end")
def end_event(
    event_id: int, body: EndEventReq, request_obj: Request, db: Session = Depends(get_db)
):
    """End an event."""
    _uid = resolve_user_id(request_obj, getattr(body, "user_id", None))
    event = events_service.end_event(db, event_id)
    return event
