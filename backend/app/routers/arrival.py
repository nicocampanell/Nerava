"""
EV Arrival Router — /v1/arrival/*

Core endpoints for the EV Arrival coordination system.
Handles session creation, order binding, geofence confirmation,
merchant confirmation, driver feedback, and active session lookup.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.models import User
from app.models.arrival_session import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ArrivalSession,
)
from app.models.billing_event import BillingEvent
from app.models.merchant_notification_config import MerchantNotificationConfig
from app.models.merchant_pos_credentials import MerchantPOSCredentials
from app.models.queued_order import QueuedOrder, QueuedOrderStatus
from app.models.while_you_charge import Charger, Merchant
from app.services.analytics import get_analytics_client
from app.services.geo import haversine_m
from app.services.notification_service import notify_merchant
from app.services.pos_adapter import get_pos_adapter
from app.utils.ev_browser import detect_ev_browser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/arrival", tags=["arrival"])

SESSION_TTL_HOURS = 2
CHARGER_RADIUS_M = 250  # Max distance from charger for geofence confirmation


# ─── Request/Response Models ────────────────────────────────────────

class CreateArrivalRequest(BaseModel):
    merchant_id: str
    charger_id: Optional[str] = None
    arrival_type: str = Field(..., pattern="^(ev_curbside|ev_dine_in)$")
    fulfillment_type: Optional[str] = Field(None, pattern="^(ev_curbside|ev_dine_in)$")
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    current_lat: Optional[float] = None
    current_lng: Optional[float] = None
    lat: float
    lng: float
    accuracy_m: Optional[float] = None
    idempotency_key: Optional[str] = None
    virtual_key_id: Optional[str] = None  # Virtual Key ID for automatic arrival tracking


class VehicleInfo(BaseModel):
    color: Optional[str] = None
    model: Optional[str] = None


class CreateArrivalResponse(BaseModel):
    session_id: str
    status: str
    merchant_name: str
    arrival_type: str
    ordering_url: Optional[str] = None
    ordering_instructions: Optional[str] = None
    expires_at: str
    vehicle: VehicleInfo
    vehicle_required: bool = False


class BindOrderRequest(BaseModel):
    order_number: str = Field(..., min_length=1, max_length=100)
    estimated_total_cents: Optional[int] = None


class BindOrderResponse(BaseModel):
    session_id: str
    status: str
    order_number: str
    order_source: str
    order_total_cents: Optional[int] = None
    order_status: str


class ConfirmArrivalRequest(BaseModel):
    charger_id: str  # Required — anti-spoofing
    lat: float
    lng: float
    accuracy_m: Optional[float] = None


class ConfirmArrivalResponse(BaseModel):
    status: str
    merchant_notified: bool
    notification_method: str


class MerchantConfirmRequest(BaseModel):
    confirmed: bool = True
    merchant_reported_total_cents: Optional[int] = None


class MerchantConfirmResponse(BaseModel):
    status: str
    billable_amount_cents: Optional[int] = None


class FeedbackRequest(BaseModel):
    rating: str = Field(..., pattern="^(up|down)$")
    reason: Optional[str] = None
    comment: Optional[str] = Field(None, max_length=200)


class SessionResponse(BaseModel):
    session_id: str
    status: str
    merchant_name: str
    merchant_id: str
    arrival_type: str
    order_number: Optional[str] = None
    order_source: Optional[str] = None
    order_total_cents: Optional[int] = None
    expires_at: str
    vehicle: VehicleInfo
    created_at: str
    merchant_notified_at: Optional[str] = None


class ActiveSessionResponse(BaseModel):
    session: Optional[SessionResponse] = None


class QueueOrderRequest(BaseModel):
    order_number: Optional[str] = Field(None, max_length=100)
    payload: Optional[dict] = None


class QueuedOrderResponse(BaseModel):
    id: str
    status: str
    ordering_url: str
    release_url: Optional[str] = None
    order_number: Optional[str] = None
    created_at: str
    released_at: Optional[str] = None


class TriggerArrivalRequest(BaseModel):
    lat: float
    lng: float
    accuracy_m: Optional[float] = None


class TriggerArrivalResponse(BaseModel):
    status: str
    order_released: bool
    estimated_ready_minutes: Optional[int] = None


# ─── Helpers ────────────────────────────────────────────────────────

def _get_active_session(db: Session, driver_id: int) -> Optional[ArrivalSession]:
    """Find the driver's current active arrival session (if any)."""
    return (
        db.query(ArrivalSession)
        .filter(
            ArrivalSession.driver_id == driver_id,
            ArrivalSession.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )


def _session_to_response(session: ArrivalSession, merchant: Merchant) -> SessionResponse:
    return SessionResponse(
        session_id=str(session.id),
        status=session.status,
        merchant_name=merchant.name,
        merchant_id=merchant.id,
        arrival_type=session.arrival_type,
        order_number=session.order_number,
        order_source=session.order_source,
        order_total_cents=session.order_total_cents,
        expires_at=session.expires_at.isoformat() + "Z" if session.expires_at else "",
        vehicle=VehicleInfo(color=session.vehicle_color, model=session.vehicle_model),
        created_at=session.created_at.isoformat() + "Z" if session.created_at else "",
        merchant_notified_at=(
            session.merchant_notified_at.isoformat() + "Z" if session.merchant_notified_at else None
        ),
    )


def _capture_event(event: str, user_id: int, properties: dict):
    """Fire-and-forget PostHog event."""
    try:
        analytics = get_analytics_client()
        if analytics:
            analytics.capture(
                distinct_id=str(user_id),
                event=event,
                properties=properties,
            )
    except Exception as e:
        logger.warning(f"Analytics capture failed for {event}: {e}")


def _build_release_url(ordering_url: str, session: ArrivalSession) -> str:
    """Build the release URL with tracking parameters."""
    separator = "&" if "?" in ordering_url else "?"
    return (
        f"{ordering_url}{separator}"
        f"nerava_session={session.id}&"
        f"nerava_released=true"
    )


def _release_queued_order_if_any(
    db: Session,
    session: ArrivalSession,
    driver_id: int,
) -> Optional[QueuedOrder]:
    """
    Release the queued order for this session if one exists.
    Returns the released QueuedOrder or None if none exists.
    """
    queued_order = (
        db.query(QueuedOrder)
        .filter(
            QueuedOrder.arrival_session_id == session.id,
            QueuedOrder.status == QueuedOrderStatus.QUEUED.value,
        )
        .first()
    )

    if not queued_order:
        return None

    release_url = _build_release_url(queued_order.ordering_url, session)
    queued_order.release(release_url)

    _capture_event("ev_arrival.queued_order_released", driver_id, {
        "session_id": str(session.id),
        "merchant_id": session.merchant_id,
        "queued_order_id": str(queued_order.id),
        "order_number": queued_order.order_number,
    })

    logger.info(f"Released queued order {queued_order.id} for session {session.id}")
    return queued_order


def _cancel_queued_order_if_any(db: Session, session: ArrivalSession) -> Optional[QueuedOrder]:
    """Cancel the queued order for this session if one exists."""
    queued_order = (
        db.query(QueuedOrder)
        .filter(
            QueuedOrder.arrival_session_id == session.id,
            QueuedOrder.status == QueuedOrderStatus.QUEUED.value,
        )
        .first()
    )

    if queued_order:
        queued_order.cancel()
        logger.info(f"Canceled queued order {queued_order.id} for session {session.id}")

    return queued_order


# ─── Endpoints ──────────────────────────────────────────────────────

@router.post("/create", status_code=201, response_model=CreateArrivalResponse)
async def create_arrival(
    req: CreateArrivalRequest,
    request: Request,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Create a new EV Arrival session."""
    # Idempotency check
    if req.idempotency_key:
        existing = (
            db.query(ArrivalSession)
            .filter(ArrivalSession.idempotency_key == req.idempotency_key)
            .first()
        )
        if existing:
            merchant = db.query(Merchant).filter(Merchant.id == req.merchant_id).first()
            return CreateArrivalResponse(
                session_id=str(existing.id),
                status=existing.status,
                merchant_name=merchant.name if merchant else "",
                arrival_type=existing.arrival_type,
                expires_at=existing.expires_at.isoformat() + "Z",
                vehicle=VehicleInfo(color=existing.vehicle_color, model=existing.vehicle_model),
            )

    # Check for existing active session
    active = _get_active_session(db, driver.id)
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ACTIVE_SESSION_EXISTS",
                "message": "You already have an active EV Arrival session",
                "session_id": str(active.id),
            },
        )

    # Validate merchant exists
    merchant = db.query(Merchant).filter(Merchant.id == req.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    # Validate charger if provided
    charger = None
    if req.charger_id:
        charger = db.query(Charger).filter(Charger.id == req.charger_id).first()
        if not charger:
            raise HTTPException(status_code=404, detail="Charger not found")

    # Get vehicle info from user
    vehicle_color = getattr(driver, "vehicle_color", None)
    vehicle_model = getattr(driver, "vehicle_model", None)
    vehicle_required = not (vehicle_color and vehicle_model)

    # Detect EV browser
    user_agent = request.headers.get("User-Agent", "")
    ev_info = detect_ev_browser(user_agent)
    
    browser_source = None
    if ev_info.is_ev_browser:
        if ev_info.brand == "Tesla":
            browser_source = "tesla_browser"
        else:
            browser_source = "ev_browser"
    else:
        browser_source = "web"

    now = datetime.utcnow()
    fulfillment_type = req.fulfillment_type or req.arrival_type
    
    # Determine arrival source based on virtual_key_id
    arrival_source = None
    if req.virtual_key_id:
        arrival_source = "virtual_key"
    elif ev_info.is_ev_browser:
        arrival_source = "geofence"  # Will use geofence polling
    else:
        arrival_source = "manual"  # Manual check-in
    
    session = ArrivalSession(
        id=uuid.uuid4(),
        driver_id=driver.id,
        merchant_id=req.merchant_id,
        charger_id=req.charger_id,
        arrival_type=req.arrival_type,
        fulfillment_type=fulfillment_type,
        browser_source=browser_source,
        ev_brand=ev_info.brand,
        ev_firmware=ev_info.firmware_version,
        virtual_key_id=req.virtual_key_id,
        arrival_source=arrival_source,
        destination_merchant_id=req.merchant_id,
        destination_lat=req.destination_lat,
        destination_lng=req.destination_lng,
        queued_order_status="queued" if fulfillment_type else None,
        vehicle_color=vehicle_color,
        vehicle_model=vehicle_model,
        status="pending_order",
        created_at=now,
        expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
        idempotency_key=req.idempotency_key,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    _capture_event("ev_arrival.created", driver.id, {
        "session_id": str(session.id),
        "merchant_id": req.merchant_id,
        "merchant_name": merchant.name,
        "arrival_type": req.arrival_type,
        "charger_id": req.charger_id,
    })

    # Get ordering info from merchant (if available)
    ordering_url = getattr(merchant, "ordering_url", None)
    ordering_instructions = getattr(merchant, "ordering_instructions", None)

    return CreateArrivalResponse(
        session_id=str(session.id),
        status=session.status,
        merchant_name=merchant.name,
        arrival_type=session.arrival_type,
        ordering_url=ordering_url,
        ordering_instructions=ordering_instructions,
        expires_at=session.expires_at.isoformat() + "Z",
        vehicle=VehicleInfo(color=vehicle_color, model=vehicle_model),
        vehicle_required=vehicle_required,
    )


@router.put("/{session_id}/order", response_model=BindOrderResponse)
async def bind_order(
    session_id: str,
    req: BindOrderRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Bind an order number to the session."""
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("pending_order", "awaiting_arrival"):
        raise HTTPException(status_code=400, detail=f"Cannot bind order in status: {session.status}")

    # Check expiry
    if session.expires_at < datetime.utcnow():
        session.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Session has expired")

    # Try POS lookup
    notif_config = (
        db.query(MerchantNotificationConfig)
        .filter(MerchantNotificationConfig.merchant_id == session.merchant_id)
        .first()
    )
    pos_integration = notif_config.pos_integration if notif_config else "none"
    pos_creds = (
        db.query(MerchantPOSCredentials)
        .filter(MerchantPOSCredentials.merchant_id == session.merchant_id)
        .first()
    )

    adapter = get_pos_adapter(pos_integration, pos_creds)
    pos_order = await adapter.lookup_order(req.order_number)

    now = datetime.utcnow()
    session.order_number = req.order_number
    session.order_bound_at = now
    session.driver_estimate_cents = req.estimated_total_cents

    if pos_order and pos_order.total_cents > 0:
        session.order_source = pos_integration  # 'toast' or 'square'
        session.order_total_cents = pos_order.total_cents
        session.order_status = pos_order.status
        session.total_source = "pos"
    else:
        session.order_source = "manual"
        session.order_total_cents = req.estimated_total_cents
        session.order_status = "unknown"
        if req.estimated_total_cents:
            session.total_source = "driver_estimate"

    # Set queued_order_status to 'queued' if fulfillment_type is set (EV order flow)
    if session.fulfillment_type:
        session.queued_order_status = "queued"

    session.status = "awaiting_arrival"
    db.commit()

    _capture_event("ev_arrival.order_bound", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "order_number": req.order_number,
        "order_source": session.order_source,
        "order_total_cents": session.order_total_cents,
    })

    return BindOrderResponse(
        session_id=str(session.id),
        status=session.status,
        order_number=session.order_number,
        order_source=session.order_source,
        order_total_cents=session.order_total_cents,
        order_status=session.order_status,
    )


@router.post("/{session_id}/confirm-arrival", response_model=ConfirmArrivalResponse)
async def confirm_arrival(
    session_id: str,
    req: ConfirmArrivalRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Geofence-triggered arrival confirmation.
    Requires charger_id + server-side distance verification (anti-spoofing).
    """
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("awaiting_arrival", "pending_order"):
        raise HTTPException(status_code=400, detail=f"Cannot confirm arrival in status: {session.status}")

    if session.expires_at < datetime.utcnow():
        session.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Session has expired")

    # Server-side distance verification
    charger = db.query(Charger).filter(Charger.id == req.charger_id).first()
    if not charger:
        raise HTTPException(status_code=400, detail="Charger not found")

    distance_m = haversine_m(req.lat, req.lng, charger.lat, charger.lng)
    if distance_m > CHARGER_RADIUS_M:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "TOO_FAR_FROM_CHARGER",
                "message": f"You are {int(distance_m)}m from the charger (max {CHARGER_RADIUS_M}m)",
                "distance_m": round(distance_m, 1),
            },
        )

    now = datetime.utcnow()
    session.arrival_lat = req.lat
    session.arrival_lng = req.lng
    session.arrival_accuracy_m = req.accuracy_m
    session.geofence_entered_at = now
    session.charger_id = req.charger_id  # Bind charger if not set at creation
    session.status = "arrived"

    # Release any queued order for this session
    released_order = _release_queued_order_if_any(db, session, driver.id)

    # Send merchant notification
    notif_config = (
        db.query(MerchantNotificationConfig)
        .filter(MerchantNotificationConfig.merchant_id == session.merchant_id)
        .first()
    )

    notification_method = "none"
    if notif_config:
        merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
        charger_name = charger.name if charger else None

        notification_method = await notify_merchant(
            notify_sms=notif_config.notify_sms,
            notify_email=notif_config.notify_email,
            sms_phone=notif_config.sms_phone,
            email_address=notif_config.email_address,
            order_number=session.order_number or "N/A",
            arrival_type=session.arrival_type,
            vehicle_color=session.vehicle_color,
            vehicle_model=session.vehicle_model,
            charger_name=charger_name,
            merchant_name=merchant.name if merchant else "",
            merchant_reply_code=session.merchant_reply_code or "",
        )

        if notification_method != "none":
            session.status = "merchant_notified"
            session.merchant_notified_at = now

    db.commit()

    _capture_event("ev_arrival.geofence_entered", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "charger_id": req.charger_id,
        "distance_m": round(distance_m, 1),
    })

    if notification_method != "none":
        _capture_event("ev_arrival.merchant_notified", driver.id, {
            "session_id": session_id,
            "merchant_id": session.merchant_id,
            "notification_method": notification_method,
        })

    return ConfirmArrivalResponse(
        status=session.status,
        merchant_notified=notification_method != "none",
        notification_method=notification_method,
    )


@router.post("/{session_id}/merchant-confirm", response_model=MerchantConfirmResponse)
async def merchant_confirm(
    session_id: str,
    req: MerchantConfirmRequest,
    db: Session = Depends(get_db),
):
    """
    Merchant confirms order was delivered.
    Can be called from merchant dashboard or triggered by SMS reply.
    """
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("merchant_notified", "arrived"):
        raise HTTPException(status_code=400, detail=f"Cannot confirm in status: {session.status}")

    now = datetime.utcnow()
    session.merchant_confirmed_at = now

    # Update merchant-reported total if provided
    if req.merchant_reported_total_cents is not None:
        session.merchant_reported_total_cents = req.merchant_reported_total_cents

    # Determine billing total with precedence: POS > merchant_reported > driver_estimate
    billing_total = None
    total_source = None

    if session.total_source == "pos" and session.order_total_cents:
        billing_total = session.order_total_cents
        total_source = "pos"
    elif req.merchant_reported_total_cents is not None and req.merchant_reported_total_cents > 0:
        billing_total = req.merchant_reported_total_cents
        total_source = "merchant_reported"
        session.total_source = total_source
        session.order_total_cents = billing_total
    elif session.driver_estimate_cents and session.driver_estimate_cents > 0:
        billing_total = session.driver_estimate_cents
        total_source = "driver_estimate"
        session.total_source = total_source
        session.order_total_cents = billing_total

    billable_amount_cents = None

    if billing_total and billing_total > 0:
        # Create billing event
        billable_amount_cents = (billing_total * session.platform_fee_bps) // 10000
        session.billable_amount_cents = billable_amount_cents
        session.billing_status = "pending"
        session.status = "completed"
        session.completed_at = now

        billing_event = BillingEvent(
            arrival_session_id=session.id,
            merchant_id=session.merchant_id,
            order_total_cents=billing_total,
            fee_bps=session.platform_fee_bps,
            billable_cents=billable_amount_cents,
            total_source=total_source,
        )
        db.add(billing_event)
    else:
        # No total available — mark completed but unbillable
        session.status = "completed_unbillable"
        session.completed_at = now

    db.commit()

    _capture_event("ev_arrival.merchant_confirmed", session.driver_id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "billable_amount_cents": billable_amount_cents,
        "total_source": total_source,
    })

    _capture_event("ev_arrival.completed", session.driver_id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "status": session.status,
    })

    return MerchantConfirmResponse(
        status=session.status,
        billable_amount_cents=billable_amount_cents,
    )


@router.post("/{session_id}/feedback")
async def submit_feedback(
    session_id: str,
    req: FeedbackRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Driver post-visit feedback."""
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ("completed", "completed_unbillable"):
        raise HTTPException(status_code=400, detail="Can only leave feedback on completed sessions")

    session.feedback_rating = req.rating
    session.feedback_reason = req.reason
    session.feedback_comment = req.comment
    db.commit()

    _capture_event("ev_arrival.feedback_submitted", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "rating": req.rating,
        "reason": req.reason,
    })

    return {"ok": True}


@router.get("/active", response_model=ActiveSessionResponse)
async def get_active_session(
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get driver's current active arrival session (if any)."""
    session = _get_active_session(db, driver.id)
    if not session:
        return ActiveSessionResponse(session=None)

    # Check expiry
    if session.expires_at < datetime.utcnow():
        session.status = "expired"
        db.commit()
        _capture_event("ev_arrival.expired", driver.id, {
            "session_id": str(session.id),
            "merchant_id": session.merchant_id,
        })
        return ActiveSessionResponse(session=None)

    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
    return ActiveSessionResponse(session=_session_to_response(session, merchant))


@router.post("/{session_id}/cancel")
async def cancel_session(
    session_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Cancel an active arrival session."""
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="Session already ended")

    # Cancel any queued order for this session
    _cancel_queued_order_if_any(db, session)

    session.status = "canceled"
    session.completed_at = datetime.utcnow()
    db.commit()

    _capture_event("ev_arrival.canceled", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "previous_status": session.status,
    })

    return {"ok": True, "status": "canceled"}


# ─── Queued Order Endpoints ──────────────────────────────────────────


@router.post("/{session_id}/queue-order", status_code=201)
async def queue_order(
    session_id: str,
    req: QueueOrderRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Queue an order intent for this session.
    The order will be released when arrival is confirmed.
    Idempotent: returns existing queued order if one exists.
    """
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot queue order in status: {session.status}")

    if session.expires_at < datetime.utcnow():
        session.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Session has expired")

    # Check for existing queued order (idempotent)
    existing = (
        db.query(QueuedOrder)
        .filter(QueuedOrder.arrival_session_id == session.id)
        .first()
    )
    if existing:
        return QueuedOrderResponse(
            id=str(existing.id),
            status=existing.status,
            ordering_url=existing.ordering_url,
            release_url=existing.release_url,
            order_number=existing.order_number,
            created_at=existing.created_at.isoformat() + "Z",
            released_at=existing.released_at.isoformat() + "Z" if existing.released_at else None,
        )

    # Get ordering URL from merchant
    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    ordering_url = getattr(merchant, "ordering_url", None)
    if not ordering_url:
        raise HTTPException(
            status_code=400,
            detail="This merchant does not support online ordering",
        )

    # Create queued order
    queued_order = QueuedOrder(
        arrival_session_id=session.id,
        merchant_id=session.merchant_id,
        ordering_url=ordering_url,
        order_number=req.order_number,
        payload_json=req.payload,
    )

    db.add(queued_order)
    db.commit()
    db.refresh(queued_order)

    _capture_event("ev_arrival.order_queued", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "queued_order_id": str(queued_order.id),
        "order_number": req.order_number,
    })

    logger.info(f"Created queued order {queued_order.id} for session {session_id}")

    return QueuedOrderResponse(
        id=str(queued_order.id),
        status=queued_order.status,
        ordering_url=queued_order.ordering_url,
        release_url=None,
        order_number=queued_order.order_number,
        created_at=queued_order.created_at.isoformat() + "Z",
        released_at=None,
    )


@router.get("/{session_id}/queued-order")
async def get_queued_order(
    session_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """Get the queued order for this session (if any)."""
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    queued_order = (
        db.query(QueuedOrder)
        .filter(QueuedOrder.arrival_session_id == session.id)
        .first()
    )

    if not queued_order:
        return {"queued_order": None}

    return {
        "queued_order": QueuedOrderResponse(
            id=str(queued_order.id),
            status=queued_order.status,
            ordering_url=queued_order.ordering_url,
            release_url=queued_order.release_url,
            order_number=queued_order.order_number,
            created_at=queued_order.created_at.isoformat() + "Z",
            released_at=queued_order.released_at.isoformat() + "Z" if queued_order.released_at else None,
        )
    }


@router.post("/{session_id}/trigger-arrival", response_model=TriggerArrivalResponse)
async def trigger_arrival(
    session_id: str,
    req: TriggerArrivalRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Arrival trigger — called when driver arrives near restaurant.

    This releases the queued order based on:
    - Fulfillment type (dine-in vs curbside)
    - Restaurant prep time
    - Walk time (for dine-in)
    """
    session = db.query(ArrivalSession).filter(ArrivalSession.id == session_id).first()
    if not session or session.driver_id != driver.id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check if order is queued
    if session.queued_order_status != "queued":
        return TriggerArrivalResponse(
            status=session.queued_order_status or "unknown",
            order_released=session.queued_order_status in ("released", "preparing", "ready"),
        )

    # Verify location is near merchant
    merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    distance_m = haversine_m(req.lat, req.lng, merchant.lat, merchant.lng)

    if distance_m > 500:  # Must be within 500m
        return TriggerArrivalResponse(
            status="queued",
            order_released=False,
        )

    # Record arrival
    session.arrival_lat = req.lat
    session.arrival_lng = req.lng
    session.arrival_accuracy_m = req.accuracy_m
    session.arrival_detected_at = datetime.utcnow()
    session.arrival_distance_m = distance_m

    # Calculate when to fire order
    prep_time_minutes = 15  # Default 15 min (can be made configurable per merchant)
    walk_time_minutes = max(1, int(distance_m / 80))  # ~80m/min walking

    if session.fulfillment_type == "ev_dine_in":
        # For dine-in: Fire so food is ready when driver walks in
        # If prep_time > walk_time, fire immediately
        # If prep_time < walk_time, we'd need to fire BEFORE arrival (not possible here)
        # So: always fire immediately on arrival for dine-in
        delay_minutes = 0
        estimated_ready = prep_time_minutes
    else:
        # For curbside: Fire immediately, merchant will bring when ready
        delay_minutes = 0
        estimated_ready = prep_time_minutes

    # Release the order
    session.queued_order_status = "released"
    session.order_released_at = datetime.utcnow()

    # Update session status
    if session.status == "pending_order":
        session.status = "awaiting_arrival"

    # Notify merchant
    notif_config = (
        db.query(MerchantNotificationConfig)
        .filter(MerchantNotificationConfig.merchant_id == merchant.id)
        .first()
    )

    charger = None
    if session.charger_id:
        charger = db.query(Charger).filter(Charger.id == session.charger_id).first()

    if notif_config:
        charger_name = charger.name if charger else None
        notification_method = await notify_merchant(
            notify_sms=notif_config.notify_sms,
            notify_email=notif_config.notify_email,
            sms_phone=notif_config.sms_phone,
            email_address=notif_config.email_address,
            order_number=session.order_number or "N/A",
            arrival_type=session.fulfillment_type or session.arrival_type,
            vehicle_color=session.vehicle_color,
            vehicle_model=session.vehicle_model,
            charger_name=charger_name,
            merchant_name=merchant.name,
            merchant_reply_code=session.merchant_reply_code or "",
            fulfillment_type=session.fulfillment_type,
            arrival_distance_m=session.arrival_distance_m,
        )

        if notification_method != "none":
            session.status = "merchant_notified"
            session.merchant_notified_at = datetime.utcnow()

    db.commit()

    _capture_event("ev_arrival.order_released", driver.id, {
        "session_id": session_id,
        "merchant_id": session.merchant_id,
        "fulfillment_type": session.fulfillment_type,
        "distance_m": int(distance_m),
        "prep_time_minutes": prep_time_minutes,
    })

    return TriggerArrivalResponse(
        status="released",
        order_released=True,
        estimated_ready_minutes=estimated_ready,
    )
