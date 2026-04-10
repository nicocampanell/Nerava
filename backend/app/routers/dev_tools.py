"""
Dev-only endpoints for testing (gated behind APP_ENV=dev)
"""
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/dev", tags=["dev"])

logger = get_logger(__name__)


class MockPurchaseRequest(BaseModel):
    provider: str  # "square" | "clo"
    user_id: int
    merchant_name: str
    merchant_ext_id: str
    city: Optional[str] = None
    amount_cents: int
    ts: Optional[str] = None  # ISO timestamp, defaults to now


@router.post("/mock_purchase")
async def mock_purchase_webhook(
    request: MockPurchaseRequest,
    db: Session = Depends(get_db)
):
    """
    Dev-only: Mock a purchase webhook by transforming body and forwarding to /v1/webhooks/purchase.
    
    Only available when APP_ENV=dev
    """
    # Guard: only allow in dev
    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).lower()
    if app_env == "prod" or app_env == "production":
        raise HTTPException(status_code=403, detail="Dev endpoints disabled in production")
    
    # Transform to provider-specific format
    if request.provider == "square":
        # Square-like webhook format
        webhook_payload = {
            "type": "payment.updated",
            "event_time": request.ts or datetime.utcnow().isoformat() + "Z",
            "data": {
                "object": {
                    "object": "payment",
                    "id": f"sq_payment_{int(datetime.utcnow().timestamp())}",
                    "amount_money": {
                        "amount": request.amount_cents,
                        "currency": "USD"
                    },
                    "location_id": request.merchant_ext_id,
                    "location": {
                        "id": request.merchant_ext_id,
                        "name": request.merchant_name
                    },
                    "created_at": request.ts or datetime.utcnow().isoformat() + "Z",
                    "metadata": {
                        "user_id": str(request.user_id)
                    }
                }
            },
            # Also include direct user_id for normalization fallback
            "user_id": request.user_id
        }
    else:
        # CLO/Generic format
        webhook_payload = {
            "provider": request.provider,
            "event_type": "purchase",
            "transaction_id": f"{request.provider}_tx_{int(datetime.utcnow().timestamp())}",
            "user_id": request.user_id,
            "merchant_ext_id": request.merchant_ext_id,
            "merchant_name": request.merchant_name,
            "amount_cents": request.amount_cents,
            "city": request.city,
            "ts": request.ts or datetime.utcnow().isoformat() + "Z"
        }
    
    # Forward to webhook endpoint by calling the handler function directly


    from app.routers.purchase_webhooks import ingest_purchase_webhook
    
    # Create a mock request object
    class MockRequest:
        def __init__(self, json_data):
            self._json = json_data
        
        async def json(self):
            return self._json
    
    mock_request = MockRequest(webhook_payload)
    
    # Call the handler directly
    try:
        result = await ingest_purchase_webhook(
            request=mock_request,
            x_webhook_secret=None,
            db=db
        )
        return result
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Webhook forwarding failed: {e}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")

