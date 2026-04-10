"""
Native App Events Router
Receives session events from iOS native app.
"""
import logging
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.models import User
from app.models.exclusive_session import ExclusiveSession
from app.services.analytics import get_analytics_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/native", tags=["native"])


# ============================================================
# RATE LIMITER
# ============================================================

class InMemoryRateLimiter:
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        if len(self._requests[key]) >= limit:
            return False
        self._requests[key].append(now)
        return True


_rate_limiter = InMemoryRateLimiter()


# ============================================================
# IDEMPOTENCY CACHE WITH TTL (Redis-backed in production)
# ============================================================

class TTLIdempotencyCache:
    """In-memory idempotency cache with TTL-based eviction (fallback when Redis unavailable)."""

    TTL_SECONDS = 3600  # 1 hour

    def __init__(self):
        self._cache: Dict[str, float] = {}  # key -> timestamp

    def check_and_set(self, key: str) -> bool:
        """Returns True if this is a duplicate (already processed)."""
        now = time.time()

        # Evict expired entries periodically
        if len(self._cache) > 1000:
            self._evict_expired(now)

        if key in self._cache:
            # Check if still within TTL
            if now - self._cache[key] < self.TTL_SECONDS:
                return True
            # Expired, allow re-processing

        self._cache[key] = now
        return False

    def _evict_expired(self, now: float):
        """Remove entries older than TTL."""
        cutoff = now - self.TTL_SECONDS
        self._cache = {k: v for k, v in self._cache.items() if v > cutoff}


class RedisIdempotencyCache:
    """Redis-backed idempotency cache for production."""

    TTL_SECONDS = 3600  # 1 hour

    def __init__(self, redis_client, fallback_cache):
        self._redis = redis_client
        self._fallback = fallback_cache

    def check_and_set(self, key: str) -> bool:
        """Returns True if this is a duplicate (already processed)."""
        try:
            # Use SET with NX (only set if not exists) and EX (expiration)
            # Returns True if key was set (new), False if key already exists
            result = self._redis.set(f"idempotency:{key}", "1", nx=True, ex=self.TTL_SECONDS)
            return result is False  # False means key already existed (duplicate)
        except Exception as e:
            logger.warning(f"Redis idempotency check failed, falling back to in-memory: {e}")
            # Fallback to in-memory cache on Redis failure
            return self._fallback.check_and_set(key)


# Initialize Redis client if available
_redis_client = None
_idempotency_cache = None
_fallback_cache = TTLIdempotencyCache()

try:
    if settings.REDIS_ENABLED and settings.REDIS_URL:
        import redis
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        # Test connection
        _redis_client.ping()
        _idempotency_cache = RedisIdempotencyCache(_redis_client, _fallback_cache)
        logger.info("Using Redis for idempotency cache")
    else:
        _idempotency_cache = _fallback_cache
        logger.info("Using in-memory idempotency cache (Redis not configured)")
except Exception as e:
    logger.warning(f"Failed to initialize Redis, using in-memory cache: {e}")
    _idempotency_cache = _fallback_cache


# ============================================================
# MODELS
# ============================================================

class SessionEventRequest(BaseModel):
    schema_version: str = "1.0"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str  # Should equal event_id
    session_id: str
    event: str
    occurred_at: str  # When the event actually happened (client time)
    timestamp: str    # When the request was sent
    source: str = "ios_native"
    app_state: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PreSessionEventRequest(BaseModel):
    schema_version: str = "1.0"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str  # Should equal event_id
    charger_id: Optional[str] = None
    event: str
    occurred_at: str  # When the event actually happened (client time)
    timestamp: str    # When the request was sent
    source: str = "ios_native"
    metadata: Optional[Dict[str, Any]] = None


class SessionEventResponse(BaseModel):
    status: str
    event_id: str


class NativeConfigResponse(BaseModel):
    chargerIntentRadius_m: float
    chargerAnchorRadius_m: float
    chargerDwellSeconds: int
    merchantUnlockRadius_m: float
    gracePeriodSeconds: int
    hardTimeoutSeconds: int
    locationAccuracyThreshold_m: float
    speedThresholdForDwell_mps: float


# ============================================================
# ENDPOINTS
# ============================================================

@router.post("/session-events", response_model=SessionEventResponse)
async def emit_session_event(
    request: SessionEventRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    if not _rate_limiter.check(f"native_events:{driver.id}", limit=60, window_seconds=60):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded"
        )

    if _idempotency_cache.check_and_set(request.idempotency_key):
        logger.debug(f"Duplicate event ignored: {request.idempotency_key}")
        return SessionEventResponse(status="already_processed", event_id=request.event_id)

    session = db.query(ExclusiveSession).filter(
        ExclusiveSession.id == request.session_id,
        ExclusiveSession.driver_id == driver.id
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    analytics = get_analytics_client()
    analytics.capture(
        distinct_id=str(driver.id),
        event=f"native_session_{request.event}",
        properties={
            "session_id": request.session_id,
            "event_id": request.event_id,
            "source": request.source,
            "app_state": request.app_state,
            "occurred_at": request.occurred_at,
            **(request.metadata or {})
        }
    )

    logger.info(f"Native session event: {request.event}", extra={
        "driver_id": driver.id,
        "session_id": request.session_id,
        "event": request.event,
        "occurred_at": request.occurred_at
    })

    return SessionEventResponse(status="ok", event_id=request.event_id)


@router.post("/pre-session-events", response_model=SessionEventResponse)
async def emit_pre_session_event(
    request: PreSessionEventRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    if not _rate_limiter.check(f"native_presession:{driver.id}", limit=30, window_seconds=60):
        return SessionEventResponse(status="throttled", event_id=request.event_id)

    if _idempotency_cache.check_and_set(request.idempotency_key):
        return SessionEventResponse(status="already_processed", event_id=request.event_id)

    analytics = get_analytics_client()
    analytics.capture(
        distinct_id=str(driver.id),
        event=f"native_presession_{request.event}",
        properties={
            "charger_id": request.charger_id,
            "event_id": request.event_id,
            "source": request.source,
            "occurred_at": request.occurred_at,
            **(request.metadata or {})
        }
    )

    logger.info(f"Native pre-session event: {request.event}", extra={
        "driver_id": driver.id,
        "charger_id": request.charger_id,
        "event": request.event,
        "occurred_at": request.occurred_at
    })

    return SessionEventResponse(status="ok", event_id=request.event_id)


@router.get("/config", response_model=NativeConfigResponse)
async def get_native_config(driver: User = Depends(get_current_driver)):
    """Get remote configuration. Reads from settings.NATIVE_* environment variables."""
    return NativeConfigResponse(
        chargerIntentRadius_m=settings.NATIVE_CHARGER_INTENT_RADIUS_M,
        chargerAnchorRadius_m=settings.NATIVE_CHARGER_ANCHOR_RADIUS_M,
        chargerDwellSeconds=settings.NATIVE_CHARGER_DWELL_SECONDS,
        merchantUnlockRadius_m=settings.NATIVE_MERCHANT_UNLOCK_RADIUS_M,
        gracePeriodSeconds=settings.NATIVE_GRACE_PERIOD_SECONDS,
        hardTimeoutSeconds=settings.NATIVE_HARD_TIMEOUT_SECONDS,
        locationAccuracyThreshold_m=settings.NATIVE_LOCATION_ACCURACY_THRESHOLD_M,
        speedThresholdForDwell_mps=settings.NATIVE_SPEED_THRESHOLD_FOR_DWELL_MPS
    )

