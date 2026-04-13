"""
Driver Orders Router

Endpoints for tracking when a driver opens a merchant ordering URL
(e.g. Toast in-app browser) and when the order is completed.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.driver import get_current_driver
from ..models.driver_order import DriverOrder
from ..models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/driver/orders", tags=["driver-orders"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class StartOrderRequest(BaseModel):
    merchant_id: str
    ordering_url: str
    merchant_name: Optional[str] = None
    session_id: Optional[str] = None


class StartOrderResponse(BaseModel):
    order_id: str
    status: str


class CompleteOrderRequest(BaseModel):
    order_id: str = Field(
        ...,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    completion_url: Optional[str] = None


class CompleteOrderResponse(BaseModel):
    order_id: str
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/start", response_model=StartOrderResponse)
def start_order(
    body: StartOrderRequest,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> StartOrderResponse:
    """Record that a driver opened a merchant ordering URL."""

    order = DriverOrder(
        driver_id=current_user.id,
        merchant_id=body.merchant_id,
        merchant_name=body.merchant_name,
        ordering_url=body.ordering_url,
        session_id=body.session_id,
        status="started",
        opened_at=datetime.now(timezone.utc),
    )
    db.add(order)

    try:
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        logger.error(
            "Failed to create driver order for driver_id=%s",
            current_user.id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create order record",
        )

    logger.info(
        "Driver %s started order %s for merchant %s",
        current_user.id,
        order.id,
        body.merchant_id,
    )

    return StartOrderResponse(order_id=str(order.id), status=order.status)


@router.post("/complete", response_model=CompleteOrderResponse)
def complete_order(
    body: CompleteOrderRequest,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
) -> CompleteOrderResponse:
    """Mark a driver order as completed."""

    order = db.query(DriverOrder).filter(DriverOrder.id == body.order_id).first()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # Ownership check: driver can only complete their own orders
    if order.driver_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to complete this order",
        )

    order.status = "completed"
    order.completed_at = datetime.now(timezone.utc)
    if body.completion_url:
        order.completion_url = body.completion_url

    try:
        db.commit()
        db.refresh(order)
    except Exception:
        db.rollback()
        logger.error(
            "Failed to complete driver order %s for driver_id=%s",
            body.order_id,
            current_user.id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update order record",
        )

    logger.info(
        "Driver %s completed order %s",
        current_user.id,
        order.id,
    )

    return CompleteOrderResponse(order_id=str(order.id), status=order.status)
