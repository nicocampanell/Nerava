"""
Perks Router
Handles perk unlock endpoint
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.schemas.perks import (
    PerkUnlockRequest,
    PerkUnlockResponse,
)
from app.services.perk_service import unlock_perk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/perks", tags=["perks"])


@router.post(
    "/unlock",
    response_model=PerkUnlockResponse,
    summary="Unlock a merchant perk",
    description="""
    Unlock a perk for the current user.
    
    Supports two unlock methods:
    - "dwell_time": User has been at merchant for threshold time
    - "user_confirmation": User confirms they walked to merchant
    
    Updates mocked wallet pass state machine: IDLE → CHARGING_MOMENT → PERK_UNLOCKED
    """
)
async def unlock_perk_endpoint(
    request: PerkUnlockRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Unlock a perk for the current user.
    
    Supports two unlock methods:
    - "dwell_time": User has been at merchant for threshold time
    - "user_confirmation": User confirms they walked to merchant
    """
    try:
        # Validate unlock method
        if request.unlock_method not in ["dwell_time", "user_confirmation"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid unlock_method. Must be 'dwell_time' or 'user_confirmation'",
            )
        
        unlock = unlock_perk(
            db=db,
            user_id=current_user.id,
            perk_id=request.perk_id,
            unlock_method=request.unlock_method,
            intent_session_id=request.intent_session_id,
            merchant_id=request.merchant_id,
            dwell_time_seconds=request.dwell_time_seconds,
        )
        
        from app.core.copy import PERK_UNLOCK_COPY
        
        return PerkUnlockResponse(
            unlock_id=unlock.id,
            perk_id=unlock.perk_id,
            unlocked_at=unlock.unlocked_at.isoformat(),
            message=PERK_UNLOCK_COPY["success"],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error unlocking perk: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unlock perk",
        )

