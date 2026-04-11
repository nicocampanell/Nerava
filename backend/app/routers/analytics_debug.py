"""
Debug endpoint for testing PostHog events.
Only enabled in non-production environments.
"""
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.analytics import get_analytics_client

router = APIRouter(prefix="/debug/analytics", tags=["Debug - Analytics"])


class PostHogTestEvent(BaseModel):
    event: str
    distinct_id: Optional[str] = "test-user"
    properties: Optional[Dict[str, Any]] = None

    class Config:
        json_schema_extra = {
            "example": {
                "event": "test_event",
                "distinct_id": "user-123",
                "properties": {
                    "button": "merchant_card",
                    "merchant_id": "merch-456"
                }
            }
        }


@router.post("/posthog/test", summary="Fire a test PostHog event")
async def fire_test_posthog_event(payload: PostHogTestEvent):
    """
    Fire a test event to PostHog.

    **Only available in dev/staging environments.**

    Use this to verify PostHog integration is working.
    Check your PostHog dashboard for the event after calling.
    """
    env = os.getenv("ENV", "dev")
    if env == "prod":
        raise HTTPException(
            status_code=403,
            detail="Debug endpoints disabled in production"
        )

    analytics = get_analytics_client()
    if not analytics.enabled:
        raise HTTPException(
            status_code=400,
            detail="PostHog not configured (POSTHOG_KEY missing or ANALYTICS_ENABLED=false)"
        )

    analytics.capture(
        event=payload.event,
        distinct_id=payload.distinct_id or "test-user",
        properties={
            **(payload.properties or {}),
            "is_test": True,
        }
    )

    return {
        "ok": True,
        "message": f"Event '{payload.event}' sent to PostHog",
        "distinct_id": payload.distinct_id,
        "note": "Check PostHog dashboard in ~30 seconds"
    }


@router.get("/posthog/status", summary="Check PostHog configuration")
async def check_posthog_status():
    """Check if PostHog is configured and return safe config info."""
    analytics = get_analytics_client()
    posthog_key = os.getenv("POSTHOG_KEY") or os.getenv("POSTHOG_API_KEY", "")

    return {
        "configured": analytics.enabled,
        "host": analytics.posthog_host,
        "env": analytics.env,
        "key_prefix": posthog_key[:8] + "..." if posthog_key else None,
    }
