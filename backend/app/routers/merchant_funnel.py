"""
Merchant acquisition funnel endpoints.

Flow: Landing CTA -> /find (search) -> /preview (personalized page) -> Loom -> CTAs
"""
import hashlib
import hmac
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.models.while_you_charge import Charger, ChargerMerchant, Merchant
from app.services.analytics import get_analytics_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchant/funnel", tags=["merchant_funnel"])

PREVIEW_TTL_DAYS = 7


# ---------------------------------------------------------------------------
# HMAC signing helpers
# ---------------------------------------------------------------------------

def _get_signing_key() -> str:
    """Return signing key, falling back to JWT_SECRET in dev."""
    key = settings.PREVIEW_SIGNING_KEY
    if not key:
        key = settings.JWT_SECRET
    return key


def sign_preview(merchant_id: str, expires_at: int) -> str:
    """HMAC-SHA256 hex digest over merchant_id:expires_at."""
    key = _get_signing_key()
    message = f"{merchant_id}:{expires_at}"
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_signature(merchant_id: str, expires_at: int, sig: str) -> bool:
    """Verify HMAC signature and check TTL."""
    if int(time.time()) > expires_at:
        return False
    expected = sign_preview(merchant_id, expires_at)
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    place_id: str
    name: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_url: Optional[str] = None
    types: list = Field(default_factory=list)


class SearchResponse(BaseModel):
    results: list[SearchResult]


class ResolveRequest(BaseModel):
    place_id: str
    name: str
    lat: float
    lng: float


class ResolveResponse(BaseModel):
    merchant_id: str
    preview_url: str
    sig: str
    expires_at: int


class PreviewPayload(BaseModel):
    merchant_id: str
    name: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_url: Optional[str] = None
    photo_urls: list = Field(default_factory=list)
    open_now: Optional[bool] = None
    business_status: Optional[str] = None
    category: Optional[str] = None
    nearest_charger: Optional[dict] = None
    verified_visit_count: int = 0


class TextLinkRequest(BaseModel):
    phone: str
    preview_url: str
    merchant_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/search", response_model=SearchResponse)
async def search_businesses(
    q: str = Query(..., min_length=1, max_length=200),
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
):
    """Google Places text search wrapper (max 10 results)."""
    from app.services.google_places_new import get_photo_url, search_text

    location_bias = None
    if lat is not None and lng is not None:
        location_bias = {"lat": lat, "lng": lng}

    try:
        places = await search_text(query=q, location_bias=location_bias, max_results=10)
    except Exception as e:
        logger.error("[MerchantFunnel] search_text error: %s", e, exc_info=True)
        places = []

    results = []
    for p in places:
        # Resolve photo_ref: prefix to actual URL
        photo_url = p.get("photo_url")
        if photo_url and photo_url.startswith("photo_ref:"):
            photo_ref = photo_url.replace("photo_ref:", "")
            try:
                photo_url = await get_photo_url(photo_ref, max_width=400)
            except Exception:
                photo_url = None

        results.append(SearchResult(
            place_id=p.get("place_id", ""),
            name=p.get("name", ""),
            address=p.get("address"),
            lat=p.get("lat"),
            lng=p.get("lng"),
            rating=p.get("rating"),
            user_rating_count=p.get("user_rating_count"),
            photo_url=photo_url,
            types=p.get("types", []),
        ))

    return SearchResponse(results=results)


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_merchant(body: ResolveRequest, db: Session = Depends(get_db)):
    """Idempotent: place_id -> find-or-create Merchant, enrich, return signed preview URL."""
    # Check for existing merchant by place_id
    merchant = db.query(Merchant).filter(Merchant.place_id == body.place_id).first()

    if not merchant:
        merchant_id = f"m_{uuid.uuid4().hex[:12]}"
        merchant = Merchant(
            id=merchant_id,
            place_id=body.place_id,
            name=body.name,
            lat=body.lat,
            lng=body.lng,
        )
        db.add(merchant)
        db.commit()
        db.refresh(merchant)

    # Always update name/location from the search result (authoritative source)
    merchant.name = body.name
    merchant.lat = body.lat
    merchant.lng = body.lng
    db.commit()

    # Enrich from Google Places if missing photo/rating data (best effort)
    needs_enrichment = not merchant.primary_photo_url and not merchant.photo_url
    if needs_enrichment:
        try:
            from app.services.merchant_enrichment import enrich_from_google_places
            await enrich_from_google_places(db, merchant, body.place_id, force_refresh=True)
        except Exception as e:
            logger.warning("[MerchantFunnel] enrichment failed for %s: %s", merchant.id, e)
            # Ensure session is usable after enrichment failure
            try:
                db.rollback()
                db.refresh(merchant)
            except Exception:
                pass

    expires_at = int(time.time()) + (PREVIEW_TTL_DAYS * 86400)
    sig = sign_preview(merchant.id, expires_at)
    preview_url = f"/preview?merchant_id={merchant.id}&exp={expires_at}&sig={sig}"

    # PostHog event
    analytics = get_analytics_client()
    analytics.capture(
        event="merchant_funnel.resolve",
        distinct_id=merchant.id,
        properties={
            "merchant_id": merchant.id,
            "place_id": body.place_id,
            "name": body.name,
        },
    )

    return ResolveResponse(
        merchant_id=merchant.id,
        preview_url=preview_url,
        sig=sig,
        expires_at=expires_at,
    )


@router.get("/preview", response_model=PreviewPayload)
async def get_preview(
    merchant_id: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
    db: Session = Depends(get_db),
):
    """Validate signature and return safe preview payload."""
    if not verify_signature(merchant_id, exp, sig):
        raise HTTPException(status_code=403, detail="This link has expired or is invalid.")

    merchant = db.query(Merchant).filter(Merchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found.")

    # Nearest charger via ChargerMerchant join
    nearest_charger = None
    cm = (
        db.query(ChargerMerchant)
        .filter(ChargerMerchant.merchant_id == merchant_id)
        .order_by(ChargerMerchant.distance_m.asc())
        .first()
    )
    if cm:
        charger = db.query(Charger).filter(Charger.id == cm.charger_id).first()
        if charger:
            walk_min = round(cm.walk_duration_s / 60) if cm.walk_duration_s else None
            nearest_charger = {
                "name": charger.name,
                "network": charger.network_name,
                "walk_minutes": walk_min,
                "distance_m": round(cm.distance_m) if cm.distance_m else None,
            }

    # Count verified visits
    verified_visit_count = 0
    try:
        from app.models.verified_visit import VerifiedVisit
        verified_visit_count = (
            db.query(VerifiedVisit)
            .filter(VerifiedVisit.merchant_id == merchant_id)
            .count()
        )
    except Exception:
        pass

    # PostHog event
    analytics = get_analytics_client()
    analytics.capture(
        event="merchant_funnel.preview_view",
        distinct_id=merchant_id,
        properties={
            "merchant_id": merchant_id,
            "name": merchant.name,
            "has_charger": nearest_charger is not None,
            "verified_visits": verified_visit_count,
        },
    )

    return PreviewPayload(
        merchant_id=merchant.id,
        name=merchant.name,
        address=merchant.address,
        lat=merchant.lat,
        lng=merchant.lng,
        rating=merchant.rating,
        user_rating_count=merchant.user_rating_count,
        photo_url=merchant.primary_photo_url or merchant.photo_url or (merchant.photo_urls[0] if merchant.photo_urls else None),
        photo_urls=merchant.photo_urls or [],
        open_now=merchant.open_now,
        business_status=merchant.business_status,
        category=merchant.category or merchant.primary_category,
        nearest_charger=nearest_charger,
        verified_visit_count=verified_visit_count,
    )


@router.post("/text-preview-link")
async def text_preview_link(body: TextLinkRequest):
    """Send preview URL via SMS (reuse Twilio)."""
    from app.utils.phone import normalize_phone

    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone number.")

    sms_body = (
        f"See how {body.merchant_name} appears to EV drivers on Nerava: "
        f"https://merchant.nerava.network{body.preview_url}"
    )

    sent = False
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.OTP_FROM_NUMBER:
        import asyncio
        try:
            from twilio.rest import Client
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

            def _send():
                return client.messages.create(
                    body=sms_body,
                    from_=settings.OTP_FROM_NUMBER,
                    to=phone,
                )

            await asyncio.wait_for(asyncio.to_thread(_send), timeout=15)
            sent = True
        except Exception as e:
            logger.error("[MerchantFunnel] SMS send error: %s", e, exc_info=True)
    else:
        logger.info("[MerchantFunnel][DEV] Would send SMS to %s: %s", phone, sms_body)

    # PostHog event
    analytics = get_analytics_client()
    analytics.capture(
        event="merchant_funnel.text_link_sent",
        distinct_id=phone,
        properties={"merchant_name": body.merchant_name},
    )

    return {"ok": sent or settings.ENV == "dev", "message": "Link sent" if sent else "SMS not configured in dev"}
