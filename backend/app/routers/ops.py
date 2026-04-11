import os

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.config import settings
from app.core.env import is_production_env
from app.db import get_db

router = APIRouter()

# NOTE: /healthz and /readyz are now defined at root level in main_simple.py
# These endpoints are kept for backward compatibility but may be removed in future versions.
# Use root-level /healthz (liveness) and /readyz (readiness) instead.

@router.get("/readyz")
async def readiness_check(db = Depends(get_db)):
    """Readiness check - verifies dependencies"""
    try:
        # Check database
        db.execute(text("SELECT 1"))
        
        # Check Redis
        redis_client = redis.from_url(settings.redis_url)
        redis_client.ping()
        
        return {"ok": True, "status": "ready"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service not ready: {str(e)}")

@router.get("/metrics")
async def metrics(request: Request):
    """
    Prometheus metrics endpoint (protected).
    
    Access control:
    - Enabled by default in production (METRICS_ENABLED=true)
    - Disabled by default in local/dev (METRICS_ENABLED=false)
    - Optional token-based auth via METRICS_TOKEN env var
    """
    from fastapi import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    
    # Check if metrics are enabled
    metrics_enabled = os.getenv("METRICS_ENABLED", "true" if is_production_env() else "false").lower() == "true"
    
    if not metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics endpoint is disabled"
        )
    
    # Optional token-based auth
    metrics_token = os.getenv("METRICS_TOKEN")
    if metrics_token:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or auth_header[7:] != metrics_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing metrics token"
            )
    
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
