from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.security.tokens import decode_verify_token
from app.services.verify_dwell import ping as svc_ping
from app.services.verify_dwell import start_session as svc_start
from app.utils.log import get_logger

router = APIRouter(tags=["sessions-verify"])
logger = get_logger(__name__)


@router.get("/verify/s/{token}", response_class=HTMLResponse)
def verify_page(request: Request, token: str):
    try:
        claims = decode_verify_token(token)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_token")
    from pathlib import Path
    tpl_path = Path(__file__).parent.parent / "templates" / "verify.html"
    html = tpl_path.read_text(encoding="utf-8")
    html = html.replace("{{ token }}", token).replace("{{ public_base }}", settings.public_base_url)
    return HTMLResponse(content=html)


class StartBody(BaseModel):
    token: str
    lat: float
    lng: float
    accuracy_m: float
    ua: str
    event_id: Optional[int] = None


@router.post("/v1/sessions/verify/start")
def start(body: StartBody):
    try:
        claims = decode_verify_token(body.token)
    except Exception:
        # Return ok:false instead of 500
        logger.info({"at":"verify","step":"start","ok":False,"reason":"bad_token"})
        return {"ok": False, "reason": "bad_token", "hint": "Create a new verification link.", "status":"start_failed"}
    session_id = claims.get("session_id")
    user_id = int(claims.get("user_id"))
    db: Session = SessionLocal()
    try:
        res = svc_start(db, session_id=session_id, user_id=user_id, lat=body.lat, lng=body.lng, accuracy_m=body.accuracy_m, ua=body.ua, event_id=body.event_id)
        logger.info({"at":"verify","step":"start","ok":bool(res.get("ok", False)),"sid":session_id,"reason":res.get("reason")})
        if not res.get("ok") and res.get("reason") == "no_target":
            return res
        return res
    finally:
        db.close()


class PingBody(BaseModel):
    session_id: str
    lat: float
    lng: float
    accuracy_m: float
    ts: Optional[str] = None


@router.post("/v1/sessions/verify/ping")
def ping(body: PingBody):
    db: Session = SessionLocal()
    try:
        res = svc_ping(db, session_id=body.session_id, lat=body.lat, lng=body.lng, accuracy_m=body.accuracy_m)
        logger.info({"at":"verify","step":"ping","ok":bool(res.get("ok", False)),"sid":body.session_id,"verified":res.get("verified"),"accrued":res.get("dwell_seconds")})
        return res
    finally:
        db.close()


