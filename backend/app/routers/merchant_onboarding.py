"""
Merchant Onboarding Router

Handles merchant onboarding endpoints:
- Google Business Profile OAuth (sign-in + verify ownership)
- Location claims
- Stripe SetupIntent for card-on-file
- Placement rule updates
"""
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.schemas.merchant_onboarding import (
    ClaimLocationRequest,
    ClaimLocationResponse,
    GoogleAuthStartResponse,
    LocationsListResponse,
    LocationSummary,
    SetupIntentResponse,
    UpdatePlacementRequest,
    UpdatePlacementResponse,
)
from app.services.google_business_profile import (
    exchange_oauth_code,
    get_oauth_authorize_url,
    get_user_info,
    list_locations,
)
from app.services.merchant_onboarding_service import (
    claim_location,
    create_or_get_merchant_account,
    create_setup_intent,
    get_valid_access_token,
    link_location_to_merchant,
    store_oauth_state,
    store_oauth_tokens,
    update_placement_rule,
    validate_oauth_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchant", tags=["merchant_onboarding"])


@router.post(
    "/auth/google/start",
    response_model=GoogleAuthStartResponse,
    summary="Start Google Business Profile OAuth",
)
async def start_google_auth(
    db: Session = Depends(get_db),
):
    """
    Start Google OAuth flow. No auth required — this IS the login.
    Returns auth_url to redirect the user to Google consent screen.
    """
    try:
        state = secrets.token_urlsafe(32)
        redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI or f"{settings.MERCHANT_PORTAL_URL}/auth/google/callback"
        store_oauth_state(state, {"redirect_uri": redirect_uri})

        auth_url = get_oauth_authorize_url(state, redirect_uri)

        return GoogleAuthStartResponse(
            auth_url=auth_url,
            state=state,
        )
    except Exception as e:
        logger.error(f"Error starting Google OAuth: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start Google OAuth",
        )


@router.get(
    "/auth/google/callback",
    summary="Handle Google OAuth callback",
)
async def google_auth_callback(
    code: str = Query(..., description="OAuth authorization code"),
    state: str = Query(..., description="OAuth state token"),
    db: Session = Depends(get_db),
):
    """
    Handle Google OAuth callback.
    Exchanges code for tokens, creates/finds user, creates merchant account,
    stores encrypted tokens, and returns JWT.
    """
    try:
        # Validate state (best-effort — in-memory store doesn't survive across instances)
        state_data = validate_oauth_state(state)
        if not state_data:
            logger.warning("OAuth state not found (likely cross-instance). Proceeding with config redirect_uri.")

        redirect_uri = (state_data or {}).get("redirect_uri", "")
        if not redirect_uri:
            redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI or f"{settings.MERCHANT_PORTAL_URL}/auth/google/callback"

        # Exchange code for tokens
        tokens = await exchange_oauth_code(code, redirect_uri)

        # Get user info from Google
        user_info = await get_user_info(tokens["access_token"])
        email = user_info.get("email", "")
        name = user_info.get("name", "")

        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not retrieve email from Google account",
            )

        # Find or create user
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                phone=f"google_{secrets.token_urlsafe(8)}",  # placeholder for phone-first schema
                display_name=name,
                email=email,
                role_flags="merchant_admin",
                auth_provider="google",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created merchant user {user.id} for {email}")
        else:
            # Ensure merchant_admin role
            if user.role_flags and "merchant_admin" not in user.role_flags:
                user.role_flags = f"{user.role_flags},merchant_admin"
                db.commit()

        # Create merchant account
        merchant_account = create_or_get_merchant_account(db, user.id)

        # Store encrypted OAuth tokens
        store_oauth_tokens(db, merchant_account.id, tokens)

        # Issue JWT
        from app.core.security import create_access_token
        access_token = create_access_token(
            subject=user.public_id,
            auth_provider="google",
            role=user.role_flags or "merchant_admin",
        )

        # Create refresh token
        from app.services.refresh_token_service import RefreshTokenService
        refresh_token_str, _ = RefreshTokenService.create_refresh_token(db, user)
        db.commit()

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token_str,
            "merchant_account_id": merchant_account.id,
            "user_email": email,
            "user_name": name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling Google OAuth callback: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to handle Google OAuth callback",
        )


@router.get(
    "/locations",
    response_model=LocationsListResponse,
    summary="List Google Business Profile locations",
)
async def list_merchant_locations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List available locations from the merchant's Google Business Profile."""
    try:
        merchant_account = create_or_get_merchant_account(db, current_user.id)

        # Get valid access token (auto-refreshes if expired)
        access_token = await get_valid_access_token(db, merchant_account.id)

        if not access_token and not settings.MERCHANT_AUTH_MOCK:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth not completed. Please complete Google OAuth flow first.",
            )

        if settings.MERCHANT_AUTH_MOCK and not access_token:
            access_token = "mock_token"

        locations_data = await list_locations(access_token)

        locations = [
            LocationSummary(
                location_id=loc.get("location_id", ""),
                name=loc.get("name", ""),
                address=loc.get("address", ""),
                place_id=loc.get("place_id"),
            )
            for loc in locations_data
        ]

        return LocationsListResponse(locations=locations)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"GBP locations unavailable (falling back to search): {e}")
        # Return empty list so frontend falls back to Places search
        return LocationsListResponse(locations=[])


@router.post(
    "/claim",
    response_model=ClaimLocationResponse,
    summary="Claim a location",
)
async def claim_location_endpoint(
    request: ClaimLocationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Claim a Google Place location and link it to the merchant's DomainMerchant record."""
    try:
        merchant_account = create_or_get_merchant_account(db, current_user.id)

        # Claim in MerchantLocationClaim table
        claim_record = claim_location(
            db=db,
            merchant_account_id=merchant_account.id,
            place_id=request.place_id,
        )

        # Also create/update DomainMerchant record
        name = request.name or ""
        address = request.address or ""
        domain_merchant = link_location_to_merchant(
            db=db,
            user_id=current_user.id,
            place_id=request.place_id,
            name=name,
            address=address,
        )

        return ClaimLocationResponse(
            claim_id=claim_record.id,
            place_id=claim_record.place_id,
            status=claim_record.status,
            merchant_id=str(domain_merchant.id) if domain_merchant else None,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error claiming location: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to claim location",
        )


@router.post(
    "/billing/setup_intent",
    response_model=SetupIntentResponse,
    summary="Create Stripe SetupIntent",
)
async def create_setup_intent_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create Stripe SetupIntent for card-on-file."""
    try:
        merchant_account = create_or_get_merchant_account(db, current_user.id)
        result = create_setup_intent(db, merchant_account.id)
        return SetupIntentResponse(
            client_secret=result["client_secret"],
            setup_intent_id=result["setup_intent_id"],
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error creating SetupIntent: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create SetupIntent",
        )


@router.post(
    "/placement/update",
    response_model=UpdatePlacementResponse,
    summary="Update placement rules",
)
async def update_placement_endpoint(
    request: UpdatePlacementRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update placement rules for a location."""
    try:
        merchant_account = create_or_get_merchant_account(db, current_user.id)
        rule = update_placement_rule(
            db=db,
            merchant_account_id=merchant_account.id,
            place_id=request.place_id,
            daily_cap_cents=request.daily_cap_cents,
            boost_weight=request.boost_weight,
            perks_enabled=request.perks_enabled,
        )
        return UpdatePlacementResponse(
            rule_id=rule.id,
            place_id=rule.place_id,
            status=rule.status,
            daily_cap_cents=rule.daily_cap_cents,
            boost_weight=rule.boost_weight,
            perks_enabled=rule.perks_enabled,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error updating placement: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update placement",
        )
