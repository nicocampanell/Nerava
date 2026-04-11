from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.cache.layers import layered_cache
from app.config import settings
from app.db import get_db
from app.events.domain import ChargeStartedEvent, ChargeStoppedEvent, DomainEvent
from app.services.async_wallet import async_wallet
from app.services.circuit_breaker import wallet_circuit_breaker
from app.services.energyhub_sim import sim
from app.services.idempotency import idempotency_service

router = APIRouter(prefix="/v1/energyhub", tags=["energyhub"])


def parse_at(at: Optional[str]) -> Optional[datetime]:
    if not at:
        return None
    if not settings.energyhub_allow_demo_at:
        raise HTTPException(status_code=403, detail="demo_at_disabled")
    try:
        dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_at_param")


class ChargeStartReq(BaseModel):
    user_id: str = Field(..., description="User email or id")
    hub_id: str = Field(..., description="Hub identifier")


class ChargeStartResp(BaseModel):
    session_id: str
    active_window: Optional[dict]


class ChargeStopReq(BaseModel):
    session_id: str
    kwh_consumed: float = Field(..., gt=0)


class ChargeStopResp(BaseModel):
    session_id: str
    user_id: str
    hub_id: str
    kwh: float
    window_applied: Optional[str]
    grid_reward_usd: float
    merchant_reward_usd: float
    total_reward_usd: float
    wallet_balance_cents: Optional[int] = None
    message: str


@router.get("/windows")
async def list_windows(at: Optional[str] = Query(None, description="ISO datetime override")):
    # Use layered cache with single-flight protection
    cache_key = f"energyhub:windows:{at or 'current'}"

    async def fetch_windows():
        override_dt = parse_at(at)
        return sim.list_windows(override_dt)

    result = await layered_cache.get_or_set(
        cache_key, fetch_windows, ttl=settings.cache_ttl_windows
    )
    return result


@router.post("/events/charge-start", response_model=ChargeStartResp)
async def charge_start(payload: ChargeStartReq, at: Optional[str] = Query(None)):
    override_dt = parse_at(at)
    result = sim.start_session(payload.user_id, payload.hub_id, override_dt)

    # Create and publish charge started event
    charge_event = ChargeStartedEvent(
        session_id=result["session_id"],
        user_id=payload.user_id,
        hub_id=payload.hub_id,
        started_at=datetime.utcnow(),
        window_id=result["active_window"]["id"] if result["active_window"] else None,
    )

    # Store in outbox for reliable delivery
    await _store_outbox_event(charge_event)

    return result


@router.post("/events/charge-stop", response_model=ChargeStopResp)
async def charge_stop(payload: ChargeStopReq, at: Optional[str] = Query(None)):
    # Check for idempotency
    idempotency_key = f"charge-stop:{payload.session_id}:{payload.kwh_consumed}"
    cached_result = await idempotency_service.get_result(
        "charge-stop", {"session_id": payload.session_id, "kwh_consumed": payload.kwh_consumed}
    )

    if cached_result:
        return cached_result

    override_dt = parse_at(at)
    try:
        result = sim.stop_session(payload.session_id, payload.kwh_consumed, override_dt)
    except KeyError:
        raise HTTPException(status_code=404, detail="session_not_found")

    cents = int(round(result["total_reward_usd"] * 100))

    if settings.enable_sync_credit:
        # Synchronous credit (for demo) with circuit breaker
        new_balance: Optional[int] = None
        try:
            response = await wallet_circuit_breaker.post(
                "/v1/wallet/credit_qs", params={"user_id": result["user_id"], "cents": cents}
            )
            if response.status_code == 200:
                jd = response.json()
                new_balance = jd.get("new_balance_cents") or jd.get("balance_cents")
        except Exception:
            pass
        result["wallet_balance_cents"] = new_balance
    else:
        # Asynchronous credit (production)
        await async_wallet.queue_wallet_credit(result["user_id"], cents, result["session_id"])
        # Return 202 with estimated reward
        result["wallet_balance_cents"] = None  # Will be updated async

    # Create and publish charge stopped event
    charge_stopped_event = ChargeStoppedEvent(
        session_id=result["session_id"],
        user_id=result["user_id"],
        hub_id=result["hub_id"],
        stopped_at=datetime.utcnow(),
        kwh_consumed=result["kwh"],
        window_id=result["window_applied"],
        grid_reward_usd=result["grid_reward_usd"],
        merchant_reward_usd=result["merchant_reward_usd"],
        total_reward_usd=result["total_reward_usd"],
    )

    # Store in outbox for reliable delivery
    await _store_outbox_event(charge_stopped_event)

    # Store result for idempotency
    await idempotency_service.store_result(
        "charge-stop",
        {"session_id": payload.session_id, "kwh_consumed": payload.kwh_consumed},
        result,
    )

    return result


async def _store_outbox_event(event: DomainEvent):
    """Store an event in the outbox for reliable delivery"""
    try:
        db = next(get_db())
        db.execute(
            text(
                """
            INSERT INTO outbox_events (event_type, payload_json, created_at)
            VALUES (:event_type, :payload_json, :created_at)
        """
            ),
            {
                "event_type": event.event_type,
                "payload_json": json.dumps(event.__dict__, default=str),
                "created_at": event.timestamp,
            },
        )
        db.commit()
    except Exception as e:
        # Log error but don't fail the request
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Error storing outbox event: {e}")


@router.delete("/dev/reset")
async def dev_reset():
    sim.reset()
    return {"ok": True}
