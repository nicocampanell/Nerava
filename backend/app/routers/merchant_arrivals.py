"""
Merchant-facing arrival endpoints:
  GET  /v1/merchants/{merchant_id}/arrivals   — list arrival sessions
  GET  /v1/merchants/{merchant_id}/notification-config
  PUT  /v1/merchants/{merchant_id}/notification-config
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.arrival_session import ArrivalSession
from app.models.merchant_notification_config import MerchantNotificationConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchants", tags=["merchant-arrivals"])


class ArrivalSessionItem(BaseModel):
    session_id: str
    status: str
    arrival_type: str
    order_number: Optional[str] = None
    order_total_cents: Optional[int] = None
    vehicle_color: Optional[str] = None
    vehicle_model: Optional[str] = None
    created_at: str
    merchant_notified_at: Optional[str] = None


class ArrivalListResponse(BaseModel):
    sessions: List[ArrivalSessionItem]


class NotificationConfigRequest(BaseModel):
    sms_phone: Optional[str] = None
    email_address: Optional[str] = None
    notify_sms: bool = True
    notify_email: bool = False


class NotificationConfigResponse(BaseModel):
    notify_sms: bool
    notify_email: bool
    sms_phone: Optional[str] = None
    email_address: Optional[str] = None
    pos_integration: str = "none"


@router.get("/{merchant_id}/arrivals", response_model=ArrivalListResponse)
def list_arrivals(
    merchant_id: str,
    db: Session = Depends(get_db),
):
    """List arrival sessions for a merchant (active + recent completed)."""
    sessions = (
        db.query(ArrivalSession)
        .filter(ArrivalSession.merchant_id == merchant_id)
        .order_by(ArrivalSession.created_at.desc())
        .limit(50)
        .all()
    )
    return ArrivalListResponse(
        sessions=[
            ArrivalSessionItem(
                session_id=str(s.id),
                status=s.status,
                arrival_type=s.arrival_type,
                order_number=s.order_number,
                order_total_cents=s.order_total_cents,
                vehicle_color=s.vehicle_color,
                vehicle_model=s.vehicle_model,
                created_at=s.created_at.isoformat() + "Z" if s.created_at else "",
                merchant_notified_at=(
                    s.merchant_notified_at.isoformat() + "Z" if s.merchant_notified_at else None
                ),
            )
            for s in sessions
        ]
    )


@router.get("/{merchant_id}/notification-config", response_model=NotificationConfigResponse)
def get_notification_config(
    merchant_id: str,
    db: Session = Depends(get_db),
):
    """Get merchant notification config."""
    config = (
        db.query(MerchantNotificationConfig)
        .filter(MerchantNotificationConfig.merchant_id == merchant_id)
        .first()
    )
    if not config:
        return NotificationConfigResponse(
            notify_sms=True,
            notify_email=False,
        )
    return NotificationConfigResponse(
        notify_sms=config.notify_sms,
        notify_email=config.notify_email,
        sms_phone=config.sms_phone,
        email_address=config.email_address,
        pos_integration=config.pos_integration,
    )


@router.put("/{merchant_id}/notification-config", response_model=NotificationConfigResponse)
def update_notification_config(
    merchant_id: str,
    req: NotificationConfigRequest,
    db: Session = Depends(get_db),
):
    """Create or update merchant notification config."""
    config = (
        db.query(MerchantNotificationConfig)
        .filter(MerchantNotificationConfig.merchant_id == merchant_id)
        .first()
    )
    if not config:
        config = MerchantNotificationConfig(merchant_id=merchant_id)
        db.add(config)

    config.notify_sms = req.notify_sms
    config.notify_email = req.notify_email
    config.sms_phone = req.sms_phone
    config.email_address = req.email_address
    db.commit()
    db.refresh(config)

    return NotificationConfigResponse(
        notify_sms=config.notify_sms,
        notify_email=config.notify_email,
        sms_phone=config.sms_phone,
        email_address=config.email_address,
        pos_integration=config.pos_integration,
    )
