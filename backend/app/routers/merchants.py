"""
Merchant Details Router
Handles GET /v1/merchants/{merchant_id} endpoint
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.models import User
from app.models.while_you_charge import AmenityVote, FavoriteMerchant
from app.models.while_you_charge import Merchant as WYCMerchant
from app.schemas.merchants import AmenityVoteRequest, AmenityVoteResponse, MerchantDetailsResponse
from app.services.merchant_details import get_merchant_details

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchants", tags=["merchants"])


# IMPORTANT: Static routes must be defined BEFORE dynamic /{merchant_id} routes


@router.get("/me")
def get_my_merchant(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_driver),
):
    """
    Get merchant dashboard data for the authenticated merchant admin.
    Delegates to merchants_domain /me endpoint logic.
    """
    from sqlalchemy import and_

    from app.models.domain import DomainMerchant

    merchant = db.query(DomainMerchant).filter(
        and_(
            DomainMerchant.owner_user_id == current_user.id,
            DomainMerchant.status == "active",
        )
    ).first()

    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found for user")

    from app.services.nova_service import NovaService
    transactions = NovaService.get_merchant_transactions(db, merchant.id, limit=10)

    return {
        "merchant": {
            "id": merchant.id,
            "name": merchant.name,
            "nova_balance": merchant.nova_balance,
            "zone_slug": merchant.zone_slug,
            "status": merchant.status,
        },
        "transactions": [
            {
                "id": txn.id,
                "type": txn.type,
                "amount": txn.amount,
                "driver_user_id": txn.driver_user_id,
                "created_at": txn.created_at.isoformat(),
                "metadata": txn.transaction_meta,
            }
            for txn in transactions
        ],
    }


@router.get("/me/insights")
def get_my_merchant_insights(
    period: str = Query("30d", description="Time period: 7d, 30d, 90d"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_driver),
):
    """Merchant insights — nearby charging sessions, dwell time, peak hours."""
    from datetime import datetime, timedelta

    from sqlalchemy import and_

    from app.models.domain import DomainMerchant
    from app.models.session_event import SessionEvent
    from app.models.while_you_charge import ChargerMerchant
    from app.models.while_you_charge import Merchant as WYCMerchant2

    merchant = db.query(DomainMerchant).filter(
        and_(
            DomainMerchant.owner_user_id == current_user.id,
            DomainMerchant.status == "active",
        )
    ).first()
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found for user")

    days = 30
    if period.endswith("d"):
        try:
            days = int(period[:-1])
        except ValueError:
            days = 30

    since = datetime.utcnow() - timedelta(days=days)

    wyc = db.query(WYCMerchant2).filter(WYCMerchant2.place_id == merchant.google_place_id).first() if merchant.google_place_id else None
    charger_ids = []
    if wyc:
        links = db.query(ChargerMerchant.charger_id).filter(ChargerMerchant.merchant_id == wyc.id).all()
        charger_ids = [l[0] for l in links]

    ev_sessions = 0
    unique_drivers = 0
    avg_duration = None
    avg_kwh = None
    peak_hours = []

    if charger_ids:
        base = db.query(SessionEvent).filter(
            SessionEvent.charger_id.in_(charger_ids),
            SessionEvent.session_start >= since,
        )
        ev_sessions = base.count()
        unique_drivers = base.with_entities(SessionEvent.driver_user_id).distinct().count()

        dur = base.with_entities(func.avg(SessionEvent.duration_seconds)).scalar()
        if dur:
            avg_duration = round(dur / 60, 1)

        kwh = base.with_entities(func.avg(SessionEvent.kwh_added)).scalar()
        if kwh:
            avg_kwh = round(float(kwh), 1)

        hour_counts = (
            base.with_entities(
                func.extract("hour", SessionEvent.session_start).label("hr"),
                func.count().label("cnt"),
            )
            .group_by("hr")
            .order_by("hr")
            .all()
        )
        peak_hours = [{"hour": int(h), "sessions": c} for h, c in hour_counts]

    return {
        "period": period,
        "ev_sessions_nearby": ev_sessions,
        "unique_drivers": unique_drivers,
        "avg_duration_minutes": avg_duration,
        "avg_kwh": avg_kwh,
        "peak_hours": peak_hours,
        "dwell_distribution": None,
        "walk_traffic": None,
    }


@router.get("/favorites")
def list_favorites(
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """List user's favorite merchants"""
    favorites = db.query(FavoriteMerchant).filter(
        FavoriteMerchant.user_id == driver.id
    ).all()

    merchant_ids = [f.merchant_id for f in favorites]
    merchants = db.query(WYCMerchant).filter(WYCMerchant.id.in_(merchant_ids)).all() if merchant_ids else []

    return {
        "favorites": [
            {
                "merchant_id": m.id,
                "name": m.name,
                "category": m.category,
                "photo_url": m.primary_photo_url or m.photo_url,
            }
            for m in merchants
        ]
    }


@router.post("/{merchant_id}/favorite")
def add_favorite(
    merchant_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """Add a merchant to favorites (idempotent)"""
    # Verify merchant exists
    merchant = db.query(WYCMerchant).filter(WYCMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=404,
            detail="Merchant not found"
        )

    # Check if already favorited
    favorite = db.query(FavoriteMerchant).filter(
        FavoriteMerchant.user_id == driver.id,
        FavoriteMerchant.merchant_id == merchant_id
    ).first()

    if favorite:
        return {"ok": True, "is_favorite": True}

    # Create favorite
    favorite = FavoriteMerchant(
        user_id=driver.id,
        merchant_id=merchant_id
    )
    db.add(favorite)
    db.commit()

    return {"ok": True, "is_favorite": True}


@router.delete("/{merchant_id}/favorite")
def remove_favorite(
    merchant_id: str,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """Remove a merchant from favorites"""
    favorite = db.query(FavoriteMerchant).filter(
        FavoriteMerchant.user_id == driver.id,
        FavoriteMerchant.merchant_id == merchant_id
    ).first()

    if favorite:
        db.delete(favorite)
        db.commit()

    return {"ok": True, "is_favorite": False}


@router.post(
    "/{merchant_id}/amenities/{amenity}/vote",
    response_model=AmenityVoteResponse,
    summary="Vote on a merchant amenity",
    description="""
    Vote on a merchant amenity (bathroom or wifi).
    
    - If user hasn't voted: creates a new vote
    - If user voted same type: removes vote (toggle)
    - If user voted different type: updates vote to new type
    
    Returns updated vote counts for the amenity.
    """
)
def vote_amenity(
    merchant_id: str,
    amenity: str,
    request: AmenityVoteRequest,
    driver: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Vote on a merchant amenity (bathroom or wifi).
    
    Auth: Required (Bearer token)
    Path params: merchant_id (string), amenity ('bathroom' | 'wifi')
    Request body: { vote_type: 'up' | 'down' }
    """
    # Validate amenity
    if amenity not in ['bathroom', 'wifi']:
        raise HTTPException(
            status_code=400,
            detail="Invalid amenity. Must be 'bathroom' or 'wifi'"
        )
    
    # Validate vote_type (already validated by Pydantic schema, but double-check)
    if request.vote_type not in ['up', 'down']:
        raise HTTPException(
            status_code=400,
            detail="Invalid vote_type. Must be 'up' or 'down'"
        )
    
    # Check if merchant exists
    merchant = db.query(WYCMerchant).filter(WYCMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=404,
            detail="Merchant not found"
        )
    
    # Get existing vote (if any)
    existing_vote = db.query(AmenityVote).filter(
        AmenityVote.merchant_id == merchant_id,
        AmenityVote.user_id == driver.id,
        AmenityVote.amenity == amenity
    ).first()
    
    # Upsert logic
    if existing_vote:
        if existing_vote.vote_type == request.vote_type:
            # Same vote type: toggle (remove vote)
            db.delete(existing_vote)
        else:
            # Different vote type: update
            existing_vote.vote_type = request.vote_type
    else:
        # No existing vote: create new
        new_vote = AmenityVote(
            merchant_id=merchant_id,
            user_id=driver.id,
            amenity=amenity,
            vote_type=request.vote_type
        )
        db.add(new_vote)
    
    db.commit()
    
    # Aggregate votes for response (single query)
    vote_counts = db.query(
        AmenityVote.vote_type,
        func.count(AmenityVote.id).label('count')
    ).filter(
        AmenityVote.merchant_id == merchant_id,
        AmenityVote.amenity == amenity
    ).group_by(AmenityVote.vote_type).all()
    
    # Initialize counts
    upvotes = 0
    downvotes = 0
    
    # Update from query results
    for vote_type, count in vote_counts:
        if vote_type == 'up':
            upvotes = count
        elif vote_type == 'down':
            downvotes = count
    
    return AmenityVoteResponse(
        ok=True,
        upvotes=upvotes,
        downvotes=downvotes
    )


@router.get("/{merchant_id}/share-link")
def get_share_link(
    merchant_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get shareable link for a merchant with optional referral param"""
    # Verify merchant exists
    merchant = db.query(WYCMerchant).filter(WYCMerchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(
            status_code=404,
            detail="Merchant not found"
        )

    # Build share URL
    from app.core.config import settings
    from app.dependencies.driver import get_current_driver_optional
    base_url = getattr(settings, 'FRONTEND_URL', 'https://app.nerava.network')
    url = f"{base_url}/merchant/{merchant_id}"

    # Try to get authenticated user (optional)
    try:
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get("access_token")

        if token:
            driver = get_current_driver_optional(request, token, db)
            if driver:
                url += f"?ref={driver.public_id}"
    except:
        pass

    return {
        "url": url,
        "title": f"Check out {merchant.name}",
        "description": merchant.description or f"Visit {merchant.name} while you charge"
    }


@router.get(
    "/{merchant_id}",
    response_model=MerchantDetailsResponse,
    summary="Get merchant details",
    description="""
    Get detailed information about a merchant including:
    - Merchant info (name, category, photo, address, rating)
    - Moment info (distance, walk time, charge window fit)
    - Perk info (title, badge, description)
    - Wallet state (can add, current state)
    - Actions (add to wallet, get directions)
    
    Optionally provide session_id query param for distance calculation.
    """
)
async def get_merchant_details_endpoint(
    merchant_id: str,
    http_request: Request,
    session_id: Optional[str] = Query(None, description="Optional intent session ID for context"),
    db: Session = Depends(get_db),
):
    """
    Get merchant details for a given merchant ID.
    """
    # Try to resolve driver for reward state (optional — no auth required)
    driver_user_id = None
    try:
        auth_header = http_request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from app.dependencies.driver import get_current_driver_optional
            driver = get_current_driver_optional(http_request, auth_header[7:], db)
            if driver:
                driver_user_id = driver.id
    except Exception:
        pass

    try:
        result = await get_merchant_details(db, merchant_id, session_id, driver_user_id=driver_user_id)

        # PostHog: Fire merchant_details_viewed event
        from app.services.analytics import get_analytics_client
        analytics = get_analytics_client()
        request_id = getattr(http_request.state, "request_id", None) if hasattr(http_request, 'state') else None
        
        analytics.capture(
            event="merchant_details_viewed",
            distinct_id="anonymous",  # No user auth required for this endpoint
            request_id=request_id,
            merchant_id=merchant_id,
            ip=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent") if hasattr(http_request, 'headers') else None,
            properties={
                "source": "driver"
            }
        )
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching merchant details for {merchant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch merchant details: {str(e)}")
