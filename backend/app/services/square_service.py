"""
Square Service - Square OAuth and merchant data fetching
Handles Square OAuth flow and fetching merchant location stats (AOV).
All HTTP calls are mockable for tests.
"""

import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import NamedTuple, Optional
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import get_square_sandbox_config, settings
from ..models.domain import DomainMerchant, SquareOAuthState
from .token_encryption import TokenDecryptionError, decrypt_token

logger = logging.getLogger(__name__)


class SquareCreds(NamedTuple):
    """Square credentials for a specific environment"""

    application_id: str
    application_secret: str
    redirect_url: str
    base_url: str
    auth_base_url: str


class SquareOAuthResult(BaseModel):
    """Result from Square OAuth token exchange"""

    merchant_id: str
    location_id: str
    access_token: str


class SquareLocationStats(BaseModel):
    """Location stats from Square (for AOV calculation)"""

    avg_order_value_cents: int


def get_square_creds() -> SquareCreds:
    """
    Get Square credentials based on SQUARE_ENV setting.

    Returns:
        SquareCreds with application_id, application_secret, redirect_url, and base URLs

    Raises:
        ValueError: If credentials are not configured for the selected environment
    """
    is_prod = settings.square_env == "production"

    if is_prod:
        app_id = settings.square_application_id_production or settings.square_application_id
        app_secret = (
            settings.square_application_secret_production or settings.square_application_secret
        )
        redirect_url = settings.square_redirect_url_production or settings.square_redirect_url
        base_url = "https://connect.squareup.com"
        auth_base_url = "https://squareup.com"
    else:
        app_id = settings.square_application_id_sandbox or settings.square_application_id
        app_secret = (
            settings.square_application_secret_sandbox or settings.square_application_secret
        )
        redirect_url = settings.square_redirect_url_sandbox or settings.square_redirect_url
        base_url = "https://connect.squareupsandbox.com"
        auth_base_url = "https://squareupsandbox.com"

    if not app_id:
        env_name = "production" if is_prod else "sandbox"
        raise ValueError(f"SQUARE_APPLICATION_ID_{env_name.upper()} not configured")
    if not app_secret:
        env_name = "production" if is_prod else "sandbox"
        raise ValueError(f"SQUARE_APPLICATION_SECRET_{env_name.upper()} not configured")
    if not redirect_url:
        env_name = "production" if is_prod else "sandbox"
        raise ValueError(f"SQUARE_REDIRECT_URL_{env_name.upper()} not configured")

    return SquareCreds(
        application_id=app_id,
        application_secret=app_secret,
        redirect_url=redirect_url,
        base_url=base_url,
        auth_base_url=auth_base_url,
    )


async def get_square_oauth_authorize_url(state: str) -> str:
    """
    Build the Square OAuth authorization URL (SANDBOX ONLY).

    Args:
        state: OAuth state parameter for CSRF protection

    Returns:
        Authorization URL for Square OAuth (sandbox)

    Raises:
        ValueError: If Square configuration is missing
    """
    cfg = get_square_sandbox_config()

    scopes = ["MERCHANT_PROFILE_READ", "ORDERS_READ", "PAYMENTS_READ"]

    query_params = {
        "client_id": cfg["application_id"],
        "response_type": "code",
        "scope": " ".join(scopes),
        "redirect_uri": cfg["redirect_url"],
        "state": state,
    }

    # Use auth_base_url for OAuth authorize page (squareupsandbox.com, not connect.squareupsandbox.com)
    auth_base = cfg.get("auth_base_url", cfg["base_url"])
    return f"{auth_base}/oauth2/authorize?" + urlencode(query_params)


async def exchange_square_oauth_code(code: str) -> SquareOAuthResult:
    """
    Exchange Square OAuth authorization code for access token (SANDBOX ONLY).

    Args:
        code: OAuth authorization code from callback

    Returns:
        SquareOAuthResult with merchant_id, location_id, and access_token

    Raises:
        ValueError: If configuration is missing or OAuth exchange fails
        HTTPException: If Square API returns an error
    """
    cfg = get_square_sandbox_config()
    token_url = f'{cfg["base_url"]}/oauth2/token'

    # Square OAuth token request
    # Note: redirect_uri must match the one used in the authorization request
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            token_url,
            json={
                "client_id": cfg["application_id"],
                "client_secret": cfg["application_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": cfg["redirect_url"],
            },
            headers={
                "Square-Version": "2023-10-18",  # Use a recent Square API version
                "Content-Type": "application/json",
            },
        )

        if response.status_code != 200:
            error_detail = response.text
            try:
                error_json = response.json()
                error_message = error_json.get("message", error_json.get("errors", error_detail))
                logger.error(
                    f"Square OAuth token exchange failed: {response.status_code} - {error_message}"
                )
            except:
                logger.error(
                    f"Square OAuth token exchange failed: {response.status_code} - {redact_token_for_log(error_detail)}"
                )
            raise ValueError(
                f"Square OAuth exchange failed: {response.status_code} - {error_detail[:200]}"
            )

        data = response.json()

        # Extract merchant_id and location_id from the response
        # Square OAuth response structure:
        # {
        #   "access_token": "...",
        #   "token_type": "bearer",
        #   "expires_at": "...",
        #   "merchant_id": "...",
        #   "refresh_token": "..."
        # }
        # Note: location_id may need to be fetched separately via Locations API

        access_token = data.get("access_token")
        merchant_id = data.get("merchant_id")

        if not access_token or not merchant_id:
            raise ValueError("Square OAuth response missing required fields")

        # Fetch location_id from Locations API (use first location)
        location_id = await _fetch_primary_location_id(access_token, cfg["base_url"])

        return SquareOAuthResult(
            merchant_id=merchant_id, location_id=location_id, access_token=access_token
        )


async def _fetch_primary_location_id(access_token: str, base_url: str) -> str:
    """
    Fetch the primary location ID for a Square merchant.

    Args:
        access_token: Square access token
        base_url: Square API base URL

    Returns:
        Primary location ID

    Raises:
        ValueError: If no locations found or API call fails
    """
    locations_url = f"{base_url}/v2/locations"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            locations_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Square-Version": "2023-10-18",
            },
        )

        if response.status_code != 200:
            error_detail = response.text
            logger.error(f"Square Locations API failed: {response.status_code} - {error_detail}")
            raise ValueError(f"Failed to fetch locations: {response.status_code}")

        data = response.json()
        locations = data.get("locations", [])

        if not locations:
            raise ValueError("No Square locations found for merchant")

        # Return the first location's ID (or could filter by primary location)
        return locations[0]["id"]


async def fetch_square_location_stats(access_token: str, location_id: str) -> SquareLocationStats:
    """
    Fetch location stats from Square to estimate average order value (AOV).

    For now, this uses a simplified approach:
    - If we can fetch recent orders, calculate AOV from them
    - Otherwise, use a default/stub value (e.g., $15 = 1500 cents)

    This is designed to be easily mockable in tests.

    Args:
        access_token: Square access token
        location_id: Square location ID

    Returns:
        SquareLocationStats with avg_order_value_cents
    """
    creds = get_square_creds()
    base_url = creds.base_url

    # Try to fetch recent orders to calculate AOV
    # For MVP, we'll use a simplified approach:
    # 1. Try to fetch last 30 days of orders
    # 2. Calculate average order total
    # 3. Fall back to a default if no orders found

    orders_url = f"{base_url}/v2/orders/search"

    try:
        # Query for orders in the last 30 days
        from datetime import datetime, timedelta

        start_date = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                orders_url,
                json={
                    "location_ids": [location_id],
                    "query": {
                        "filter": {"date_time_filter": {"created_at": {"start_at": start_date}}}
                    },
                    "limit": 100,  # Fetch up to 100 orders for AOV calculation
                },
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Square-Version": "2023-10-18",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                data = response.json()
                orders = data.get("orders", [])

                if orders:
                    # Calculate average order value
                    total_cents = sum(
                        int(order.get("total_money", {}).get("amount", 0)) for order in orders
                    )
                    avg_cents = total_cents // len(orders)

                    # Ensure minimum AOV (e.g., $5 = 500 cents)
                    avg_cents = max(500, avg_cents)

                    logger.info(f"Calculated AOV from {len(orders)} orders: ${avg_cents / 100:.2f}")
                    return SquareLocationStats(avg_order_value_cents=avg_cents)

            # If no orders found or API call failed, use default
            logger.info("No orders found or API call failed, using default AOV")

    except Exception as e:
        logger.warning(f"Failed to fetch Square orders for AOV: {e}, using default")

    # Default AOV: $15 (1500 cents) - reasonable default for many merchants
    # This is still mockable - tests can patch this function
    default_aov_cents = 1500
    return SquareLocationStats(avg_order_value_cents=default_aov_cents)


def get_decrypted_square_token(merchant: DomainMerchant) -> Optional[str]:
    """
    Get decrypted Square access token from merchant.

    This helper function decrypts the stored token for use in Square API calls.
    Use this when you need to make Square API calls with a stored merchant token.

    Args:
        merchant: DomainMerchant instance with encrypted square_access_token

    Returns:
        Decrypted access token, or None if merchant has no token

    Raises:
        TokenDecryptionError: If decryption fails (wrong key, corrupted data, etc.)
    """
    if not merchant.square_access_token:
        return None

    try:
        return decrypt_token(merchant.square_access_token)
    except TokenDecryptionError:
        # Re-raise with context
        raise TokenDecryptionError(
            f"Failed to decrypt Square token for merchant {merchant.id}. "
            "Token may have been encrypted with a different key."
        )


def redact_token_for_log(_: str) -> str:
    """
    Redact token for logging purposes.

    Args:
        _: Token string (ignored)

    Returns:
        Redacted placeholder string
    """
    return "[REDACTED_SQUARE_TOKEN]"


class OAuthStateInvalidError(Exception):
    """Raised when OAuth state validation fails"""

    pass


def create_oauth_state(db: Session) -> str:
    """
    Create and persist an OAuth state for CSRF protection.

    Generates a secure random state token, stores it in the database with expiration,
    and returns it for use in the OAuth flow.

    Args:
        db: Database session

    Returns:
        str: OAuth state token
    """
    # Generate secure random state (32 bytes = 43 chars base64)
    state = secrets.token_urlsafe(32)

    # Set expiration to 15 minutes from now
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    # Create state record
    state_id = str(uuid.uuid4())
    oauth_state = SquareOAuthState(
        id=state_id, state=state, created_at=datetime.utcnow(), expires_at=expires_at, used=False
    )

    db.add(oauth_state)
    db.commit()
    db.refresh(oauth_state)

    logger.info(f"Created OAuth state: {state[:8]}... (expires at {expires_at})")

    return state


def validate_oauth_state(db: Session, state: str) -> None:
    """
    Validate OAuth state and mark as used.

    Checks that:
    - State exists in database
    - State has not expired
    - State has not been used before

    If valid, marks the state as used.

    Args:
        db: Database session
        state: OAuth state token to validate

    Raises:
        OAuthStateInvalidError: If state is invalid, expired, or already used
    """
    if not state:
        raise OAuthStateInvalidError("OAuth state is required")

    # Atomic update: only mark as used if not already used and not expired
    # This prevents race conditions where two callbacks could consume the same state (P0 security fix)
    from sqlalchemy import text

    now = datetime.utcnow()
    result = db.execute(
        text(
            """
        UPDATE square_oauth_states
        SET used = TRUE
        WHERE state = :state
        AND used = FALSE
        AND expires_at > :now
    """
        ),
        {"state": state, "now": now},
    )

    if result.rowcount == 0:
        # State not found, expired, or already used - get details for error message
        oauth_state = db.query(SquareOAuthState).filter(SquareOAuthState.state == state).first()

        if not oauth_state:
            logger.warning(f"OAuth state not found: {state[:8]}...")
            raise OAuthStateInvalidError("OAuth state not found")

        if oauth_state.used:
            logger.warning(f"OAuth state already used: {state[:8]}...")
            raise OAuthStateInvalidError("OAuth state has already been used")

        if oauth_state.expires_at < now:
            logger.warning(
                f"OAuth state expired: {state[:8]}... (expired at {oauth_state.expires_at})"
            )
            raise OAuthStateInvalidError("OAuth state has expired")

        # Should not reach here, but raise generic error
        raise OAuthStateInvalidError("OAuth state validation failed")

    db.commit()
    logger.info(f"Validated and marked OAuth state as used (atomic): {state[:8]}...")
