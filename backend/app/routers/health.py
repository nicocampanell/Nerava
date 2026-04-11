from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.db import get_engine

router = APIRouter()

@router.get("/health")
def health():
    """
    Basic health check endpoint.

    Returns:
        {
            "status": "ok",
            "db": "ok"
        }
    """
    try:
        # Test database connection with a trivial query
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        return {
            "status": "ok",
            "db": "ok"
        }
    except Exception:
        # Return 500 on database failure
        raise HTTPException(status_code=500, detail="Database connection failed")

@router.get("/healthz")
async def healthz():
    """
    DEPRECATED: This endpoint performs database checks which can block health checks.
    
    Use root-level /healthz (liveness) for App Runner health checks instead.
    This endpoint is kept for backward compatibility but may be removed.
    
    For dependency checks, use root-level /readyz (readiness probe).
    """
    # Return simple response without DB check to avoid blocking
    # This matches the root-level /healthz behavior
    return {
        "ok": True,
        "database": "not_checked",  # DB check removed to prevent blocking
        "time": datetime.utcnow().isoformat(),
        "deprecated": True,
        "message": "Use root-level /healthz for liveness, /readyz for readiness"
    }
