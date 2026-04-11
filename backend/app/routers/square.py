import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies.feature_flags import require_square

router = APIRouter(prefix="/v1/square", tags=["square"])

@router.post("/checkout", dependencies=[Depends(require_square)])
async def create_checkout(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create Square checkout link (demo version).
    
    In production, this would integrate with Square's API to create a real payment link.
    For now, this returns a mock success response.
    """
    merchant_id = request_data.get('merchantId')
    amount_cents = request_data.get('amountCents')
    
    if not merchant_id or not amount_cents:
        raise HTTPException(status_code=400, detail="merchantId and amountCents required")
    
    # Generate a mock payment ID
    payment_id = str(uuid.uuid4())
    
    # In production, this would create a real Square checkout link
    # For now, return a mock success response
    return {
        'url': f'https://squareup.com/checkout/demo?payment_id={payment_id}',
        'paymentId': payment_id,
        'note': 'This is a demo payment link. No actual payment will be processed.'
    }
