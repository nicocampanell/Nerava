"""
Wallet Pass Router
Handles POST /v1/wallet/pass/activate endpoint
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.wallet import WalletActivateRequest, WalletActivateResponse
from app.services.wallet_activate import activate_wallet_pass

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/wallet", tags=["wallet"])


@router.post(
    "/pass/activate",
    response_model=WalletActivateResponse,
    summary="Activate wallet pass",
    description="""
    Activate a wallet pass for a merchant tied to an intent session.
    
    Creates or updates a wallet pass state record with:
    - session_id: Intent session ID
    - merchant_id: Merchant ID
    - state: ACTIVE
    - expires_at: Current time + 60 minutes
    
    Returns wallet state with expiry information.
    """
)
async def activate_wallet_pass_endpoint(
    request: WalletActivateRequest,
    db: Session = Depends(get_db),
):
    """
    Activate a wallet pass for a given session and merchant.
    """
    try:
        result = activate_wallet_pass(db, request.session_id, request.merchant_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating wallet pass: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to activate wallet pass")
