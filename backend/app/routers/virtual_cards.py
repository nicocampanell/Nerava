"""
Virtual Cards Router

Endpoints for virtual card generation and management.
Feature-flagged with VIRTUAL_CARD_ENABLED.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.config import flag_enabled
from ..db import get_db
from ..dependencies_domain import get_current_user
from ..models import User

router = APIRouter(prefix="/v1/virtual_cards", tags=["virtual-cards"])
logger = logging.getLogger(__name__)


@router.post("/create")
async def create_virtual_card(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Create a virtual card for the current user.
    
    Returns a mocked virtual card object if feature is enabled.
    If disabled, returns 501.
    """
    # Check feature flag
    if not flag_enabled("feature_virtual_card"):
        raise HTTPException(
            status_code=501,
            detail={
                "error": "VIRTUAL_CARD_DISABLED",
                "message": "Virtual card generation is not enabled"
            }
        )
    
    try:
        # TODO: Implement real virtual card generation
        # For now, return a mocked card object
        card_id = f"vc_{uuid.uuid4().hex[:12]}"
        
        # Mock card data
        card = {
            "card_id": card_id,
            "status": "active",
            "brand": "VISA",
            "last4": "4242",
            "exp_month": 12,
            "exp_year": 2028,
            "created_at": datetime.utcnow().isoformat() + "Z"
        }
        
        logger.info(f"Created virtual card {card_id} for user {current_user.id}")
        
        return card
        
    except Exception as e:
        logger.error(f"Failed to create virtual card: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "VIRTUAL_CARD_CREATION_FAILED",
                "message": "Failed to create virtual card"
            }
        )


@router.get("/me")
async def get_my_virtual_card(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """
    Get the current user's virtual card if it exists.
    
    Returns null if no card exists.
    """
    # Check feature flag
    if not flag_enabled("feature_virtual_card"):
        # Return null instead of 501 for GET requests (allows UI to show "no card" state)
        return None
    
    try:
        # TODO: Query database for existing card
        # For now, return null (no card exists)
        # In a real implementation, you would query:
        # card = db.query(VirtualCard).filter(VirtualCard.user_id == current_user.id).first()
        # if card:
        #     return {...}
        
        return None
        
    except Exception as e:
        logger.error(f"Failed to get virtual card: {e}", exc_info=True)
        # Return null on error (allows UI to handle gracefully)
        return None

