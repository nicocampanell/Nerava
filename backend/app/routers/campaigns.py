"""
Campaigns Router — Sponsor/admin campaign management.

CRUD for campaigns, grant listing, budget management.
Used by the campaign portal (console.nerava.network).
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies.domain import get_current_user
from ..dependencies.driver import get_current_driver
from ..models import Charger
from ..models.campaign import Campaign
from ..models.session_event import IncentiveGrant, SessionEvent
from ..models.user import User
from ..services.campaign_service import CampaignService
from ..services.geo import haversine_m

router = APIRouter(prefix="/v1/campaigns", tags=["campaigns"])


def _is_admin_or_sponsor(user: User) -> bool:
    """Check if user is admin or sponsor (can manage campaigns)."""
    if getattr(user, "admin_role", None) in ("super_admin", "admin"):
        return True
    if getattr(user, "role_flags", None) and "sponsor" in (getattr(user, "role_flags", "") or ""):
        return True
    return False


# --- Request/Response Schemas ---


class CampaignRulesInput(BaseModel):
    charger_ids: Optional[List[str]] = None
    charger_networks: Optional[List[str]] = None
    zone_ids: Optional[List[str]] = None
    geo_center_lat: Optional[float] = None
    geo_center_lng: Optional[float] = None
    geo_radius_m: Optional[int] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    days_of_week: Optional[List[int]] = None
    min_duration_minutes: Optional[int] = 15
    max_duration_minutes: Optional[int] = None
    min_power_kw: Optional[float] = None
    connector_types: Optional[List[str]] = None
    driver_session_count_min: Optional[int] = None
    driver_session_count_max: Optional[int] = None
    driver_allowlist: Optional[List[str]] = None


class CampaignCapsInput(BaseModel):
    per_day: Optional[int] = None
    per_campaign: Optional[int] = None
    per_charger: Optional[int] = None


class CreateCampaignRequest(BaseModel):
    sponsor_name: str
    sponsor_email: Optional[str] = None
    sponsor_logo_url: Optional[str] = None
    sponsor_type: Optional[str] = None
    name: str
    description: Optional[str] = None
    campaign_type: str = "custom"
    priority: int = 100
    budget_cents: int
    cost_per_session_cents: int
    max_sessions: Optional[int] = None
    start_date: str  # ISO format
    end_date: Optional[str] = None
    auto_renew: bool = False
    auto_renew_budget_cents: Optional[int] = None
    rules: Optional[CampaignRulesInput] = None
    caps: Optional[CampaignCapsInput] = None
    offer_url: Optional[str] = None


class UpdateCampaignRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    campaign_type: Optional[str] = None
    priority: Optional[int] = None
    budget_cents: Optional[int] = None
    cost_per_session_cents: Optional[int] = None
    max_sessions: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    auto_renew: Optional[bool] = None
    auto_renew_budget_cents: Optional[int] = None
    rules: Optional[CampaignRulesInput] = None
    caps: Optional[CampaignCapsInput] = None
    offer_url: Optional[str] = None


# --- Endpoints ---


@router.post("/")
async def create_campaign(
    req: CreateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new campaign (draft status)."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")

    start_date = datetime.fromisoformat(req.start_date)
    end_date = datetime.fromisoformat(req.end_date) if req.end_date else None

    campaign = CampaignService.create_campaign(
        db,
        sponsor_name=req.sponsor_name,
        sponsor_email=req.sponsor_email,
        sponsor_logo_url=req.sponsor_logo_url,
        sponsor_type=req.sponsor_type,
        name=req.name,
        description=req.description,
        campaign_type=req.campaign_type,
        priority=req.priority,
        budget_cents=req.budget_cents,
        cost_per_session_cents=req.cost_per_session_cents,
        start_date=start_date,
        end_date=end_date,
        rules=req.rules.model_dump() if req.rules else None,
        caps=req.caps.model_dump() if req.caps else None,
        created_by_user_id=current_user.id,
        offer_url=req.offer_url,
    )
    return {"campaign": _campaign_to_dict(campaign)}


@router.get("/")
async def list_campaigns(
    status: Optional[str] = None,
    sponsor_name: Optional[str] = None,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List campaigns. Admin sees all, sponsors see their own."""
    # Admins see everything; sponsors see only their own campaigns
    is_admin = getattr(current_user, "admin_role", None) in ("super_admin", "admin")
    owner_filter = None if is_admin else current_user.id

    campaigns = CampaignService.list_campaigns(
        db,
        sponsor_name=sponsor_name,
        status=status,
        limit=limit,
        offset=offset,
        owner_user_id=owner_filter,
    )
    return {
        "campaigns": [_campaign_to_dict(c) for c in campaigns],
        "count": len(campaigns),
    }


@router.get("/driver/active")
async def get_driver_active_campaigns(
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    charger_id: Optional[str] = None,
    db: Session = Depends(get_db),
    driver: User = Depends(get_current_driver),
):
    """Return active campaigns relevant to the driver's location."""
    from app.models.while_you_charge import Charger
    from app.routers.chargers import _match_campaign_reward

    active = CampaignService.get_active_campaigns(db)
    results = []

    # Look up charger network if charger_id is provided
    charger_network = ""
    if charger_id:
        charger_obj = db.query(Charger).filter(Charger.id == charger_id).first()
        charger_network = charger_obj.network_name if charger_obj else ""

    for c in active:
        eligible = CampaignService.check_driver_caps(db, c, driver.id, charger_id)

        # Geo matching: if campaign has geo rule and driver provided location
        if c.rule_geo_center_lat and c.rule_geo_center_lng and c.rule_geo_radius_m:
            if lat is not None and lng is not None:
                dist = haversine_m(lat, lng, c.rule_geo_center_lat, c.rule_geo_center_lng)
                if dist > c.rule_geo_radius_m:
                    continue
            else:
                continue

        # Charger matching (ID + network, fuzzy)
        if charger_id:
            if not _match_campaign_reward(c, charger_id, charger_network):
                continue

        results.append(
            {
                "id": c.id,
                "name": c.name,
                "sponsor_name": c.sponsor_name,
                "sponsor_logo_url": c.sponsor_logo_url,
                "description": c.description,
                "reward_cents": c.cost_per_session_cents,
                "campaign_type": c.campaign_type,
                "eligible": eligible,
                "end_date": c.end_date.isoformat() if c.end_date else None,
                "offer_url": c.offer_url,
            }
        )

    return {"campaigns": results}


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get campaign details. Sponsors can only view their own."""
    campaign = CampaignService.get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    is_admin = getattr(current_user, "admin_role", None) in ("super_admin", "admin")
    if not is_admin and campaign.created_by_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": _campaign_to_dict(campaign)}


@router.put("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    req: UpdateCampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a draft/paused campaign."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")

    # Sponsors can only update their own campaigns
    is_admin = getattr(current_user, "admin_role", None) in ("super_admin", "admin")
    if not is_admin:
        existing = CampaignService.get_campaign(db, campaign_id)
        if not existing or existing.created_by_user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Campaign not found")

    update_data = req.model_dump(exclude_none=True)

    # Flatten rules into rule_ columns
    if "rules" in update_data:
        rules = update_data.pop("rules")
        if rules:
            for key, val in rules.items():
                update_data[f"rule_{key}"] = val

    # Flatten caps
    if "caps" in update_data:
        caps = update_data.pop("caps")
        if caps:
            if caps.get("per_day") is not None:
                update_data["max_grants_per_driver_per_day"] = caps["per_day"]
            if caps.get("per_campaign") is not None:
                update_data["max_grants_per_driver_per_campaign"] = caps["per_campaign"]
            if caps.get("per_charger") is not None:
                update_data["max_grants_per_driver_per_charger"] = caps["per_charger"]

    # Parse dates
    if "start_date" in update_data:
        update_data["start_date"] = datetime.fromisoformat(update_data["start_date"])
    if "end_date" in update_data:
        update_data["end_date"] = datetime.fromisoformat(update_data["end_date"])

    try:
        campaign = CampaignService.update_campaign(db, campaign_id, **update_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": _campaign_to_dict(campaign)}


@router.post("/{campaign_id}/activate")
async def activate_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activate a draft campaign."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")
    try:
        campaign = CampaignService.activate_campaign(db, campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": _campaign_to_dict(campaign)}


@router.post("/{campaign_id}/checkout")
async def create_campaign_checkout(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create Stripe Checkout session to fund a campaign."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")

    campaign = CampaignService.get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status != "draft":
        raise HTTPException(
            status_code=400, detail="Only draft campaigns can be funded via checkout"
        )

    # If already pending, return existing checkout URL
    if campaign.funding_status == "pending" and campaign.stripe_checkout_session_id:
        return {
            "checkout_url": None,
            "session_id": campaign.stripe_checkout_session_id,
            "status": "already_pending",
            "message": "Checkout session already created. Complete payment or create a new campaign.",
        }

    if campaign.funding_status == "funded":
        raise HTTPException(status_code=400, detail="Campaign is already funded")

    import stripe as stripe_module

    from ..core.config import settings

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    stripe_module.api_key = settings.STRIPE_SECRET_KEY

    try:
        # Determine success/cancel URLs for the console
        import os

        console_url = os.getenv("CONSOLE_URL", "").rstrip("/")
        if not console_url:
            # Fallback: derive from DRIVER_APP_URL or FRONTEND_URL
            base = settings.DRIVER_APP_URL or settings.FRONTEND_URL
            console_url = base.replace("app.", "console.") if "app." in base else base
        success_url = f"{console_url}/campaigns/{campaign_id}?funded=true"
        cancel_url = f"{console_url}/campaigns/{campaign_id}?funded=false"

        # Fee-inclusive: sponsor pays budget_cents total, Nerava keeps platform fee
        gross_amount = campaign.budget_cents
        fee_bps = settings.PLATFORM_FEE_BPS  # 2000 = 20%
        platform_fee = int(gross_amount * fee_bps / 10000)
        net_to_rewards = gross_amount - platform_fee

        checkout_session = stripe_module.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": f"Driver Rewards — {campaign.name}",
                            "description": f"${net_to_rewards / 100:.2f} allocated to driver rewards",
                        },
                        "unit_amount": net_to_rewards,
                    },
                    "quantity": 1,
                },
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": "Nerava Platform Fee",
                            "description": f"{fee_bps / 100:.0f}% platform fee",
                        },
                        "unit_amount": platform_fee,
                    },
                    "quantity": 1,
                },
            ],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "type": "campaign_funding",
                "campaign_id": str(campaign.id),
                "campaign_name": campaign.name,
                "gross_amount_cents": str(gross_amount),
                "platform_fee_cents": str(platform_fee),
                "net_reward_cents": str(net_to_rewards),
            },
        )

        campaign.funding_status = "pending"
        campaign.stripe_checkout_session_id = checkout_session.id
        campaign.updated_at = datetime.utcnow()
        db.commit()

        return {
            "checkout_url": checkout_session.url,
            "session_id": checkout_session.id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create checkout: {str(e)}")


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause an active campaign."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")
    try:
        campaign = CampaignService.pause_campaign(db, campaign_id, reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": _campaign_to_dict(campaign)}


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused campaign."""
    if not _is_admin_or_sponsor(current_user):
        raise HTTPException(status_code=403, detail="Sponsor or admin access required")
    try:
        campaign = CampaignService.resume_campaign(db, campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": _campaign_to_dict(campaign)}


@router.get("/{campaign_id}/grants")
async def list_campaign_grants(
    campaign_id: str,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List grants for a campaign."""
    campaign = CampaignService.get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    grants = (
        db.query(IncentiveGrant)
        .filter(IncentiveGrant.campaign_id == campaign_id)
        .order_by(IncentiveGrant.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    total = db.query(IncentiveGrant).filter(IncentiveGrant.campaign_id == campaign_id).count()

    return {
        "grants": [_grant_to_dict(g, db) for g in grants],
        "total": total,
        "count": len(grants),
    }


@router.get("/{campaign_id}/budget")
async def get_campaign_budget(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get campaign budget status."""
    budget = CampaignService.check_budget(db, campaign_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return budget


# --- Charger utilization endpoint (for Charger Explorer) ---


@router.get("/chargers/browse")
async def browse_chargers(
    search: Optional[str] = None,
    network: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = Query(default=500, le=2000),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Browse all chargers in the database for campaign targeting."""
    from datetime import timedelta

    from sqlalchemy import func

    query = db.query(Charger)

    if search:
        term = f"%{search}%"
        query = query.filter(
            (Charger.name.ilike(term))
            | (Charger.address.ilike(term))
            | (Charger.city.ilike(term))
            | (Charger.network_name.ilike(term))
            | (Charger.id.ilike(term))
        )

    if network:
        query = query.filter(Charger.network_name.ilike(f"%{network}%"))

    if state:
        query = query.filter(Charger.state == state)

    total = query.count()
    chargers = query.order_by(Charger.name).offset(offset).limit(limit).all()

    # Get utilization stats for these chargers (last 30 days)
    since = datetime.utcnow() - timedelta(days=30)
    charger_ids = [c.id for c in chargers]
    util_rows = (
        (
            db.query(
                SessionEvent.charger_id,
                func.count(SessionEvent.id).label("total_sessions"),
                func.count(func.distinct(SessionEvent.driver_user_id)).label("unique_drivers"),
            )
            .filter(
                SessionEvent.charger_id.in_(charger_ids),
                SessionEvent.session_start >= since,
                SessionEvent.session_end.is_not(None),
            )
            .group_by(SessionEvent.charger_id)
            .all()
        )
        if charger_ids
        else []
    )

    util_map = {
        r.charger_id: {"total_sessions": r.total_sessions, "unique_drivers": r.unique_drivers}
        for r in util_rows
    }

    return {
        "chargers": [
            {
                "id": c.id,
                "name": c.name or c.id,
                "network_name": c.network_name,
                "lat": c.lat,
                "lng": c.lng,
                "address": c.address,
                "city": c.city,
                "state": c.state,
                "power_kw": c.power_kw,
                "num_evse": c.num_evse,
                "connector_types": c.connector_types,
                "pricing_per_kwh": c.pricing_per_kwh,
                "total_sessions": util_map.get(c.id, {}).get("total_sessions", 0),
                "unique_drivers": util_map.get(c.id, {}).get("unique_drivers", 0),
            }
            for c in chargers
        ],
        "total": total,
    }


@router.get("/utilization/chargers")
async def get_charger_utilization(
    charger_ids: Optional[str] = None,  # comma-separated
    since_days: int = Query(default=30, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get session counts per charger for utilization dashboard."""
    from datetime import timedelta

    from sqlalchemy import func

    since = datetime.utcnow() - timedelta(days=since_days)
    query = (
        db.query(
            SessionEvent.charger_id,
            func.count(SessionEvent.id).label("total_sessions"),
            func.count(func.distinct(SessionEvent.driver_user_id)).label("unique_drivers"),
            func.avg(SessionEvent.duration_minutes).label("avg_duration_minutes"),
        )
        .filter(
            SessionEvent.session_start >= since,
            SessionEvent.session_end.is_not(None),
            SessionEvent.charger_id.is_not(None),
        )
        .group_by(SessionEvent.charger_id)
    )

    if charger_ids:
        ids = [c.strip() for c in charger_ids.split(",")]
        query = query.filter(SessionEvent.charger_id.in_(ids))

    rows = query.all()
    return {
        "chargers": [
            {
                "charger_id": row.charger_id,
                "total_sessions": row.total_sessions,
                "unique_drivers": row.unique_drivers,
                "avg_duration_minutes": (
                    round(row.avg_duration_minutes, 1) if row.avg_duration_minutes else 0
                ),
            }
            for row in rows
        ]
    }


# --- Helpers ---


def _campaign_to_dict(c: Campaign) -> dict:
    return {
        "id": c.id,
        "sponsor_name": c.sponsor_name,
        "sponsor_email": c.sponsor_email,
        "sponsor_logo_url": c.sponsor_logo_url,
        "sponsor_type": c.sponsor_type,
        "name": c.name,
        "description": c.description,
        "campaign_type": c.campaign_type,
        "status": c.status,
        "priority": c.priority,
        "budget_cents": c.budget_cents,
        "spent_cents": c.spent_cents,
        "cost_per_session_cents": c.cost_per_session_cents,
        "max_sessions": c.max_sessions,
        "sessions_granted": c.sessions_granted,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "auto_renew": c.auto_renew,
        "auto_renew_budget_cents": c.auto_renew_budget_cents,
        "max_grants_per_driver_per_day": c.max_grants_per_driver_per_day,
        "max_grants_per_driver_per_campaign": c.max_grants_per_driver_per_campaign,
        "max_grants_per_driver_per_charger": c.max_grants_per_driver_per_charger,
        "rules": {
            "charger_ids": c.rule_charger_ids,
            "charger_networks": c.rule_charger_networks,
            "zone_ids": c.rule_zone_ids,
            "geo_center_lat": c.rule_geo_center_lat,
            "geo_center_lng": c.rule_geo_center_lng,
            "geo_radius_m": c.rule_geo_radius_m,
            "time_start": c.rule_time_start,
            "time_end": c.rule_time_end,
            "days_of_week": c.rule_days_of_week,
            "min_duration_minutes": c.rule_min_duration_minutes,
            "max_duration_minutes": c.rule_max_duration_minutes,
            "min_power_kw": c.rule_min_power_kw,
            "connector_types": c.rule_connector_types,
            "driver_session_count_min": c.rule_driver_session_count_min,
            "driver_session_count_max": c.rule_driver_session_count_max,
            "driver_allowlist": c.rule_driver_allowlist,
        },
        "funding_status": getattr(c, "funding_status", "unfunded") or "unfunded",
        "funded_at": c.funded_at.isoformat() if getattr(c, "funded_at", None) else None,
        "gross_funding_cents": getattr(c, "gross_funding_cents", None),
        "platform_fee_cents": getattr(c, "platform_fee_cents", None),
        "offer_url": c.offer_url,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def _grant_to_dict(g: IncentiveGrant, db: Session) -> dict:
    session = db.query(SessionEvent).filter(SessionEvent.id == g.session_event_id).first()
    return {
        "id": g.id,
        "session_event_id": g.session_event_id,
        "campaign_id": g.campaign_id,
        "driver_user_id": g.driver_user_id,
        "amount_cents": g.amount_cents,
        "status": g.status,
        "granted_at": g.granted_at.isoformat() if g.granted_at else None,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "charger_id": session.charger_id if session else None,
        "duration_minutes": session.duration_minutes if session else None,
    }
