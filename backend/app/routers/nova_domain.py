"""
Domain Charge Party MVP Nova Router
Nova grant endpoint for charging session verification
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import require_admin
from app.services.nova_service import NovaService

router = APIRouter(prefix="/v1/nova", tags=["nova"])


class GrantNovaRequest(BaseModel):
    driver_user_id: int
    charging_session_id: str
    amount: int


class GrantNovaResponse(BaseModel):
    transaction_id: str
    driver_balance: int


@router.post("/grant", response_model=GrantNovaResponse)
def grant_nova_to_driver(
    request: GrantNovaRequest,
    admin = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Grant Nova to driver after charging session verification.
    Admin-only endpoint for MVP (can be automated later).
    """
    try:
        # Get event_id from session if available
        from app.models_domain import DomainChargingSession
        session = db.query(DomainChargingSession).filter(
            DomainChargingSession.id == request.charging_session_id
        ).first()
        event_id = session.event_id if session else None
        
        transaction = NovaService.grant_to_driver(
            db=db,
            driver_id=request.driver_user_id,
            amount=request.amount,
            type="driver_earn",
            session_id=request.charging_session_id,
            event_id=event_id,
            metadata={"granted_by": admin.id, "reason": "charging_session_verification"}
        )
        
        # Mark session as verified (separate commit after NovaService already committed)
        if session:
            session.verified = True
            session.verification_source = "admin"
            db.commit()
        
        wallet = NovaService.get_driver_wallet(db, request.driver_user_id)
        
        return GrantNovaResponse(
            transaction_id=transaction.id,
            driver_balance=wallet.nova_balance
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

