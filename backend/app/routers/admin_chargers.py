"""
Admin Charger Management Router

CRUD endpoints for chargers and charger-merchant links.
All endpoints require admin role via JWT authentication.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.dependencies_domain import require_admin
from app.models.user import User
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.audit import log_admin_action

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-chargers"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class LinkedMerchantResponse(BaseModel):
    link_id: int
    merchant_id: str
    merchant_name: Optional[str] = None
    distance_m: Optional[float] = None
    walk_duration_s: Optional[int] = None
    walk_distance_m: Optional[float] = None
    exclusive_title: Optional[str] = None
    exclusive_description: Optional[str] = None
    is_primary: bool = False
    created_at: Optional[datetime] = None


class ChargerListItem(BaseModel):
    id: str
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    network_name: Optional[str] = None
    power_kw: Optional[float] = None
    num_evse: Optional[int] = None
    status: str = "available"
    pricing_per_kwh: Optional[float] = None
    connector_types: Optional[list] = None
    lat: float
    lng: float
    created_at: Optional[datetime] = None
    merchant_count: int = 0


class ChargerListResponse(BaseModel):
    chargers: List[ChargerListItem]
    total: int
    page: int
    page_size: int


class ChargerDetailResponse(BaseModel):
    id: str
    external_id: Optional[str] = None
    name: str
    network_name: Optional[str] = None
    lat: float
    lng: float
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    connector_types: Optional[list] = None
    power_kw: Optional[float] = None
    num_evse: Optional[int] = None
    is_public: bool = True
    access_code: Optional[str] = None
    pricing_per_kwh: Optional[float] = None
    pricing_source: Optional[str] = None
    nerava_score: Optional[float] = None
    status: str = "available"
    last_verified_at: Optional[datetime] = None
    logo_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    linked_merchants: List[LinkedMerchantResponse] = []


class ChargerCreateRequest(BaseModel):
    id: str = Field(..., description="Charger ID (e.g. 'ch_123' or external ID)")
    name: str
    lat: float
    lng: float
    external_id: Optional[str] = None
    network_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    connector_types: Optional[list] = None
    power_kw: Optional[float] = None
    num_evse: Optional[int] = None
    is_public: bool = True
    access_code: Optional[str] = None
    pricing_per_kwh: Optional[float] = None
    pricing_source: Optional[str] = None
    nerava_score: Optional[float] = None
    status: str = "available"
    logo_url: Optional[str] = None


class ChargerUpdateRequest(BaseModel):
    name: Optional[str] = None
    external_id: Optional[str] = None
    network_name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    connector_types: Optional[list] = None
    power_kw: Optional[float] = None
    num_evse: Optional[int] = None
    is_public: Optional[bool] = None
    access_code: Optional[str] = None
    pricing_per_kwh: Optional[float] = None
    pricing_source: Optional[str] = None
    nerava_score: Optional[float] = None
    status: Optional[str] = None
    logo_url: Optional[str] = None


class LinkMerchantRequest(BaseModel):
    merchant_id: str
    distance_m: float
    walk_duration_s: int
    walk_distance_m: Optional[float] = None
    is_primary: bool = False
    override_mode: Optional[str] = None
    suppress_others: bool = False
    exclusive_title: Optional[str] = None
    exclusive_description: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _charger_to_detail(charger: Charger, linked_merchants: List[LinkedMerchantResponse]) -> dict:
    return {
        "id": charger.id,
        "external_id": charger.external_id,
        "name": charger.name,
        "network_name": charger.network_name,
        "lat": charger.lat,
        "lng": charger.lng,
        "address": charger.address,
        "city": charger.city,
        "state": charger.state,
        "zip_code": charger.zip_code,
        "connector_types": charger.connector_types,
        "power_kw": charger.power_kw,
        "num_evse": charger.num_evse,
        "is_public": charger.is_public,
        "access_code": charger.access_code,
        "pricing_per_kwh": charger.pricing_per_kwh,
        "pricing_source": charger.pricing_source,
        "nerava_score": charger.nerava_score,
        "status": charger.status,
        "last_verified_at": charger.last_verified_at,
        "logo_url": charger.logo_url,
        "created_at": charger.created_at,
        "updated_at": charger.updated_at,
        "linked_merchants": linked_merchants,
    }


def _get_linked_merchants(db: Session, charger_id: str) -> List[LinkedMerchantResponse]:
    """Fetch linked merchants for a charger via the junction table."""
    links = (
        db.query(ChargerMerchant, Merchant.name)
        .outerjoin(Merchant, ChargerMerchant.merchant_id == Merchant.id)
        .filter(ChargerMerchant.charger_id == charger_id)
        .order_by(ChargerMerchant.distance_m)
        .all()
    )
    result = []
    for link, merchant_name in links:
        result.append(LinkedMerchantResponse(
            link_id=link.id,
            merchant_id=link.merchant_id,
            merchant_name=merchant_name,
            distance_m=link.distance_m,
            walk_duration_s=link.walk_duration_s,
            walk_distance_m=link.walk_distance_m,
            exclusive_title=link.exclusive_title,
            exclusive_description=link.exclusive_description,
            is_primary=link.is_primary,
            created_at=link.created_at,
        ))
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/v1/admin/chargers")
def list_chargers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by name or address"),
    network: Optional[str] = Query(None, description="Filter by network_name"),
    state: Optional[str] = Query(None, description="Filter by state"),
    city: Optional[str] = Query(None, description="Filter by city"),
    status: Optional[str] = Query(None, description="Filter by status"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List chargers with pagination, search, and filters."""

    # Subquery: merchant count per charger
    merchant_count_sq = (
        db.query(
            ChargerMerchant.charger_id,
            func.count(ChargerMerchant.id).label("merchant_count"),
        )
        .group_by(ChargerMerchant.charger_id)
        .subquery()
    )

    query = (
        db.query(Charger, func.coalesce(merchant_count_sq.c.merchant_count, 0).label("merchant_count"))
        .outerjoin(merchant_count_sq, Charger.id == merchant_count_sq.c.charger_id)
    )

    # Search filter
    if search:
        like_term = f"%{search}%"
        query = query.filter(
            or_(
                Charger.name.ilike(like_term),
                Charger.address.ilike(like_term),
            )
        )

    # Exact filters
    if network:
        query = query.filter(Charger.network_name == network)
    if state:
        query = query.filter(Charger.state == state)
    if city:
        query = query.filter(Charger.city == city)
    if status:
        query = query.filter(Charger.status == status)

    total = query.count()

    rows = (
        query
        .order_by(Charger.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    chargers = []
    for charger, m_count in rows:
        chargers.append(ChargerListItem(
            id=charger.id,
            name=charger.name,
            address=charger.address,
            city=charger.city,
            state=charger.state,
            network_name=charger.network_name,
            power_kw=charger.power_kw,
            num_evse=charger.num_evse,
            status=charger.status,
            pricing_per_kwh=charger.pricing_per_kwh,
            connector_types=charger.connector_types,
            lat=charger.lat,
            lng=charger.lng,
            created_at=charger.created_at,
            merchant_count=m_count,
        ))

    return ChargerListResponse(
        chargers=chargers,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/v1/admin/chargers/{charger_id}")
def get_charger(
    charger_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get charger detail including linked merchants."""
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail="Charger not found")

    linked = _get_linked_merchants(db, charger_id)
    return _charger_to_detail(charger, linked)


@router.post("/v1/admin/chargers", status_code=201)
def create_charger(
    body: ChargerCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new charger."""
    existing = db.query(Charger).filter(Charger.id == body.id).first()
    if existing:
        raise HTTPException(status_code=409, detail="Charger with this ID already exists")

    charger = Charger(
        id=body.id,
        name=body.name,
        lat=body.lat,
        lng=body.lng,
        external_id=body.external_id,
        network_name=body.network_name,
        address=body.address,
        city=body.city,
        state=body.state,
        zip_code=body.zip_code,
        connector_types=body.connector_types or [],
        power_kw=body.power_kw,
        num_evse=body.num_evse,
        is_public=body.is_public,
        access_code=body.access_code,
        pricing_per_kwh=body.pricing_per_kwh,
        pricing_source=body.pricing_source,
        nerava_score=body.nerava_score,
        status=body.status,
        logo_url=body.logo_url,
    )
    db.add(charger)

    try:
        log_admin_action(
            db=db,
            actor_id=admin.id,
            action="charger_created",
            target_type="charger",
            target_id=body.id,
            after_json={"name": body.name, "network": body.network_name, "lat": body.lat, "lng": body.lng},
        )
    except Exception:
        logger.warning("Failed to log admin action for charger creation", exc_info=True)

    db.commit()
    db.refresh(charger)
    logger.info(f"Admin {admin.id} created charger {charger.id}")
    return _charger_to_detail(charger, [])


@router.put("/v1/admin/chargers/{charger_id}")
def update_charger(
    charger_id: str,
    body: ChargerUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update charger fields. Only provided (non-None) fields are updated."""
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail="Charger not found")

    before = {"name": charger.name, "status": charger.status, "network_name": charger.network_name}
    updates = body.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    for field, value in updates.items():
        setattr(charger, field, value)

    charger.updated_at = datetime.utcnow()

    try:
        log_admin_action(
            db=db,
            actor_id=admin.id,
            action="charger_updated",
            target_type="charger",
            target_id=charger_id,
            before_json=before,
            after_json=updates,
        )
    except Exception:
        logger.warning("Failed to log admin action for charger update", exc_info=True)

    db.commit()
    db.refresh(charger)
    linked = _get_linked_merchants(db, charger_id)
    logger.info(f"Admin {admin.id} updated charger {charger_id}: {list(updates.keys())}")
    return _charger_to_detail(charger, linked)


@router.delete("/v1/admin/chargers/{charger_id}")
def delete_charger(
    charger_id: str,
    hard: bool = Query(False, description="Hard delete (permanent). Default is soft delete (status='removed')."),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete a charger.

    By default performs a soft delete (sets status to 'removed').
    Pass ?hard=true for permanent deletion (cascades to charger_merchants links).
    """
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail="Charger not found")

    action = "charger_hard_deleted" if hard else "charger_soft_deleted"
    try:
        log_admin_action(
            db=db,
            actor_id=admin.id,
            action=action,
            target_type="charger",
            target_id=charger_id,
            before_json={"name": charger.name, "status": charger.status},
        )
    except Exception:
        logger.warning("Failed to log admin action for charger deletion", exc_info=True)

    if hard:
        db.delete(charger)
        db.commit()
        logger.info(f"Admin {admin.id} hard-deleted charger {charger_id}")
        return {"detail": "Charger permanently deleted", "charger_id": charger_id}
    else:
        charger.status = "removed"
        charger.updated_at = datetime.utcnow()
        db.commit()
        logger.info(f"Admin {admin.id} soft-deleted charger {charger_id}")
        return {"detail": "Charger marked as removed", "charger_id": charger_id}


@router.post("/v1/admin/chargers/{charger_id}/merchants", status_code=201)
def link_merchant(
    charger_id: str,
    body: LinkMerchantRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Link a merchant to a charger (create ChargerMerchant junction record)."""
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        raise HTTPException(status_code=404, detail="Charger not found")

    merchant = db.query(Merchant).filter(Merchant.id == body.merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    existing = (
        db.query(ChargerMerchant)
        .filter(
            ChargerMerchant.charger_id == charger_id,
            ChargerMerchant.merchant_id == body.merchant_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Merchant is already linked to this charger")

    link = ChargerMerchant(
        charger_id=charger_id,
        merchant_id=body.merchant_id,
        distance_m=body.distance_m,
        walk_duration_s=body.walk_duration_s,
        walk_distance_m=body.walk_distance_m,
        is_primary=body.is_primary,
        override_mode=body.override_mode,
        suppress_others=body.suppress_others,
        exclusive_title=body.exclusive_title,
        exclusive_description=body.exclusive_description,
    )
    db.add(link)

    try:
        log_admin_action(
            db=db,
            actor_id=admin.id,
            action="charger_merchant_linked",
            target_type="charger_merchant",
            target_id=f"{charger_id}:{body.merchant_id}",
            after_json={
                "charger_id": charger_id,
                "merchant_id": body.merchant_id,
                "merchant_name": merchant.name,
                "distance_m": body.distance_m,
            },
        )
    except Exception:
        logger.warning("Failed to log admin action for charger-merchant link", exc_info=True)

    db.commit()
    db.refresh(link)
    logger.info(f"Admin {admin.id} linked merchant {body.merchant_id} to charger {charger_id}")

    return LinkedMerchantResponse(
        link_id=link.id,
        merchant_id=link.merchant_id,
        merchant_name=merchant.name,
        distance_m=link.distance_m,
        walk_duration_s=link.walk_duration_s,
        walk_distance_m=link.walk_distance_m,
        exclusive_title=link.exclusive_title,
        exclusive_description=link.exclusive_description,
        is_primary=link.is_primary,
        created_at=link.created_at,
    )


@router.delete("/v1/admin/chargers/{charger_id}/merchants/{merchant_id}")
def unlink_merchant(
    charger_id: str,
    merchant_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Remove a charger-merchant link."""
    link = (
        db.query(ChargerMerchant)
        .filter(
            ChargerMerchant.charger_id == charger_id,
            ChargerMerchant.merchant_id == merchant_id,
        )
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Charger-merchant link not found")

    try:
        log_admin_action(
            db=db,
            actor_id=admin.id,
            action="charger_merchant_unlinked",
            target_type="charger_merchant",
            target_id=f"{charger_id}:{merchant_id}",
            before_json={
                "charger_id": charger_id,
                "merchant_id": merchant_id,
                "distance_m": link.distance_m,
                "exclusive_title": link.exclusive_title,
            },
        )
    except Exception:
        logger.warning("Failed to log admin action for charger-merchant unlink", exc_info=True)

    db.delete(link)
    db.commit()
    logger.info(f"Admin {admin.id} unlinked merchant {merchant_id} from charger {charger_id}")
    return {"detail": "Charger-merchant link removed", "charger_id": charger_id, "merchant_id": merchant_id}
