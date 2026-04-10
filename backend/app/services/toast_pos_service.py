"""
Toast POS Integration Service

Handles OAuth flow, token management, and read-only order data for Toast POS.
Supports TOAST_MOCK_MODE=true for development without Toast partner API access.

Uses MerchantOAuthToken model with provider="toast" for token storage.
"""
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.token_encryption import decrypt_token, encrypt_token
from app.models.merchant_oauth_token import MerchantOAuthToken

logger = logging.getLogger(__name__)

# Toast API URLs
TOAST_API_BASE = "https://ws-api.toasttab.com"
TOAST_OAUTH_AUTHORIZE_URL = f"{TOAST_API_BASE}/usermgmt/v1/oauth/authorize"
TOAST_TOKEN_URL = f"{TOAST_API_BASE}/authentication/v1/authentication/login"

# Toast config from environment
TOAST_CLIENT_ID = getattr(settings, "TOAST_CLIENT_ID", "") or ""
TOAST_CLIENT_SECRET = getattr(settings, "TOAST_CLIENT_SECRET", "") or ""
TOAST_MOCK_MODE = getattr(settings, "TOAST_MOCK_MODE", True)


def _is_mock_mode() -> bool:
    """Check if Toast mock mode is active."""
    return TOAST_MOCK_MODE


# ---------------------------------------------------------------------------
# OAuth state management (in-memory fallback if DB table not yet migrated)
# ---------------------------------------------------------------------------

_oauth_states: Dict[str, Dict] = {}


def store_oauth_state(db: Session, state: str, data: Optional[Dict] = None) -> None:
    """Store OAuth state token for CSRF protection."""
    try:
        from app.models.merchant_oauth_token import PosOAuthState
        PosOAuthState.store(db, state, data or {}, ttl_minutes=10)
    except Exception:
        # Fallback to in-memory if pos_oauth_states table doesn't exist yet
        _oauth_states[state] = {**(data or {}), "created_at": datetime.utcnow()}


def validate_oauth_state(db: Session, state: str) -> Optional[Dict]:
    """Validate and consume an OAuth state token. Returns stored data or None."""
    try:
        from app.models.merchant_oauth_token import PosOAuthState
        return PosOAuthState.pop(db, state)
    except Exception:
        # Fallback to in-memory
        state_data = _oauth_states.pop(state, None)
        if not state_data:
            return None
        if (datetime.utcnow() - state_data.get("created_at", datetime.utcnow())).total_seconds() > 600:
            return None
        return state_data


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def get_auth_url(db: Session, merchant_account_id: str, redirect_uri: str) -> Dict[str, str]:
    """
    Generate Toast OAuth authorization URL and state token.

    Returns:
        {"auth_url": "...", "state": "..."}
    """
    state = secrets.token_urlsafe(32)
    store_oauth_state(db, state, {"merchant_account_id": merchant_account_id})

    if _is_mock_mode():
        # In mock mode, redirect directly to callback with a fake code
        mock_code = f"mock_toast_code_{secrets.token_urlsafe(16)}"
        auth_url = f"{redirect_uri}?code={mock_code}&state={state}"
        return {"auth_url": auth_url, "state": state}

    params = {
        "client_id": TOAST_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "orders.read restaurants.read",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"{TOAST_OAUTH_AUTHORIZE_URL}?{query}"
    return {"auth_url": auth_url, "state": state}


async def exchange_code(
    db: Session,
    code: str,
    state: str,
    redirect_uri: str,
    merchant_account_id_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Exchange authorization code for tokens and store encrypted in DB.

    Returns:
        {"connected": True, "restaurant_name": "...", "restaurant_guid": "..."}

    Raises:
        ValueError on invalid state or API error.
    """
    state_data = validate_oauth_state(db, state)

    if _is_mock_mode():
        # In mock mode, state validation may fail across multi-instance deploys.
        # Use override from the authenticated user if state data is missing.
        merchant_account_id = (state_data or {}).get("merchant_account_id", "") or merchant_account_id_override or ""
    elif not state_data:
        raise ValueError("Invalid or expired OAuth state")
    else:
        merchant_account_id = state_data["merchant_account_id"]

    if _is_mock_mode():
        # Store mock credentials
        _upsert_toast_token(
            db,
            merchant_account_id=merchant_account_id,
            access_token=f"mock_toast_access_{secrets.token_urlsafe(16)}",
            refresh_token=f"mock_toast_refresh_{secrets.token_urlsafe(16)}",
            expires_in=86400,
            restaurant_guid=f"mock-guid-{secrets.token_urlsafe(8)}",
        )
        return {
            "connected": True,
            "restaurant_name": "Mock Toast Restaurant",
            "restaurant_guid": f"mock-guid-{secrets.token_urlsafe(8)}",
        }

    # Real Toast token exchange
    if not TOAST_CLIENT_ID or not TOAST_CLIENT_SECRET:
        raise ValueError("Toast API credentials not configured (TOAST_CLIENT_ID / TOAST_CLIENT_SECRET)")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOAST_TOKEN_URL,
            json={
                "clientId": TOAST_CLIENT_ID,
                "clientSecret": TOAST_CLIENT_SECRET,
                "userAccessType": "TOAST_MACHINE_CLIENT",
                "code": code,
                "redirectUri": redirect_uri,
            },
        )

        if response.status_code != 200:
            logger.error(f"Toast token exchange failed: {response.status_code} {response.text}")
            raise ValueError(f"Toast token exchange failed (HTTP {response.status_code})")

        token_data = response.json()

    access_token = token_data.get("accessToken") or token_data.get("access_token", "")
    refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token", "")
    expires_in = int(token_data.get("expiresIn", token_data.get("expires_in", 86400)))

    if not access_token:
        raise ValueError("No access token in Toast response")

    # Get restaurant info using the new token
    restaurant_guid = token_data.get("restaurantGuid") or token_data.get("restaurant_guid", "")
    restaurant_name = "Unknown"

    if restaurant_guid:
        try:
            info = await _fetch_restaurant_info(access_token, restaurant_guid)
            restaurant_name = info.get("name", "Unknown")
        except Exception as e:
            logger.warning(f"Could not fetch restaurant info after token exchange: {e}")

    _upsert_toast_token(
        db,
        merchant_account_id=merchant_account_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        restaurant_guid=restaurant_guid,
    )

    return {
        "connected": True,
        "restaurant_name": restaurant_name,
        "restaurant_guid": restaurant_guid,
    }


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _upsert_toast_token(
    db: Session,
    merchant_account_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    restaurant_guid: str = "",
) -> MerchantOAuthToken:
    """Create or update the Toast OAuth token row for a merchant."""
    existing = (
        db.query(MerchantOAuthToken)
        .filter(
            MerchantOAuthToken.merchant_account_id == merchant_account_id,
            MerchantOAuthToken.provider == "toast",
        )
        .first()
    )

    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)

    if existing:
        existing.access_token_encrypted = encrypt_token(access_token)
        if refresh_token:
            existing.refresh_token_encrypted = encrypt_token(refresh_token)
        existing.token_expiry = token_expiry
        existing.scopes = "orders.read restaurants.read"
        if restaurant_guid:
            existing.gbp_account_id = restaurant_guid  # reuse column for Toast restaurant GUID
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    oauth_token = MerchantOAuthToken(
        id=str(uuid.uuid4()),
        merchant_account_id=merchant_account_id,
        provider="toast",
        access_token_encrypted=encrypt_token(access_token),
        refresh_token_encrypted=encrypt_token(refresh_token) if refresh_token else None,
        token_expiry=token_expiry,
        scopes="orders.read restaurants.read",
        gbp_account_id=restaurant_guid,  # reuse column for Toast restaurant GUID
    )
    db.add(oauth_token)
    db.commit()
    db.refresh(oauth_token)
    return oauth_token


def _get_toast_token(db: Session, merchant_account_id: str) -> Optional[MerchantOAuthToken]:
    """Get the Toast OAuth token row for a merchant."""
    return (
        db.query(MerchantOAuthToken)
        .filter(
            MerchantOAuthToken.merchant_account_id == merchant_account_id,
            MerchantOAuthToken.provider == "toast",
        )
        .first()
    )


async def refresh_token_if_needed(db: Session, merchant_account_id: str) -> Optional[str]:
    """
    Return a valid decrypted access token, refreshing if expired.
    Returns None if no token stored or refresh fails.
    """
    token_row = _get_toast_token(db, merchant_account_id)
    if not token_row or not token_row.access_token_encrypted:
        return None

    # If still valid (with 5-minute buffer), return it
    if token_row.token_expiry and token_row.token_expiry > datetime.utcnow() + timedelta(minutes=5):
        return decrypt_token(token_row.access_token_encrypted)

    # Need refresh
    if not token_row.refresh_token_encrypted:
        logger.warning(f"No Toast refresh token for merchant {merchant_account_id}")
        return None

    if _is_mock_mode():
        # Mock: just issue a new fake token
        new_access = f"mock_toast_access_{secrets.token_urlsafe(16)}"
        token_row.access_token_encrypted = encrypt_token(new_access)
        token_row.token_expiry = datetime.utcnow() + timedelta(hours=24)
        token_row.updated_at = datetime.utcnow()
        db.commit()
        return new_access

    refresh_tok = decrypt_token(token_row.refresh_token_encrypted)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                TOAST_TOKEN_URL,
                json={
                    "clientId": TOAST_CLIENT_ID,
                    "clientSecret": TOAST_CLIENT_SECRET,
                    "userAccessType": "TOAST_MACHINE_CLIENT",
                    "refreshToken": refresh_tok,
                },
            )
            if response.status_code != 200:
                logger.error(f"Toast token refresh failed: {response.status_code} {response.text}")
                return None

            data = response.json()

        new_access = data.get("accessToken") or data.get("access_token", "")
        new_refresh = data.get("refreshToken") or data.get("refresh_token", "")
        expires_in = int(data.get("expiresIn", data.get("expires_in", 86400)))

        token_row.access_token_encrypted = encrypt_token(new_access)
        if new_refresh:
            token_row.refresh_token_encrypted = encrypt_token(new_refresh)
        token_row.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        token_row.updated_at = datetime.utcnow()
        db.commit()

        return new_access
    except Exception as e:
        logger.error(f"Toast token refresh error for merchant {merchant_account_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# Restaurant info
# ---------------------------------------------------------------------------

async def _fetch_restaurant_info(access_token: str, restaurant_guid: str) -> Dict[str, Any]:
    """Fetch restaurant details from Toast API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{TOAST_API_BASE}/restaurants/v1/restaurants/{restaurant_guid}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Toast-Restaurant-External-ID": restaurant_guid,
            },
        )
        response.raise_for_status()
        return response.json()


async def get_restaurant_info(db: Session, merchant_account_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch restaurant name and GUID for a connected Toast merchant.
    Returns None if not connected.
    """
    token_row = _get_toast_token(db, merchant_account_id)
    if not token_row:
        return None

    restaurant_guid = token_row.gbp_account_id or ""

    if _is_mock_mode():
        return {
            "restaurant_guid": restaurant_guid or "mock-guid-12345",
            "name": "Mock Toast Restaurant",
            "location": "123 Main St, Austin, TX",
        }

    access_token = await refresh_token_if_needed(db, merchant_account_id)
    if not access_token or not restaurant_guid:
        return None

    try:
        info = await _fetch_restaurant_info(access_token, restaurant_guid)
        return {
            "restaurant_guid": restaurant_guid,
            "name": info.get("restaurantName", info.get("name", "Unknown")),
            "location": info.get("location", {}).get("address1", ""),
        }
    except Exception as e:
        logger.error(f"Failed to fetch Toast restaurant info: {e}")
        return None


# ---------------------------------------------------------------------------
# Order data and AOV
# ---------------------------------------------------------------------------

async def get_recent_orders(
    db: Session,
    merchant_account_id: str,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    Fetch recent orders from Toast API for AOV calculation.
    Returns list of order dicts with total, timestamp, etc.
    """
    if _is_mock_mode():
        return _generate_mock_orders(days)

    access_token = await refresh_token_if_needed(db, merchant_account_id)
    if not access_token:
        return []

    token_row = _get_toast_token(db, merchant_account_id)
    restaurant_guid = token_row.gbp_account_id if token_row else ""
    if not restaurant_guid:
        return []

    # Toast Orders API uses businessDate format (YYYYMMDD)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    orders = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Paginate through orders day by day (Toast API returns by business date)
            current = start_date
            while current <= end_date:
                biz_date = current.strftime("%Y%m%d")
                response = await client.get(
                    f"{TOAST_API_BASE}/orders/v2/orders",
                    params={"businessDate": biz_date, "pageSize": 100},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Toast-Restaurant-External-ID": restaurant_guid,
                    },
                )
                if response.status_code == 200:
                    page_orders = response.json()
                    if isinstance(page_orders, list):
                        for order in page_orders:
                            total = order.get("totalAmount", order.get("amount", 0))
                            orders.append({
                                "order_id": order.get("guid", ""),
                                "total_cents": int(float(total) * 100) if total else 0,
                                "timestamp": order.get("closedDate") or order.get("createdDate", ""),
                                "checks_count": len(order.get("checks", [])),
                            })
                elif response.status_code == 429:
                    logger.warning("Toast API rate limited during order fetch")
                    break
                else:
                    logger.warning(f"Toast orders API returned {response.status_code} for date {biz_date}")

                current += timedelta(days=1)

    except Exception as e:
        logger.error(f"Failed to fetch Toast orders: {e}")

    return orders


def _generate_mock_orders(days: int) -> List[Dict[str, Any]]:
    """Generate realistic mock order data for development."""
    import random
    orders = []
    now = datetime.utcnow()
    # ~15-40 orders per day for a typical restaurant
    for day_offset in range(days):
        date = now - timedelta(days=day_offset)
        num_orders = random.randint(15, 40)
        for i in range(num_orders):
            # Realistic total: $8-$65 with most in $15-$35 range
            total_cents = random.choice([
                random.randint(800, 1500),    # light meals
                random.randint(1500, 3500),   # typical meals (weighted more)
                random.randint(1500, 3500),
                random.randint(1500, 3500),
                random.randint(3500, 6500),   # larger orders
            ])
            orders.append({
                "order_id": f"mock-order-{day_offset}-{i}",
                "total_cents": total_cents,
                "timestamp": (date - timedelta(hours=random.randint(0, 14))).isoformat(),
                "checks_count": random.randint(1, 3),
            })
    return orders


async def calculate_aov(db: Session, merchant_account_id: str, days: int = 30) -> Optional[Dict[str, Any]]:
    """
    Calculate average order value from recent Toast orders.

    Returns:
        {"aov_cents": 3850, "order_count": 420, "period_days": 30, "source": "toast"}
        or None if no data available.
    """
    orders = await get_recent_orders(db, merchant_account_id, days=days)
    if not orders:
        return None

    totals = [o["total_cents"] for o in orders if o.get("total_cents", 0) > 0]
    if not totals:
        return None

    aov_cents = int(sum(totals) / len(totals))
    return {
        "aov_cents": aov_cents,
        "order_count": len(totals),
        "period_days": days,
        "source": "toast",
    }


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------

def disconnect(db: Session, merchant_account_id: str) -> bool:
    """
    Remove stored Toast credentials for a merchant.
    Returns True if credentials were found and deleted.
    """
    token_row = _get_toast_token(db, merchant_account_id)
    if not token_row:
        return False

    db.delete(token_row)
    db.commit()
    logger.info(f"Disconnected Toast POS for merchant account {merchant_account_id}")
    return True
