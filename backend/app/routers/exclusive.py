"""
Exclusive Session Router
Handles POST /v1/exclusive/activate, POST /v1/exclusive/complete, GET /v1/exclusive/active
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.env import is_local_env
from app.db import get_db
from app.dependencies.domain import get_current_user
from app.dependencies.driver import get_current_driver, get_current_driver_optional
from app.models import User
from app.models.domain import DomainMerchant
from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus
from app.models.session_event import SessionEvent
from app.models.verified_visit import VerifiedVisit
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.analytics import get_analytics_client
from app.services.geo import haversine_m
from app.services.hubspot import get_hubspot_client
from app.utils.exclusive_logging import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/exclusive", tags=["exclusive"])

# Constants
CHARGER_RADIUS_M = settings.CHARGER_RADIUS_M
EXCLUSIVE_DURATION_MIN = settings.EXCLUSIVE_DURATION_MIN


def _verify_merchant_ownership(db: Session, user: User, wyc_merchant_id: str) -> None:
    """
    Verify that a merchant-role user owns the WYC merchant being accessed.
    Admin users bypass this check. Raises 403 if ownership cannot be verified.
    """
    user_roles = (user.role_flags or "").split(",")
    if "admin" in user_roles:
        return  # Admins can access any merchant

    # Look up the WYC merchant to get its external_id (Google Place ID)
    wyc_merchant = db.query(Merchant).filter(Merchant.id == wyc_merchant_id).first()
    if not wyc_merchant:
        return  # Let the caller handle 404

    # Check if user owns a DomainMerchant linked to this WYC merchant
    ownership_query = db.query(DomainMerchant).filter(
        DomainMerchant.owner_user_id == user.id,
    )
    # Match by Google Place ID or by name
    domain_merchant = (
        ownership_query.filter(DomainMerchant.google_place_id == wyc_merchant.external_id).first()
        if wyc_merchant.external_id
        else None
    )

    if not domain_merchant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this merchant",
        )


# Request/Response Models
class ActivateExclusiveRequest(BaseModel):
    merchant_id: Optional[str] = None
    merchant_place_id: Optional[str] = None
    charger_id: str
    charger_place_id: Optional[str] = None
    intent_session_id: Optional[str] = None
    lat: float  # Required: driver must provide location for radius validation
    lng: float  # Required: driver must provide location for radius validation
    accuracy_m: Optional[float] = None
    # V3: Intent capture fields
    intent: Optional[str] = None  # "eat" | "work" | "quick-stop"
    party_size: Optional[int] = None
    needs_power_outlet: Optional[bool] = None
    is_to_go: Optional[bool] = None

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, v):
        if v is not None and v not in ("eat", "work", "quick-stop"):
            raise ValueError("intent must be one of: eat, work, quick-stop")
        return v


class ExclusiveSessionResponse(BaseModel):
    id: str
    merchant_id: Optional[str]
    charger_id: Optional[str]
    expires_at: str
    activated_at: str
    remaining_seconds: int
    # Enriched fields for claim card / claim details
    merchant_name: Optional[str] = None
    merchant_place_id: Optional[str] = None
    exclusive_title: Optional[str] = None
    merchant_lat: Optional[float] = None
    merchant_lng: Optional[float] = None
    merchant_distance_m: Optional[float] = None
    merchant_walk_time_min: Optional[int] = None
    merchant_category: Optional[str] = None
    merchant_photo_url: Optional[str] = None
    charger_name: Optional[str] = None
    verification_code: Optional[str] = None
    charging_active: Optional[bool] = None
    charging_session_ended_at: Optional[str] = None


class ActivateExclusiveResponse(BaseModel):
    status: str
    exclusive_session: ExclusiveSessionResponse
    idempotent: Optional[bool] = None


class CompleteExclusiveRequest(BaseModel):
    exclusive_session_id: str
    feedback: Optional[dict] = None  # thumbs_up: bool, tags: List[str]


class CompleteExclusiveResponse(BaseModel):
    status: str
    idempotent: Optional[bool] = None
    nova_earned: Optional[float] = None


class ActiveExclusiveResponse(BaseModel):
    exclusive_session: Optional[ExclusiveSessionResponse] = None


class VerifyVisitRequest(BaseModel):
    exclusive_session_id: str
    lat: Optional[float] = None
    lng: Optional[float] = None


class VerifyVisitResponse(BaseModel):
    status: str
    verification_code: str
    visit_number: int
    merchant_name: str
    verified_at: str


def generate_session_id() -> str:
    """Generate a UUID string for session ID"""
    return str(uuid.uuid4())


def _compute_effective_expiry(
    db: Session,
    session: ExclusiveSession,
) -> datetime:
    """
    Compute effective expiry: max(original expires_at, charging_session_end + 60min).
    If charging session is still active, keep original expiry (will be extended on next check).
    """
    base_expiry = session.expires_at
    if session.charging_session_id:
        cs = db.query(SessionEvent).filter(SessionEvent.id == session.charging_session_id).first()
        if cs and cs.session_end:
            post_charge_expiry = cs.session_end + timedelta(minutes=EXCLUSIVE_DURATION_MIN)
            if post_charge_expiry > base_expiry:
                # Update in DB so the extended expiry persists
                session.expires_at = post_charge_expiry
                session.updated_at = datetime.now(timezone.utc)
                db.flush()
                return post_charge_expiry
    return base_expiry


def _enrich_session_response(
    db: Session,
    session: ExclusiveSession,
    remaining_seconds: int,
) -> ExclusiveSessionResponse:
    """Build an enriched ExclusiveSessionResponse with merchant/charger details."""
    merchant_name = None
    merchant_place_id = session.merchant_place_id
    exclusive_title = None
    merchant_lat = None
    merchant_lng = None
    merchant_distance_m = session.activation_distance_to_charger_m
    merchant_walk_time_min = None
    merchant_category = None
    merchant_photo_url = None
    charger_name = None

    # Enrich merchant data
    if session.merchant_id:
        wyc_merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
        if wyc_merchant:
            merchant_name = wyc_merchant.name
            merchant_lat = wyc_merchant.lat
            merchant_lng = wyc_merchant.lng
            merchant_category = wyc_merchant.category
            # Photo fallback: primary_photo_url > photo_urls[0] > photo_url
            merchant_photo_url = (
                getattr(wyc_merchant, "primary_photo_url", None)
                or (getattr(wyc_merchant, "photo_urls", None) or [None])[0]
                or getattr(wyc_merchant, "photo_url", None)
            )
            if not merchant_place_id:
                merchant_place_id = getattr(wyc_merchant, "place_id", None)

        # Get exclusive title from charger-merchant link
        if session.charger_id:
            cm_link = (
                db.query(ChargerMerchant)
                .filter(
                    ChargerMerchant.charger_id == session.charger_id,
                    ChargerMerchant.merchant_id == session.merchant_id,
                )
                .first()
            )
            if cm_link:
                exclusive_title = cm_link.exclusive_title
                if cm_link.distance_m is not None:
                    merchant_distance_m = cm_link.distance_m
                if cm_link.walk_duration_s is not None:
                    merchant_walk_time_min = max(1, round(cm_link.walk_duration_s / 60))

    # Enrich charger data
    if session.charger_id:
        charger = db.query(Charger).filter(Charger.id == session.charger_id).first()
        if charger:
            charger_name = charger.name

    # Check charging session status for dynamic expiry
    charging_active = None
    charging_session_ended_at = None
    if session.charging_session_id:
        cs = db.query(SessionEvent).filter(SessionEvent.id == session.charging_session_id).first()
        if cs:
            if cs.session_end:
                charging_active = False
                charging_session_ended_at = cs.session_end.isoformat()
            else:
                charging_active = True

    return ExclusiveSessionResponse(
        id=str(session.id),
        merchant_id=session.merchant_id,
        charger_id=session.charger_id,
        expires_at=session.expires_at.isoformat(),
        activated_at=session.activated_at.isoformat(),
        remaining_seconds=max(0, remaining_seconds),
        merchant_name=merchant_name,
        merchant_place_id=merchant_place_id,
        exclusive_title=exclusive_title,
        merchant_lat=merchant_lat,
        merchant_lng=merchant_lng,
        merchant_distance_m=merchant_distance_m,
        merchant_walk_time_min=merchant_walk_time_min,
        merchant_category=merchant_category,
        merchant_photo_url=merchant_photo_url,
        charger_name=charger_name,
        verification_code=session.verification_code,
        charging_active=charging_active,
        charging_session_ended_at=charging_session_ended_at,
    )


def validate_charger_radius(db: Session, charger_id: str, lat: float, lng: float) -> Tuple:
    """
    Validate that activation location is within charger radius.

    Returns:
        tuple: (distance_m, is_within_radius)
    """
    charger = db.query(Charger).filter(Charger.id == charger_id).first()
    if not charger:
        # Log available chargers for debugging
        all_chargers = db.query(Charger).limit(10).all()
        charger_ids = [c.id for c in all_chargers]
        logger.warning(
            f"Charger {charger_id} not found. Available chargers: {charger_ids}",
            extra={"requested_charger_id": charger_id, "available_charger_ids": charger_ids},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "charger_not_found",
                "message": f"Charger not found: {charger_id}. Please run bootstrap endpoint first.",
                "available_chargers": charger_ids[:5],  # Show first 5 for debugging
            },
        )

    distance_m = haversine_m(lat, lng, charger.lat, charger.lng)
    is_within_radius = distance_m <= CHARGER_RADIUS_M

    return distance_m, is_within_radius


@router.post("/activate", response_model=ActivateExclusiveResponse)
async def activate_exclusive(
    request: ActivateExclusiveRequest,
    http_request: Request,
    driver: User = Depends(get_current_driver),  # Required auth - OTP must be verified first
    db: Session = Depends(get_db),
):
    """
    Activate an exclusive session for a driver at a merchant/charger.

    Requires driver authentication (OTP must be verified first).

    Returns 401 if not authenticated, 428 if OTP not verified, 400 for invalid inputs, 500 for unexpected errors (with logging).
    """
    try:
        # Verify authentication: user must have a verified auth provider
        if not driver.auth_provider or driver.auth_provider == "anonymous":
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail="OTP_REQUIRED"
            )

        # Idempotency check — idempotency_key has a global unique constraint
        idempotency_key = http_request.headers.get("X-Idempotency-Key")
        if idempotency_key:
            existing_session = (
                db.query(ExclusiveSession)
                .filter(
                    ExclusiveSession.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing_session:
                # Guard against idempotency key collision across drivers
                if existing_session.driver_id != driver.id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency key already used by another driver",
                    )
                remaining_seconds = max(
                    0,
                    int((existing_session.expires_at - datetime.now(timezone.utc)).total_seconds()),
                )
                return ActivateExclusiveResponse(
                    status=existing_session.status.value,
                    exclusive_session=ExclusiveSessionResponse(
                        id=str(existing_session.id),
                        merchant_id=existing_session.merchant_id,
                        charger_id=existing_session.charger_id,
                        expires_at=existing_session.expires_at.isoformat(),
                        activated_at=existing_session.activated_at.isoformat(),
                        remaining_seconds=remaining_seconds,
                    ),
                    idempotent=True,
                )

        # Validate merchant_id or merchant_place_id is provided
        if not request.merchant_id and not request.merchant_place_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "missing_merchant",
                    "message": "Either merchant_id or merchant_place_id is required",
                },
            )

        # Check for existing active session
        existing_active = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                ExclusiveSession.status == ExclusiveSessionStatus.ACTIVE,
            )
            .first()
        )

        if existing_active:
            # Check if expired
            if existing_active.expires_at < datetime.now(timezone.utc):
                # Mark as expired
                existing_active.status = ExclusiveSessionStatus.EXPIRED
                existing_active.updated_at = datetime.now(timezone.utc)
                db.commit()
                log_event(
                    "exclusive_expired",
                    {
                        "driver_id": driver.id,
                        "exclusive_session_id": str(existing_active.id),
                        "merchant_id": existing_active.merchant_id,
                    },
                )
            else:
                # Return existing active session
                remaining_seconds = int(
                    (existing_active.expires_at - datetime.now(timezone.utc)).total_seconds()
                )
                return ActivateExclusiveResponse(
                    status="ACTIVE",
                    exclusive_session=ExclusiveSessionResponse(
                        id=str(existing_active.id),
                        merchant_id=existing_active.merchant_id,
                        charger_id=existing_active.charger_id,
                        expires_at=existing_active.expires_at.isoformat(),
                        activated_at=existing_active.activated_at.isoformat(),
                        remaining_seconds=max(0, remaining_seconds),
                    ),
                )

        # Validate charger_id and location are provided
        if not request.charger_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "missing_charger_id", "message": "charger_id is required"},
            )

        # Location validation: lat/lng are required, enforce charger radius
        distance_m, is_within_radius = validate_charger_radius(
            db, request.charger_id, request.lat, request.lng
        )
        if not is_within_radius:
            logger.warning(
                f"Activation rejected: outside charger radius distance={distance_m:.0f}m, required={CHARGER_RADIUS_M}m",
                extra={
                    "driver_id": driver.id,
                    "charger_id": request.charger_id,
                    "distance_m": distance_m,
                    "radius_m": CHARGER_RADIUS_M,
                },
            )
            log_event(
                "exclusive_activation_outside_radius_rejected",
                {
                    "driver_id": driver.id,
                    "charger_id": request.charger_id,
                    "distance_m": distance_m,
                    "radius_m": CHARGER_RADIUS_M,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "outside_charger_radius",
                    "message": f"You are {distance_m:.0f}m from the charger. Must be within {CHARGER_RADIUS_M}m.",
                    "distance_m": round(distance_m, 1),
                    "radius_m": CHARGER_RADIUS_M,
                },
            )

        # Resolve merchant_id to WYC merchant ID (FK constraint requires it)
        resolved_merchant_id = request.merchant_id
        if resolved_merchant_id:
            wyc_merchant = db.query(Merchant).filter(Merchant.id == resolved_merchant_id).first()
            if not wyc_merchant and request.merchant_place_id:
                wyc_merchant = (
                    db.query(Merchant)
                    .filter(Merchant.place_id == request.merchant_place_id)
                    .first()
                )
            if not wyc_merchant:
                # Try place_id as merchant_id (common when frontend sends place_id)
                wyc_merchant = (
                    db.query(Merchant).filter(Merchant.place_id == resolved_merchant_id).first()
                )
            if wyc_merchant:
                resolved_merchant_id = wyc_merchant.id
        elif request.merchant_place_id:
            wyc_merchant = (
                db.query(Merchant).filter(Merchant.place_id == request.merchant_place_id).first()
            )
            if wyc_merchant:
                resolved_merchant_id = wyc_merchant.id

        # Find the driver's active charging session (if any) for post-charge expiry
        active_charging_session = (
            db.query(SessionEvent)
            .filter(
                SessionEvent.driver_user_id == driver.id,
                SessionEvent.session_end.is_(None),  # Still active (no end time)
            )
            .order_by(SessionEvent.session_start.desc())
            .first()
        )

        charging_session_id = None
        if active_charging_session:
            charging_session_id = active_charging_session.id

        # Create exclusive session
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=EXCLUSIVE_DURATION_MIN)

        # Build intent_metadata dict if intent is provided
        intent_metadata = None
        if request.intent:
            intent_metadata = {
                "party_size": request.party_size,
                "needs_power_outlet": request.needs_power_outlet,
                "is_to_go": request.is_to_go,
            }

        # Generate verification code eagerly (for QR code display)
        verification_code = None
        if resolved_merchant_id:
            wyc_m = db.query(Merchant).filter(Merchant.id == resolved_merchant_id).first()
            if wyc_m:
                if not wyc_m.short_code:
                    base_code = "".join(c for c in wyc_m.name.upper() if c.isalnum())[:6]
                    existing_code = (
                        db.query(Merchant).filter(Merchant.short_code == base_code).first()
                    )
                    if existing_code and existing_code.id != wyc_m.id:
                        for i in range(1, 100):
                            new_code = f"{base_code[:5]}{i}"
                            if (
                                not db.query(Merchant)
                                .filter(Merchant.short_code == new_code)
                                .first()
                            ):
                                base_code = new_code
                                break
                    wyc_m.short_code = base_code
                    wyc_m.region_code = wyc_m.region_code or "ATX"
                    db.flush()
                region_code = wyc_m.region_code or "ATX"
                from datetime import date

                today_start = datetime.combine(date.today(), datetime.min.time())
                latest = (
                    db.query(VerifiedVisit)
                    .filter(
                        VerifiedVisit.merchant_id == wyc_m.id,
                        VerifiedVisit.verified_at >= today_start,
                    )
                    .order_by(VerifiedVisit.visit_number.desc())
                    .with_for_update()
                    .first()
                )
                visit_number = (latest.visit_number if latest else 0) + 1
                verification_code = f"{region_code}-{wyc_m.short_code}-{str(visit_number).zfill(3)}"

                # Reserve the visit number atomically in the same transaction
                reserved_visit = VerifiedVisit(
                    merchant_id=wyc_m.id,
                    visit_number=visit_number,
                    verification_code=verification_code,
                    verified_at=now,
                    status="reserved",
                )
                db.add(reserved_visit)
                db.flush()

        session = ExclusiveSession(
            id=generate_session_id(),
            driver_id=driver.id,
            merchant_id=resolved_merchant_id,
            merchant_place_id=request.merchant_place_id or request.merchant_id,
            charger_id=request.charger_id,
            charger_place_id=request.charger_place_id,
            intent_session_id=request.intent_session_id,
            charging_session_id=charging_session_id,
            verification_code=verification_code,
            status=ExclusiveSessionStatus.ACTIVE,
            activated_at=now,
            expires_at=expires_at,
            activation_lat=request.lat,
            activation_lng=request.lng,
            activation_accuracy_m=request.accuracy_m,
            activation_distance_to_charger_m=distance_m,
            # V3: Intent capture fields
            intent=request.intent,
            intent_metadata=intent_metadata,
            idempotency_key=idempotency_key if idempotency_key else None,
        )

        try:
            db.add(session)
            db.commit()
            db.refresh(session)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            # Check if error is due to unique constraint violation on idempotency_key
            error_str = str(e).lower()
            if idempotency_key and (
                "unique" in error_str or "duplicate" in error_str or "constraint" in error_str
            ):
                # Fetch existing session by idempotency_key
                existing_session = (
                    db.query(ExclusiveSession)
                    .filter(ExclusiveSession.idempotency_key == idempotency_key)
                    .first()
                )
                if existing_session:
                    # Guard against idempotency key collision across drivers
                    if existing_session.driver_id != driver.id:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="Idempotency key already used by another driver",
                        ) from None
                    # Return idempotent response
                    remaining_seconds = max(
                        0,
                        int(
                            (
                                existing_session.expires_at - datetime.now(timezone.utc)
                            ).total_seconds()
                        ),
                    )
                    return ActivateExclusiveResponse(
                        status=existing_session.status.value,
                        exclusive_session=ExclusiveSessionResponse(
                            id=str(existing_session.id),
                            merchant_id=existing_session.merchant_id,
                            charger_id=existing_session.charger_id,
                            expires_at=existing_session.expires_at.isoformat(),
                            activated_at=existing_session.activated_at.isoformat(),
                            remaining_seconds=remaining_seconds,
                        ),
                        idempotent=True,
                    )
            logger.error(
                "exclusive_activate_failed: %s",
                str(e),
                exc_info=True,
                extra={
                    "driver_id": driver.id,
                    "merchant_id": request.merchant_id,
                    "charger_id": request.charger_id,
                    "idempotency_key": idempotency_key,
                    "error": str(e),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to activate exclusive session",
            ) from None

        # Log activation event (both structured log and standard logger)
        log_event(
            "exclusive_activated",
            {
                "driver_id": driver.id,
                "exclusive_session_id": str(session.id),
                "merchant_id": request.merchant_id,
                "merchant_place_id": request.merchant_place_id,
                "charger_id": request.charger_id,
                "distance_m": distance_m,
                "expires_at": expires_at.isoformat(),
            },
        )
        logger.info(
            f"[Exclusive][Activate] Session {session.id} activated for driver {driver.id}, "
            f"merchant {request.merchant_id}, charger {request.charger_id}, "
            f"distance {distance_m:.1f}m, expires at {expires_at.isoformat()}"
        )

        # PostHog: Fire exclusive_activated event
        request_id = getattr(http_request.state, "request_id", None)
        analytics = get_analytics_client()

        # Get cluster_id if available (from charger) - non-fatal if table doesn't exist
        cluster_id = None
        if request.charger_id:
            try:
                from app.models.while_you_charge import ChargerCluster

                cluster = (
                    db.query(ChargerCluster)
                    .filter(ChargerCluster.charger_id == request.charger_id)
                    .first()
                )
                if cluster:
                    cluster_id = str(cluster.id)
            except Exception:
                pass  # ChargerCluster table may not exist - that's OK

        analytics.capture(
            event="exclusive_activated",
            distinct_id=driver.public_id,  # Use user.public_id as distinct_id
            request_id=request_id,
            user_id=driver.public_id,
            merchant_id=request.merchant_id,
            charger_id=request.charger_id,
            session_id=str(session.id),
            ip=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            properties={
                "distance_m": distance_m,
                "expires_at": expires_at.isoformat(),
                "cluster_id": cluster_id,
                "merchant_id": request.merchant_id,
                "session_id": str(session.id),
                "source": "driver",
            },
        )

        remaining_seconds = int((expires_at - now).total_seconds())

        # Send push notification for exclusive confirmation (best-effort)
        try:
            from app.services.push_service import send_exclusive_confirmed_push

            merchant_name = request.merchant_id or "a nearby merchant"
            # Try to resolve merchant name from DB
            if request.merchant_id:
                from app.models.while_you_charge import Merchant as WYCMerchant

                m = db.query(WYCMerchant).filter(WYCMerchant.id == request.merchant_id).first()
                if m:
                    merchant_name = m.name
            send_exclusive_confirmed_push(db, driver.id, merchant_name)
        except Exception as push_err:
            logger.debug("Push notification failed (non-fatal): %s", push_err)

        return ActivateExclusiveResponse(
            status="ACTIVE",
            exclusive_session=_enrich_session_response(db, session, remaining_seconds),
        )
    except HTTPException:
        # Re-raise HTTP exceptions (they already have proper status codes)
        raise
    except Exception as e:
        # Log the full exception with context
        logger.exception(
            "Exclusive activation failed with unexpected error",
            extra={
                "merchant_id": request.merchant_id if request else None,
                "charger_id": request.charger_id if request else None,
                "driver_id": driver.id if driver else None,
                "lat": request.lat if request else None,
                "lng": request.lng if request else None,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )

        # Return a 500 with a clear message
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "internal_server_error",
                "message": "Exclusive activation failed due to an unexpected error",
                "request_id": getattr(http_request.state, "request_id", None),
            },
        ) from None


@router.post("/complete", response_model=CompleteExclusiveResponse)
async def complete_exclusive(
    request: CompleteExclusiveRequest,
    http_request: Request,
    driver: Optional[User] = Depends(get_current_driver_optional),
    db: Session = Depends(get_db),
):
    """
    Complete an exclusive session.

    Requires:
    - Driver authentication (required in production, optional in local/dev for demo)
    - Session must be ACTIVE
    - Session must belong to the driver
    """
    # P1 Security: Require auth in production (no demo fallback)
    if not driver:
        if is_local_env():
            # Demo fallback only in local/dev environments
            default_driver = db.query(User).filter(User.email == "demo@nerava.local").first()
            if not default_driver:
                from app.models import User as UserModel

                default_driver = UserModel(
                    id=1,
                    email="demo@nerava.local",
                    password_hash="demo",
                    is_active=True,
                    role_flags="driver",
                    auth_provider="local",
                )
                db.add(default_driver)
                db.commit()
                db.refresh(default_driver)
            driver = default_driver
        else:
            # Production: require authentication
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Idempotency check — idempotency_key has a global unique constraint
    idempotency_key = http_request.headers.get("X-Idempotency-Key")
    if idempotency_key:
        existing_completed = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.idempotency_key == idempotency_key,
                ExclusiveSession.status == ExclusiveSessionStatus.COMPLETED,
            )
            .first()
        )
        if existing_completed:
            # Guard against idempotency key collision across drivers
            if existing_completed.driver_id != driver.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key already used by another driver",
                )
            return CompleteExclusiveResponse(
                status=existing_completed.status.value,
                idempotent=True,
                nova_earned=0.0,  # Nova earned is tracked separately in nova_transactions
            )

    session = (
        db.query(ExclusiveSession)
        .filter(
            ExclusiveSession.id == request.exclusive_session_id,
            ExclusiveSession.driver_id == driver.id,
        )
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exclusive session not found"
        )

    if session.status != ExclusiveSessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is not active. Current status: {session.status.value}",
        )

    # Mark as completed
    now = datetime.now(timezone.utc)
    session.status = ExclusiveSessionStatus.COMPLETED
    session.completed_at = now
    session.updated_at = now
    if idempotency_key:
        session.idempotency_key = idempotency_key

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(
            "exclusive_complete_failed",
            extra={
                "driver_id": driver.id,
                "session_id": str(session.id),
                "idempotency_key": idempotency_key,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete exclusive session",
        ) from None

    # Calculate duration
    duration_seconds = int((now - session.activated_at).total_seconds())

    # Log completion event (both structured log and standard logger)
    log_event(
        "exclusive_completed",
        {
            "driver_id": driver.id,
            "exclusive_session_id": str(session.id),
            "merchant_id": session.merchant_id,
            "charger_id": session.charger_id,
            "duration_seconds": duration_seconds,
        },
    )
    logger.info(
        f"[Exclusive][Complete] Session {session.id} completed for driver {driver.id}, "
        f"merchant {session.merchant_id}, duration {duration_seconds}s"
    )

    # Analytics: Capture completion event
    request_id = getattr(http_request.state, "request_id", None)
    analytics = get_analytics_client()
    analytics.capture(
        event="server.driver.exclusive.complete.success",
        distinct_id=driver.public_id,
        request_id=request_id,
        user_id=driver.public_id,
        merchant_id=session.merchant_id,
        charger_id=session.charger_id,
        session_id=str(session.id),
        ip=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
        properties={
            "duration_seconds": duration_seconds,
        },
    )

    # HubSpot: Update driver contact on completion (non-blocking)
    # Failures here should NOT prevent the user from completing their exclusive
    try:
        # Check if this is the first completion for this driver
        completed_count = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                ExclusiveSession.status == ExclusiveSessionStatus.COMPLETED,
            )
            .count()
        )

        hubspot = get_hubspot_client()
        hubspot_properties = {
            "exclusive_completions": completed_count,
            "last_exclusive_completed_at": now.isoformat() + "Z",
        }

        # If first completion, set lifecycle stage
        if completed_count == 1:
            hubspot_properties["lifecycle_stage"] = "engaged_driver"

        # Update contact by phone (if available) or by driver_id
        if driver.phone:
            contact_id = hubspot.upsert_contact(phone=driver.phone, properties=hubspot_properties)
            if contact_id:
                hubspot.update_contact_properties(contact_id, hubspot_properties)
    except Exception as e:
        # Log but don't fail - HubSpot is CRM, not critical path
        logger.error(f"HubSpot update failed for driver {driver.id}: {e}")

    return CompleteExclusiveResponse(status="COMPLETED")


@router.get("/active", response_model=ActiveExclusiveResponse)
async def get_active_exclusive(
    include_expired: bool = Query(False, description="Include expired sessions"),
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Get the currently active exclusive session for the driver.

    If session is expired, marks it as EXPIRED and returns null.
    """
    active_session = (
        db.query(ExclusiveSession)
        .filter(
            ExclusiveSession.driver_id == driver.id,
            ExclusiveSession.status == ExclusiveSessionStatus.ACTIVE,
        )
        .first()
    )

    if not active_session:
        return ActiveExclusiveResponse(exclusive_session=None)

    # Compute effective expiry (may extend if charging session ended)
    effective_expiry = _compute_effective_expiry(db, active_session)

    # Check if expired
    if effective_expiry < datetime.now(timezone.utc):
        # Mark as expired
        active_session.status = ExclusiveSessionStatus.EXPIRED
        active_session.updated_at = datetime.now(timezone.utc)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise

        log_event(
            "exclusive_expired",
            {
                "driver_id": driver.id,
                "exclusive_session_id": str(active_session.id),
                "merchant_id": active_session.merchant_id,
            },
        )

        if include_expired:
            remaining_seconds = 0
        else:
            return ActiveExclusiveResponse(exclusive_session=None)
    else:
        remaining_seconds = int((effective_expiry - datetime.now(timezone.utc)).total_seconds())
        try:
            db.commit()  # Persist any expiry extension from _compute_effective_expiry
        except Exception:
            db.rollback()
            raise

    return ActiveExclusiveResponse(
        exclusive_session=_enrich_session_response(db, active_session, remaining_seconds)
    )


class SessionLookupResponse(BaseModel):
    exclusive_session: Optional[ExclusiveSessionResponse] = None
    merchant_name: Optional[str] = None
    exclusive_title: Optional[str] = None
    staff_instructions: Optional[str] = None


@router.get("/session/{session_id}", response_model=SessionLookupResponse)
async def get_exclusive_session(session_id: str, db: Session = Depends(get_db)):
    """
    Look up an exclusive session by ID.

    Used by staff-facing CustomerExclusiveView to display session details.
    No auth required — session ID acts as a capability token.
    """
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session ID format"
        ) from None

    session = db.query(ExclusiveSession).filter(ExclusiveSession.id == session_uuid).first()

    if not session:
        return SessionLookupResponse(exclusive_session=None)

    # Check if expired
    if session.status == ExclusiveSessionStatus.ACTIVE and session.expires_at < datetime.now(
        timezone.utc
    ):
        session.status = ExclusiveSessionStatus.EXPIRED
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

    remaining_seconds = 0
    if session.status == ExclusiveSessionStatus.ACTIVE:
        remaining_seconds = max(
            0, int((session.expires_at - datetime.now(timezone.utc)).total_seconds())
        )

    # Get merchant name
    merchant_name = None
    if session.merchant_id:
        merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()
        if merchant:
            merchant_name = merchant.name

    # Get exclusive title from merchant's active perk (if any)
    exclusive_title = None
    staff_instructions = None
    # For now, use generic values — these can be enriched later
    # when we add exclusive_id to the session model

    return SessionLookupResponse(
        exclusive_session=ExclusiveSessionResponse(
            id=str(session.id),
            merchant_id=session.merchant_id,
            charger_id=None,  # Redacted: unauthenticated endpoint, staff only needs merchant info
            expires_at=session.expires_at.isoformat(),
            activated_at=session.activated_at.isoformat(),
            remaining_seconds=remaining_seconds,
        ),
        merchant_name=merchant_name,
        exclusive_title=exclusive_title,
        staff_instructions=staff_instructions,
    )


@router.post("/verify", response_model=VerifyVisitResponse)
async def verify_visit(
    request: VerifyVisitRequest,
    http_request: Request,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Verify a visit and generate a verification code.

    This endpoint creates a verified_visit record with an incremental verification code
    that merchants can use to link orders to redemptions.

    The verification code follows the format: {REGION}-{MERCHANT_CODE}-{VISIT_NUMBER}
    Example: ATX-ASADAS-023 (23rd visit to Asadas in Austin region)
    """
    try:
        # Get the exclusive session
        session = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.id == request.exclusive_session_id,
                ExclusiveSession.driver_id == driver.id,
            )
            .first()
        )

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Exclusive session not found"
            )

        # Get the merchant
        merchant = db.query(Merchant).filter(Merchant.id == session.merchant_id).first()

        if not merchant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found")

        # Ensure merchant has a short_code
        if not merchant.short_code:
            # Generate a short code from merchant name (first 6 chars, uppercase, no spaces)
            base_code = "".join(c for c in merchant.name.upper() if c.isalnum())[:6]
            # Check for uniqueness and append number if needed
            existing = db.query(Merchant).filter(Merchant.short_code == base_code).first()
            if existing:
                # Find unique code by appending a number
                for i in range(1, 100):
                    new_code = f"{base_code[:5]}{i}"
                    if not db.query(Merchant).filter(Merchant.short_code == new_code).first():
                        base_code = new_code
                        break
            merchant.short_code = base_code
            merchant.region_code = merchant.region_code or "ATX"
            db.flush()

        region_code = merchant.region_code or "ATX"
        merchant_code = merchant.short_code

        # Check if a verified visit already exists for this session
        existing_visit = (
            db.query(VerifiedVisit).filter(VerifiedVisit.exclusive_session_id == session.id).first()
        )

        if existing_visit:
            # Return the existing verification code
            return VerifyVisitResponse(
                status="ALREADY_VERIFIED",
                verification_code=existing_visit.verification_code,
                visit_number=existing_visit.visit_number,
                merchant_name=merchant.name,
                verified_at=existing_visit.verified_at.isoformat(),
            )

        # Get the next visit number for this merchant TODAY (resets daily)
        # Locks only the latest row instead of all matching rows
        from datetime import date

        today_start = datetime.combine(date.today(), datetime.min.time())

        latest = (
            db.query(VerifiedVisit)
            .filter(
                VerifiedVisit.merchant_id == merchant.id, VerifiedVisit.verified_at >= today_start
            )
            .order_by(VerifiedVisit.visit_number.desc())
            .with_for_update()
            .first()
        )
        visit_number = (latest.visit_number if latest else 0) + 1

        # Generate verification code
        verification_code = VerifiedVisit.generate_verification_code(
            region_code, merchant_code, visit_number
        )

        # Create the verified visit
        now = datetime.now(timezone.utc)
        verified_visit = VerifiedVisit(
            id=str(uuid.uuid4()),
            verification_code=verification_code,
            region_code=region_code,
            merchant_code=merchant_code,
            visit_number=visit_number,
            merchant_id=merchant.id,
            driver_id=driver.id,
            exclusive_session_id=session.id,
            charger_id=session.charger_id,
            verified_at=now,
            visit_date=today_start,  # Store the date for daily reset
            verification_lat=request.lat or session.activation_lat,
            verification_lng=request.lng or session.activation_lng,
        )

        db.add(verified_visit)

        # Mark the exclusive session as COMPLETED if not already
        if session.status == ExclusiveSessionStatus.ACTIVE:
            session.status = ExclusiveSessionStatus.COMPLETED
            session.completed_at = now
            session.updated_at = now

        try:
            db.commit()
            db.refresh(verified_visit)
        except Exception:
            db.rollback()
            # Likely duplicate verification code — check for existing visit
            existing = (
                db.query(VerifiedVisit)
                .filter(VerifiedVisit.exclusive_session_id == session.id)
                .first()
            )
            if existing:
                return VerifyVisitResponse(
                    status="ALREADY_VERIFIED",
                    verification_code=existing.verification_code,
                    visit_number=existing.visit_number,
                    merchant_name=merchant.name,
                    verified_at=existing.verified_at.isoformat(),
                )
            # No existing visit — get a fresh visit_number and retry
            fresh_latest = (
                db.query(VerifiedVisit)
                .filter(
                    VerifiedVisit.merchant_id == merchant.id,
                    VerifiedVisit.verified_at >= today_start,
                )
                .order_by(VerifiedVisit.visit_number.desc())
                .with_for_update()
                .first()
            )
            visit_number = (fresh_latest.visit_number if fresh_latest else 0) + 1
            verification_code = VerifiedVisit.generate_verification_code(
                region_code, merchant_code, visit_number
            )
            verified_visit.verification_code = verification_code
            verified_visit.visit_number = visit_number
            db.add(verified_visit)
            if session.status == ExclusiveSessionStatus.ACTIVE:
                session.status = ExclusiveSessionStatus.COMPLETED
                session.completed_at = now
                session.updated_at = now
            try:
                db.commit()
                db.refresh(verified_visit)
            except Exception as retry_err:
                db.rollback()
                logger.error(
                    "visit_retry_commit_failed",
                    extra={
                        "merchant_id": merchant.id,
                        "visit_number": visit_number,
                        "verification_code": verification_code,
                        "error": str(retry_err),
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to record visit after retry",
                ) from None

        # Log the verification event
        log_event(
            "visit_verified",
            {
                "driver_id": driver.id,
                "merchant_id": merchant.id,
                "merchant_name": merchant.name,
                "verification_code": verification_code,
                "visit_number": visit_number,
                "exclusive_session_id": str(session.id),
            },
        )
        logger.info(
            f"[Exclusive][Verify] Visit verified: {verification_code} for driver {driver.id} "
            f"at {merchant.name} (visit #{visit_number})"
        )

        # Analytics: Capture verification event
        request_id = getattr(http_request.state, "request_id", None)
        analytics = get_analytics_client()
        analytics.capture(
            event="visit_verified",
            distinct_id=driver.public_id,
            request_id=request_id,
            user_id=driver.public_id,
            merchant_id=merchant.id,
            charger_id=session.charger_id,
            session_id=str(session.id),
            ip=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            properties={
                "verification_code": verification_code,
                "visit_number": visit_number,
                "merchant_name": merchant.name,
            },
        )

        return VerifyVisitResponse(
            status="VERIFIED",
            verification_code=verification_code,
            visit_number=visit_number,
            merchant_name=merchant.name,
            verified_at=now.isoformat(),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "Visit verification failed with unexpected error",
            extra={
                "exclusive_session_id": request.exclusive_session_id,
                "driver_id": driver.id if driver else None,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "internal_server_error",
                "message": "Visit verification failed due to an unexpected error",
                "request_id": getattr(http_request.state, "request_id", None),
            },
        ) from None


class VisitListItem(BaseModel):
    verification_code: str
    visit_number: int
    driver_id: int
    verified_at: str
    redeemed_at: Optional[str] = None
    order_reference: Optional[str] = None


class MerchantVisitsResponse(BaseModel):
    merchant_id: str
    merchant_name: str
    total_visits: int
    visits_today: int
    visits: List[VisitListItem]


@router.get("/visits/{merchant_id}", response_model=MerchantVisitsResponse)
async def get_merchant_visits(
    merchant_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all verified visits for a merchant.

    This endpoint allows merchants to look up all visits and their verification codes.
    Requires merchant or merchant_admin role.
    """
    # Require merchant role — drivers should not see other merchants' visits
    user_roles = (user.role_flags or "").split(",")
    if (
        "merchant" not in user_roles
        and "merchant_admin" not in user_roles
        and "admin" not in user_roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Merchant role required to view visits",
        )

    # Verify the user owns this merchant (admin bypasses)
    _verify_merchant_ownership(db, user, merchant_id)

    # Get the merchant
    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found")

    # Get total count
    total = db.query(VerifiedVisit).filter(VerifiedVisit.merchant_id == merchant_id).count()

    # Get today's count
    from datetime import date

    today_start = datetime.combine(date.today(), datetime.min.time())
    visits_today = (
        db.query(VerifiedVisit)
        .filter(VerifiedVisit.merchant_id == merchant_id, VerifiedVisit.verified_at >= today_start)
        .count()
    )

    # Get visits with pagination
    visits = (
        db.query(VerifiedVisit)
        .filter(VerifiedVisit.merchant_id == merchant_id)
        .order_by(VerifiedVisit.verified_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return MerchantVisitsResponse(
        merchant_id=merchant_id,
        merchant_name=merchant.name,
        total_visits=total,
        visits_today=visits_today,
        visits=[
            VisitListItem(
                verification_code=v.verification_code,
                visit_number=v.visit_number,
                driver_id=v.driver_id,
                verified_at=v.verified_at.isoformat(),
                redeemed_at=v.redeemed_at.isoformat() if v.redeemed_at else None,
                order_reference=v.order_reference,
            )
            for v in visits
        ],
    )


class VisitLookupResponse(BaseModel):
    verification_code: str
    visit_number: int
    merchant_name: str
    verified_at: str
    redeemed_at: Optional[str] = None
    order_reference: Optional[str] = None


@router.get("/visits/lookup/{verification_code}", response_model=VisitLookupResponse)
async def lookup_visit(
    verification_code: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Look up a specific visit by verification code.

    Merchants use this to verify a driver's visit code.
    Requires merchant or merchant_admin role.
    """
    user_roles = (user.role_flags or "").split(",")
    if (
        "merchant" not in user_roles
        and "merchant_admin" not in user_roles
        and "admin" not in user_roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Merchant role required to look up visits",
        )
    visit = (
        db.query(VerifiedVisit)
        .filter(VerifiedVisit.verification_code == verification_code.upper())
        .first()
    )

    if not visit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Verification code not found"
        )

    # Verify the user owns the merchant this visit belongs to (admin bypasses)
    _verify_merchant_ownership(db, user, visit.merchant_id)

    merchant = db.query(Merchant).filter(Merchant.id == visit.merchant_id).first()

    return VisitLookupResponse(
        verification_code=visit.verification_code,
        visit_number=visit.visit_number,
        merchant_name=merchant.name if merchant else "Unknown",
        verified_at=visit.verified_at.isoformat(),
        redeemed_at=visit.redeemed_at.isoformat() if visit.redeemed_at else None,
        order_reference=visit.order_reference,
    )


class MarkRedeemedRequest(BaseModel):
    order_reference: Optional[str] = None
    notes: Optional[str] = None


@router.post("/visits/redeem/{verification_code}")
async def mark_visit_redeemed(
    verification_code: str,
    request: MarkRedeemedRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Mark a visit as redeemed by the merchant.

    Merchants call this when they fulfill the customer's order.
    Requires merchant or merchant_admin role.
    """
    user_roles = (user.role_flags or "").split(",")
    if (
        "merchant" not in user_roles
        and "merchant_admin" not in user_roles
        and "admin" not in user_roles
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Merchant role required to redeem visits",
        )
    visit = (
        db.query(VerifiedVisit)
        .filter(VerifiedVisit.verification_code == verification_code.upper())
        .first()
    )

    if not visit:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Verification code not found"
        )

    # Verify the user owns the merchant this visit belongs to (admin bypasses)
    _verify_merchant_ownership(db, user, visit.merchant_id)

    if visit.redeemed_at:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Visit already redeemed")

    visit.redeemed_at = datetime.now(timezone.utc)
    visit.order_reference = request.order_reference
    visit.redemption_notes = request.notes
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(
            "visit_redeem_failed",
            extra={
                "verification_code": verification_code.upper(),
                "merchant_id": visit.merchant_id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to redeem visit",
        ) from None

    logger.info(
        f"[Exclusive][Redeem] Visit {verification_code} marked as redeemed, "
        f"order_reference={request.order_reference}"
    )

    return {"status": "REDEEMED", "verification_code": verification_code}
