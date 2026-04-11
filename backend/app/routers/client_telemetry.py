import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models_client_events import ClientEvent

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


class ClientEventRequest(BaseModel):
    event: str
    ts: int  # Unix timestamp in milliseconds
    page: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


@router.post("/events")
async def create_client_event(
    event_request: ClientEventRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Capture client-side events (tab switches, API errors, EV connect clicks, etc.)"""
    request_id = getattr(request.state, "request_id", None)
    
    # Get user_id from request state (set by AuthMiddleware) - optional for anonymous events
    user_id = getattr(request.state, "user_id", None)
    
    event = ClientEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        event=event_request.event,
        ts=datetime.fromtimestamp(event_request.ts / 1000),
        page=event_request.page,
        meta=json.dumps(event_request.meta) if event_request.meta else None,
        request_id=request_id,
    )
    
    db.add(event)
    db.commit()
    
    return {"ok": True, "event_id": event.id}

