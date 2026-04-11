"""
Consent management router for GDPR compliance
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User, UserConsent

router = APIRouter(prefix="/v1/consent", tags=["consent"])


class ConsentStatus(BaseModel):
    consent_type: str
    granted: bool
    granted_at: Optional[str] = None
    revoked_at: Optional[str] = None


class ConsentResponse(BaseModel):
    consents: List[ConsentStatus]


class GrantConsentResponse(BaseModel):
    ok: bool
    consent_type: str
    granted_at: str


class RevokeConsentResponse(BaseModel):
    ok: bool
    consent_type: str
    revoked_at: str


@router.get("", response_model=ConsentResponse)
def get_consent_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get user's consent status for all consent types"""
    consents = db.query(UserConsent).filter(
        UserConsent.user_id == current_user.id
    ).all()
    
    # Build response with all known consent types
    consent_map = {c.consent_type: c for c in consents}
    consent_types = ["analytics", "marketing"]
    
    result = []
    for consent_type in consent_types:
        consent = consent_map.get(consent_type)
        if consent and consent.is_granted():
            result.append(ConsentStatus(
                consent_type=consent_type,
                granted=True,
                granted_at=consent.granted_at.isoformat() if consent.granted_at else None,
                revoked_at=consent.revoked_at.isoformat() if consent.revoked_at else None,
            ))
        else:
            result.append(ConsentStatus(
                consent_type=consent_type,
                granted=False,
                granted_at=None,
                revoked_at=consent.revoked_at.isoformat() if consent and consent.revoked_at else None,
            ))
    
    return ConsentResponse(consents=result)


@router.post("/{consent_type}/grant", response_model=GrantConsentResponse)
def grant_consent(
    consent_type: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Grant consent for a specific consent type"""
    if consent_type not in ["analytics", "marketing"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid consent_type: {consent_type}. Must be 'analytics' or 'marketing'"
        )
    
    now = datetime.now(timezone.utc)
    
    # Get client IP address
    client_ip = request.client.host if request.client else None
    # Check X-Forwarded-For header for proxied requests
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    
    # Find existing consent or create new
    consent = db.query(UserConsent).filter(
        UserConsent.user_id == current_user.id,
        UserConsent.consent_type == consent_type
    ).first()
    
    if consent:
        # Update existing consent
        consent.granted_at = now
        consent.revoked_at = None
        consent.ip_address = client_ip
        consent.updated_at = now
    else:
        # Create new consent
        consent = UserConsent(
            user_id=current_user.id,
            consent_type=consent_type,
            granted_at=now,
            revoked_at=None,
            ip_address=client_ip,
            privacy_policy_version=settings.PRIVACY_POLICY_VERSION
        )
        db.add(consent)
    
    db.commit()
    db.refresh(consent)
    
    return GrantConsentResponse(
        ok=True,
        consent_type=consent_type,
        granted_at=consent.granted_at.isoformat()
    )


@router.post("/{consent_type}/revoke", response_model=RevokeConsentResponse)
def revoke_consent(
    consent_type: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke consent for a specific consent type"""
    if consent_type not in ["analytics", "marketing"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid consent_type: {consent_type}. Must be 'analytics' or 'marketing'"
        )
    
    now = datetime.now(timezone.utc)
    
    # Get client IP address
    client_ip = request.client.host if request.client else None
    # Check X-Forwarded-For header for proxied requests
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    
    # Find existing consent or create new
    consent = db.query(UserConsent).filter(
        UserConsent.user_id == current_user.id,
        UserConsent.consent_type == consent_type
    ).first()
    
    if consent:
        # Update existing consent
        consent.revoked_at = now
        consent.ip_address = client_ip
        consent.updated_at = now
    else:
        # Create new consent record (for audit trail)
        consent = UserConsent(
            user_id=current_user.id,
            consent_type=consent_type,
            granted_at=None,
            revoked_at=now,
            ip_address=client_ip,
            privacy_policy_version=settings.PRIVACY_POLICY_VERSION
        )
        db.add(consent)
    
    db.commit()
    db.refresh(consent)
    
    return RevokeConsentResponse(
        ok=True,
        consent_type=consent_type,
        revoked_at=consent.revoked_at.isoformat() if consent.revoked_at else None
    )
