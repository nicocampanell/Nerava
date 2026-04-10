"""
Admin Partner Management Router — JWT-authenticated admin endpoints.

Allows admins to register partners, manage API keys, and view partner details.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.dependencies_domain import require_admin
from app.models.user import User
from app.schemas.partner import (
    PartnerAPIKeyCreateRequest,
    PartnerAPIKeyCreateResponse,
    PartnerCreateRequest,
    PartnerResponse,
    PartnerUpdateRequest,
)
from app.services.partner_service import PartnerService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/admin/partners", tags=["admin-partners"])


def _partner_to_response(p) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "partner_type": p.partner_type,
        "trust_tier": p.trust_tier,
        "status": p.status,
        "contact_name": p.contact_name,
        "contact_email": p.contact_email,
        "webhook_url": p.webhook_url,
        "webhook_enabled": p.webhook_enabled,
        "rate_limit_rpm": p.rate_limit_rpm,
        "quality_score_modifier": p.quality_score_modifier,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.post("/", response_model=PartnerResponse)
def create_partner(
    req: PartnerCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Register a new partner."""
    existing = PartnerService.get_partner_by_slug(db, req.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Partner slug '{req.slug}' already exists")

    partner = PartnerService.create_partner(
        db,
        name=req.name,
        slug=req.slug,
        partner_type=req.partner_type,
        trust_tier=req.trust_tier,
        contact_name=req.contact_name,
        contact_email=req.contact_email,
        webhook_url=req.webhook_url,
        rate_limit_rpm=req.rate_limit_rpm,
    )
    return _partner_to_response(partner)


@router.get("/")
def list_partners(
    status: str = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all partners, optionally filtered by status."""
    partners = PartnerService.list_partners(db, status=status)
    return [_partner_to_response(p) for p in partners]


@router.get("/{partner_id}", response_model=PartnerResponse)
def get_partner(
    partner_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get a partner by ID."""
    partner = PartnerService.get_partner(db, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return _partner_to_response(partner)


@router.patch("/{partner_id}", response_model=PartnerResponse)
def update_partner(
    partner_id: str,
    req: PartnerUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a partner's details."""
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    partner = PartnerService.update_partner(db, partner_id, **updates)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return _partner_to_response(partner)


@router.post("/{partner_id}/keys", response_model=PartnerAPIKeyCreateResponse)
def create_api_key(
    partner_id: str,
    req: PartnerAPIKeyCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create an API key for a partner. Returns the plaintext key once."""
    partner = PartnerService.get_partner(db, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    api_key, plaintext = PartnerService.create_api_key(
        db,
        partner_id=partner_id,
        name=req.name,
        scopes=req.scopes,
    )
    return {
        "id": api_key.id,
        "key_prefix": api_key.key_prefix,
        "plaintext_key": plaintext,
        "name": api_key.name,
        "scopes": api_key.scopes,
        "created_at": api_key.created_at.isoformat(),
    }


@router.get("/{partner_id}/keys")
def list_api_keys(
    partner_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all API keys for a partner (without plaintext)."""
    keys = PartnerService.list_api_keys(db, partner_id)
    return [
        {
            "id": k.id,
            "key_prefix": k.key_prefix,
            "name": k.name,
            "scopes": k.scopes,
            "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat(),
        }
        for k in keys
    ]


@router.delete("/{partner_id}/keys/{key_id}")
def revoke_api_key(
    partner_id: str,
    key_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Revoke an API key."""
    success = PartnerService.revoke_api_key(db, key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"ok": True}
