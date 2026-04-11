"""
Merchant Onboarding Service

Business logic for merchant onboarding, OAuth token management, location claims, and placement rules.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.token_encryption import decrypt_token, encrypt_token
from app.models import (
    DomainMerchant,
    MerchantAccount,
    MerchantLocationClaim,
    MerchantPaymentMethod,
    MerchantPlacementRule,
)
from app.models.merchant_oauth_token import MerchantOAuthToken
from app.services.google_business_profile import refresh_access_token

logger = logging.getLogger(__name__)

# In-memory state for OAuth (fallback when DB not available)
_oauth_states: Dict[str, Dict] = {}


def create_or_get_merchant_account(db: Session, user_id: int) -> MerchantAccount:
    """Create or get merchant account for a user."""
    account = (
        db.query(MerchantAccount)
        .filter(MerchantAccount.owner_user_id == user_id)
        .first()
    )

    if not account:
        account = MerchantAccount(
            id=str(uuid.uuid4()),
            owner_user_id=user_id,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        logger.info(f"Created merchant account {account.id} for user {user_id}")

    return account


def store_oauth_state(state: str, data: Optional[Dict] = None) -> None:
    """
    Store OAuth state for CSRF protection.
    Uses in-memory dict. For production, consider Redis or DB-backed store.
    """
    _oauth_states[state] = {
        **(data or {}),
        "created_at": datetime.utcnow(),
    }


def validate_oauth_state(state: str) -> Optional[Dict]:
    """
    Validate OAuth state and return associated data.
    Returns dict with stored data if valid, None otherwise.
    """
    state_data = _oauth_states.pop(state, None)
    if not state_data:
        return None

    # Expire after 10 minutes
    if (datetime.utcnow() - state_data["created_at"]).total_seconds() > 600:
        return None

    return state_data


def store_oauth_tokens(
    db: Session,
    merchant_account_id: str,
    tokens: Dict[str, str],
    gbp_account_id: Optional[str] = None,
) -> MerchantOAuthToken:
    """Encrypt and store OAuth tokens for a merchant account."""
    existing = (
        db.query(MerchantOAuthToken)
        .filter(
            MerchantOAuthToken.merchant_account_id == merchant_account_id,
            MerchantOAuthToken.provider == "google_gbp",
        )
        .first()
    )

    expires_in = int(tokens.get("expires_in", 3600))
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

    if existing:
        existing.access_token_encrypted = encrypt_token(tokens["access_token"])
        if tokens.get("refresh_token"):
            existing.refresh_token_encrypted = encrypt_token(tokens["refresh_token"])
        existing.token_expiry = token_expiry
        existing.scopes = tokens.get("scope", "")
        if gbp_account_id:
            existing.gbp_account_id = gbp_account_id
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    oauth_token = MerchantOAuthToken(
        id=str(uuid.uuid4()),
        merchant_account_id=merchant_account_id,
        provider="google_gbp",
        access_token_encrypted=encrypt_token(tokens["access_token"]),
        refresh_token_encrypted=encrypt_token(tokens.get("refresh_token", "")) if tokens.get("refresh_token") else None,
        token_expiry=token_expiry,
        scopes=tokens.get("scope", ""),
        gbp_account_id=gbp_account_id,
    )
    db.add(oauth_token)
    db.commit()
    db.refresh(oauth_token)
    return oauth_token


async def get_valid_access_token(db: Session, merchant_account_id: str) -> Optional[str]:
    """
    Get a valid access token for the merchant, auto-refreshing if expired.
    Returns decrypted access token or None.
    """
    oauth_token = (
        db.query(MerchantOAuthToken)
        .filter(
            MerchantOAuthToken.merchant_account_id == merchant_account_id,
            MerchantOAuthToken.provider == "google_gbp",
        )
        .first()
    )

    if not oauth_token or not oauth_token.access_token_encrypted:
        return None

    # Check if token is still valid (with 5-minute buffer)
    if oauth_token.token_expiry and oauth_token.token_expiry > datetime.utcnow() + timedelta(minutes=5):
        return decrypt_token(oauth_token.access_token_encrypted)

    # Token expired — attempt refresh
    if not oauth_token.refresh_token_encrypted:
        logger.warning(f"No refresh token for merchant {merchant_account_id}")
        return None

    try:
        refresh_tok = decrypt_token(oauth_token.refresh_token_encrypted)
        new_tokens = await refresh_access_token(refresh_tok)

        # Update stored tokens
        oauth_token.access_token_encrypted = encrypt_token(new_tokens["access_token"])
        expires_in = int(new_tokens.get("expires_in", 3600))
        oauth_token.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        oauth_token.updated_at = datetime.utcnow()
        db.commit()

        return new_tokens["access_token"]
    except Exception as e:
        logger.error(f"Failed to refresh token for merchant {merchant_account_id}: {e}")
        return None


def link_location_to_merchant(
    db: Session,
    user_id: int,
    place_id: str,
    name: str,
    address: str = "",
    lat: float = 0.0,
    lng: float = 0.0,
) -> DomainMerchant:
    """
    Upsert a DomainMerchant record for the claimed location.
    Sets owner_user_id, google_place_id, and status=active.
    """
    # Check if merchant already exists for this place_id
    existing = (
        db.query(DomainMerchant)
        .filter(DomainMerchant.google_place_id == place_id)
        .first()
    )

    if existing:
        existing.owner_user_id = user_id
        existing.status = "active"
        if name:
            existing.name = name
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    # Check if user already owns a merchant — reuse it with new place_id
    user_merchant = (
        db.query(DomainMerchant)
        .filter(DomainMerchant.owner_user_id == user_id, DomainMerchant.status == "active")
        .first()
    )
    if user_merchant:
        user_merchant.google_place_id = place_id
        if name:
            user_merchant.name = name
        if address:
            addr_parts = [p.strip() for p in address.split(",")] if address else []
            user_merchant.addr_line1 = addr_parts[0] if len(addr_parts) > 0 else ""
            user_merchant.city = addr_parts[1] if len(addr_parts) > 1 else ""
            user_merchant.state = addr_parts[2] if len(addr_parts) > 2 else ""
            user_merchant.postal_code = addr_parts[3] if len(addr_parts) > 3 else ""
        user_merchant.lat = lat
        user_merchant.lng = lng
        user_merchant.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(user_merchant)
        logger.info(f"Updated existing merchant {user_merchant.id} with new place_id {place_id} for user {user_id}")
        return user_merchant

    # Parse address components
    addr_parts = [p.strip() for p in address.split(",")] if address else []

    merchant = DomainMerchant(
        id=str(uuid.uuid4()),
        name=name,
        google_place_id=place_id,
        addr_line1=addr_parts[0] if len(addr_parts) > 0 else "",
        city=addr_parts[1] if len(addr_parts) > 1 else "",
        state=addr_parts[2] if len(addr_parts) > 2 else "",
        postal_code=addr_parts[3] if len(addr_parts) > 3 else "",
        lat=lat,
        lng=lng,
        owner_user_id=user_id,
        status="active",
        zone_slug="default",
    )
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    logger.info(f"Linked location {place_id} to merchant {merchant.id} for user {user_id}")
    return merchant


def claim_location(
    db: Session,
    merchant_account_id: str,
    place_id: str,
) -> MerchantLocationClaim:
    """Claim a Google Place location for a merchant account."""
    existing = (
        db.query(MerchantLocationClaim)
        .filter(
            MerchantLocationClaim.merchant_account_id == merchant_account_id,
            MerchantLocationClaim.place_id == place_id,
        )
        .first()
    )

    if existing:
        return existing

    claim = MerchantLocationClaim(
        id=str(uuid.uuid4()),
        merchant_account_id=merchant_account_id,
        place_id=place_id,
        status="CLAIMED",
    )

    db.add(claim)
    db.commit()
    db.refresh(claim)

    logger.info(f"Claimed location {place_id} for merchant account {merchant_account_id}")
    return claim


def create_setup_intent(
    db: Session,
    merchant_account_id: str,
) -> Dict[str, str]:
    """Create Stripe SetupIntent for card-on-file collection."""
    if not settings.STRIPE_SECRET_KEY:
        raise ValueError("Stripe not configured (STRIPE_SECRET_KEY missing)")

    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY
    except ImportError:
        raise ValueError("Stripe package not installed")

    merchant_account = (
        db.query(MerchantAccount)
        .filter(MerchantAccount.id == merchant_account_id)
        .first()
    )

    if not merchant_account:
        raise ValueError(f"Merchant account {merchant_account_id} not found")

    existing_payment = (
        db.query(MerchantPaymentMethod)
        .filter(
            MerchantPaymentMethod.merchant_account_id == merchant_account_id,
            MerchantPaymentMethod.status == "ACTIVE",
        )
        .first()
    )

    stripe_customer_id = None
    if existing_payment:
        stripe_customer_id = existing_payment.stripe_customer_id
    else:
        customer = stripe.Customer.create(
            metadata={
                "merchant_account_id": merchant_account_id,
                "user_id": str(merchant_account.owner_user_id),
            }
        )
        stripe_customer_id = customer.id

    setup_intent = stripe.SetupIntent.create(
        customer=stripe_customer_id,
        payment_method_types=["card"],
        usage="off_session",
        metadata={
            "merchant_account_id": merchant_account_id,
        }
    )

    return {
        "client_secret": setup_intent.client_secret,
        "setup_intent_id": setup_intent.id,
        "stripe_customer_id": stripe_customer_id,
    }


def update_placement_rule(
    db: Session,
    merchant_account_id: str,
    place_id: str,
    daily_cap_cents: Optional[int] = None,
    boost_weight: Optional[float] = None,
    perks_enabled: Optional[bool] = None,
) -> MerchantPlacementRule:
    """Update placement rule for a location."""
    claim = (
        db.query(MerchantLocationClaim)
        .filter(
            MerchantLocationClaim.merchant_account_id == merchant_account_id,
            MerchantLocationClaim.place_id == place_id,
            MerchantLocationClaim.status == "CLAIMED",
        )
        .first()
    )

    if not claim:
        raise ValueError(f"Location {place_id} not claimed by merchant account {merchant_account_id}")

    payment_method = (
        db.query(MerchantPaymentMethod)
        .filter(
            MerchantPaymentMethod.merchant_account_id == merchant_account_id,
            MerchantPaymentMethod.status == "ACTIVE",
        )
        .first()
    )

    if not payment_method:
        raise ValueError("Active payment method required for placement rules")

    rule = (
        db.query(MerchantPlacementRule)
        .filter(MerchantPlacementRule.place_id == place_id)
        .first()
    )

    if not rule:
        rule = MerchantPlacementRule(
            id=str(uuid.uuid4()),
            place_id=place_id,
            status="ACTIVE",
            daily_cap_cents=daily_cap_cents or 0,
            boost_weight=boost_weight or 0.0,
            perks_enabled=perks_enabled or False,
        )
        db.add(rule)
    else:
        if daily_cap_cents is not None:
            rule.daily_cap_cents = daily_cap_cents
        if boost_weight is not None:
            rule.boost_weight = boost_weight
        if perks_enabled is not None:
            rule.perks_enabled = perks_enabled
        rule.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(rule)

    logger.info(
        f"Updated placement rule for {place_id}: "
        f"cap={rule.daily_cap_cents}, boost={rule.boost_weight}, perks={rule.perks_enabled}"
    )

    return rule
