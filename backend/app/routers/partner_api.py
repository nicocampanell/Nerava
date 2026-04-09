"""
Partner Incentive API Router — X-Partner-Key authenticated endpoints.

External partners submit charging sessions and receive incentive evaluations.
Supports candidate/pending sessions, webhook delivery, and reward breakdowns.
"""

import logging
import math
from typing import Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.dependencies.partner_auth import get_current_partner, require_partner_scope
from app.models.partner import Partner, PartnerAPIKey
from app.models.session_event import SessionEvent
from app.schemas.partner import (
    PartnerSessionIngestRequest,
    PartnerSessionUpdateRequest,
)
from app.services.campaign_service import CampaignService
from app.services.partner_session_service import PartnerSessionService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/partners", tags=["partner-api"])


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate distance in meters between two lat/lng points."""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def _fire_session_webhook(partner, session_data):
    """Fire partner.session.resolved webhook in background."""
    try:
        from app.services.webhook_delivery_service import WebhookDeliveryService

        await WebhookDeliveryService.deliver(partner, "partner.session.resolved", session_data)
    except Exception as e:
        logger.error(f"Webhook delivery failed for partner {partner.slug}: {e}")


# --- Session Endpoints ---


@router.post("/sessions", status_code=202)
def ingest_session(
    req: PartnerSessionIngestRequest,
    background_tasks: BackgroundTasks,
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(
        require_partner_scope("sessions:write")
    ),
    db: Session = Depends(get_db),
):
    """
    Submit a charging session for incentive evaluation.

    Idempotent: re-submitting the same partner_session_id returns the existing session (200).
    Supports status="candidate" for soft-signal sessions that skip incentive evaluation.
    """
    partner, api_key = partner_and_key
    result = PartnerSessionService.ingest_session(
        db,
        partner=partner,
        partner_session_id=req.partner_session_id,
        partner_driver_id=req.partner_driver_id,
        status=req.status,
        session_start=req.session_start,
        session_end=req.session_end,
        charger_id=req.charger_id,
        charger_network=req.charger_network,
        connector_type=req.connector_type,
        power_kw=req.power_kw,
        kwh_delivered=req.kwh_delivered,
        lat=req.lat,
        lng=req.lng,
        vehicle_vin=req.vehicle_vin,
        vehicle_make=req.vehicle_make,
        vehicle_model=req.vehicle_model,
        vehicle_year=req.vehicle_year,
        battery_start_pct=req.battery_start_pct,
        battery_end_pct=req.battery_end_pct,
        signal_confidence=req.signal_confidence,
        charging_state_hint=req.charging_state_hint,
    )
    # Remove internal field
    is_new = result.pop("_is_new", True)

    # Fire webhook for completed sessions
    if is_new and result.get("status") == "completed" and partner.webhook_enabled:
        background_tasks.add_task(_fire_session_webhook, partner, result)

    if not is_new:
        # Idempotent return — existing session
        return result  # 200 (FastAPI default for non-new)
    return result


@router.get("/sessions")
def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(
        require_partner_scope("sessions:read")
    ),
    db: Session = Depends(get_db),
):
    """List this partner's submitted sessions."""
    partner, _ = partner_and_key
    sessions = PartnerSessionService.list_sessions(db, partner, limit=limit, offset=offset)
    # Remove internal field
    for s in sessions:
        s.pop("_is_new", None)
    return {"sessions": sessions, "limit": limit, "offset": offset}


@router.get("/sessions/{partner_session_id}")
def get_session(
    partner_session_id: str,
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(
        require_partner_scope("sessions:read")
    ),
    db: Session = Depends(get_db),
):
    """Get a specific session by the partner's session ID."""
    partner, _ = partner_and_key
    result = PartnerSessionService.get_session(db, partner, partner_session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    result.pop("_is_new", None)
    return result


@router.patch("/sessions/{partner_session_id}")
def update_session(
    partner_session_id: str,
    req: PartnerSessionUpdateRequest,
    background_tasks: BackgroundTasks,
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(
        require_partner_scope("sessions:write")
    ),
    db: Session = Depends(get_db),
):
    """Update telemetry or complete a session."""
    partner, _ = partner_and_key
    result = PartnerSessionService.update_session(
        db,
        partner=partner,
        partner_session_id=partner_session_id,
        status=req.status,
        session_end=req.session_end,
        kwh_delivered=req.kwh_delivered,
        power_kw=req.power_kw,
        battery_end_pct=req.battery_end_pct,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")

    is_new = result.pop("_is_new", None)

    # Fire webhook only on the first completion transition (not on replay)
    if is_new and result.get("status") == "completed" and partner.webhook_enabled:
        background_tasks.add_task(_fire_session_webhook, partner, result)

    return result


# --- Grant Endpoints ---


@router.get("/grants")
def list_grants(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(require_partner_scope("grants:read")),
    db: Session = Depends(get_db),
):
    """List incentive grants for this partner's sessions."""
    partner, _ = partner_and_key
    grants = PartnerSessionService.list_grants(db, partner, limit=limit, offset=offset)
    return {"grants": grants, "limit": limit, "offset": offset}


# --- Campaign Discovery ---


@router.get("/campaigns/available")
def list_available_campaigns(
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
    charger_network: Optional[str] = Query(None),
    connector_type: Optional[str] = Query(None),
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(
        require_partner_scope("campaigns:read")
    ),
    db: Session = Depends(get_db),
):
    """
    List active campaigns that accept partner sessions.
    Optionally filter by location, network, or connector type.
    """
    partner, _ = partner_and_key
    campaigns = CampaignService.get_active_campaigns(db)

    results = []
    for c in campaigns:
        if not getattr(c, "allow_partner_sessions", True):
            continue
        if c.rule_partner_ids and partner.id not in c.rule_partner_ids:
            continue
        if c.rule_min_trust_tier and partner.trust_tier > c.rule_min_trust_tier:
            continue
        if charger_network and c.rule_charger_networks:
            if charger_network not in c.rule_charger_networks:
                continue
        if connector_type and c.rule_connector_types:
            if connector_type not in c.rule_connector_types:
                continue
        # Geo filtering: skip campaigns whose geo radius doesn't contain the caller
        if (
            lat is not None
            and lng is not None
            and c.rule_geo_center_lat is not None
            and c.rule_geo_center_lng is not None
            and c.rule_geo_radius_m is not None
        ):
            dist = _haversine_m(lat, lng, c.rule_geo_center_lat, c.rule_geo_center_lng)
            if dist > c.rule_geo_radius_m:
                continue

        results.append(
            {
                "campaign_id": c.id,
                "name": c.name,
                "sponsor_name": c.sponsor_name,
                "cost_per_session_cents": c.cost_per_session_cents,
                "rule_min_duration_minutes": c.rule_min_duration_minutes,
                "charger_networks": c.rule_charger_networks,
                "connector_types": c.rule_connector_types,
                "start_date": c.start_date.isoformat() if c.start_date else None,
                "end_date": c.end_date.isoformat() if c.end_date else None,
                "allow_partner_sessions": c.allow_partner_sessions,
                "rule_min_trust_tier": c.rule_min_trust_tier,
            }
        )

    return {"campaigns": results}


# --- Partner Profile ---


@router.get("/me")
def get_partner_profile(
    partner_and_key: Tuple[Partner, PartnerAPIKey] = Depends(get_current_partner),
    db: Session = Depends(get_db),
):
    """Get the authenticated partner's profile and usage stats."""
    partner, _ = partner_and_key
    source = f"partner_{partner.slug}"

    total_sessions = (
        db.query(SessionEvent)
        .filter(
            SessionEvent.source == source,
        )
        .count()
    )

    from app.models.session_event import IncentiveGrant

    total_grants = (
        db.query(IncentiveGrant)
        .join(SessionEvent, IncentiveGrant.session_event_id == SessionEvent.id)
        .filter(
            SessionEvent.source == source,
        )
        .count()
    )

    return {
        "id": partner.id,
        "name": partner.name,
        "slug": partner.slug,
        "partner_type": partner.partner_type,
        "trust_tier": partner.trust_tier,
        "status": partner.status,
        "rate_limit_rpm": partner.rate_limit_rpm,
        "total_sessions": total_sessions,
        "total_grants": total_grants,
    }
