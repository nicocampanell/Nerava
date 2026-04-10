"""
Tesla Fleet Telemetry Webhook — Receives real-time vehicle telemetry.

POST /v1/webhooks/tesla/telemetry

Fleet Telemetry server dispatches HTTP POST events when vehicle fields change.
This endpoint processes them into charging session lifecycle events.
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.config import settings
from ..db import get_db
from ..schemas.telemetry import TelemetryPayload
from ..services.telemetry_processor import TelemetryProcessor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks/tesla", tags=["tesla-telemetry"])


def _verify_hmac_signature(request_body: bytes, signature: str) -> bool:
    """Verify HMAC-SHA256 signature from Fleet Telemetry server."""
    secret = settings.TESLA_TELEMETRY_HMAC_SECRET
    if not secret:
        return True  # No secret configured — skip validation

    expected = hmac.new(
        secret.encode(), request_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/telemetry")
async def receive_telemetry(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Receive telemetry events from Tesla Fleet Telemetry server.

    The Fleet Telemetry server dispatches vehicle data changes via HTTP POST.
    This endpoint is NOT authenticated via JWT — it uses HMAC signature
    verification instead (server-to-server).
    """
    # Check feature flag
    if not settings.TELEMETRY_WEBHOOK_ENABLED:
        raise HTTPException(status_code=503, detail="Telemetry webhook disabled")

    # Read raw body for HMAC validation
    body = await request.body()

    # Validate HMAC signature if configured
    signature = request.headers.get("X-Telemetry-Signature", "")
    if settings.TESLA_TELEMETRY_HMAC_SECRET and not _verify_hmac_signature(body, signature):
        logger.warning("Invalid telemetry HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse payload
    try:
        import json
        payload_dict = json.loads(body)
        payload = TelemetryPayload(**payload_dict)
    except Exception as e:
        logger.warning("Invalid telemetry payload: %s", e)
        raise HTTPException(status_code=422, detail="Invalid payload")

    # Process telemetry
    try:
        result = TelemetryProcessor.process_telemetry(
            db,
            vin=payload.vin,
            telemetry_data=[v.model_dump() for v in payload.data],
            created_at=payload.created_at,
        )
    except Exception as e:
        logger.error("Telemetry processing error for VIN %s: %s", payload.vin, e)
        # Return 200 to prevent Fleet Telemetry from retrying on our bugs
        return {"status": "error", "detail": str(e)}

    return {"status": "processed", "result": result}
