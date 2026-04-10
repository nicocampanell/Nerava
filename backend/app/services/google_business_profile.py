"""
Google Business Profile OAuth Client

Handles OAuth flow for Google Business Profile API.
Supports mock mode for local development.
"""
import logging
import secrets
from typing import Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Google OAuth configuration
GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_BUSINESS_PROFILE_SCOPE = "https://www.googleapis.com/auth/business.manage"
GOOGLE_OPENID_SCOPES = "openid email profile"

# GBP API base URLs
GBP_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
GBP_LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1"


def get_oauth_authorize_url(state: str, redirect_uri: str) -> str:
    """
    Generate Google OAuth authorization URL.
    Includes openid/email/profile scopes alongside business.manage.
    """
    if settings.MERCHANT_AUTH_MOCK:
        return f"http://localhost:8001/mock-oauth-callback?state={state}"

    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID or settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": f"{GOOGLE_OPENID_SCOPES} {GOOGLE_BUSINESS_PROFILE_SCOPE}" if getattr(settings, 'GOOGLE_GBP_REQUIRED', True) else GOOGLE_OPENID_SCOPES,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    return f"{GOOGLE_AUTH_BASE_URL}?{urlencode(params)}"


async def exchange_oauth_code(code: str, redirect_uri: str) -> Dict[str, str]:
    """
    Exchange OAuth authorization code for access token + refresh token.
    """
    if settings.MERCHANT_AUTH_MOCK:
        return {
            "access_token": f"mock_access_token_{secrets.token_urlsafe(16)}",
            "refresh_token": f"mock_refresh_token_{secrets.token_urlsafe(16)}",
            "expires_in": "3600",
            "token_type": "Bearer",
            "id_token": "mock_id_token",
        }

    client_id = settings.GOOGLE_OAUTH_CLIENT_ID or settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
    if not client_id or not client_secret:
        raise ValueError("Google OAuth not configured (GOOGLE_OAUTH_CLIENT_ID or GOOGLE_OAUTH_CLIENT_SECRET missing)")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        return response.json()


async def refresh_access_token(refresh_token: str) -> Dict[str, str]:
    """Refresh an expired access token using the refresh token."""
    if settings.MERCHANT_AUTH_MOCK:
        return {
            "access_token": f"mock_access_token_{secrets.token_urlsafe(16)}",
            "expires_in": "3600",
            "token_type": "Bearer",
        }

    client_id = settings.GOOGLE_OAUTH_CLIENT_ID or settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET

    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        return response.json()


async def get_user_info(access_token: str) -> Dict[str, str]:
    """Get user email + name from Google userinfo endpoint."""
    if settings.MERCHANT_AUTH_MOCK:
        return {
            "email": "merchant@example.com",
            "name": "Mock Merchant",
            "id": "mock_google_id_123",
        }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


async def list_accounts(access_token: str) -> List[Dict]:
    """List Google Business Profile accounts."""
    if settings.MERCHANT_AUTH_MOCK:
        return [{"name": "accounts/mock_account_1", "accountName": "Mock Business"}]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            GBP_ACCOUNTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("accounts", [])


async def list_locations(access_token: str, account_id: Optional[str] = None) -> List[Dict[str, str]]:
    """
    List Google Business Profile locations for the authenticated merchant.
    If account_id not provided, fetches first account automatically.
    """
    if settings.MERCHANT_AUTH_MOCK:
        return [
            {
                "location_id": "mock_location_1",
                "name": "Mock Coffee Shop",
                "address": "123 Main St, Austin, TX 78701",
                "place_id": "ChIJMockPlace1",
            },
            {
                "location_id": "mock_location_2",
                "name": "Mock Restaurant",
                "address": "456 Oak Ave, Austin, TX 78702",
                "place_id": "ChIJMockPlace2",
            },
        ]

    # If no account_id, get the first account
    if not account_id:
        accounts = await list_accounts(access_token)
        if not accounts:
            return []
        account_id = accounts[0]["name"]  # e.g. "accounts/12345"

    # Ensure account_id has proper format
    if not account_id.startswith("accounts/"):
        account_id = f"accounts/{account_id}"

    async with httpx.AsyncClient() as client:
        url = f"{GBP_LOCATIONS_URL}/{account_id}/locations"
        response = await client.get(
            url,
            params={"readMask": "name,title,storefrontAddress,metadata"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()

    locations = []
    for loc in data.get("locations", []):
        address_parts = []
        addr = loc.get("storefrontAddress", {})
        for line in addr.get("addressLines", []):
            address_parts.append(line)
        if addr.get("locality"):
            address_parts.append(addr["locality"])
        if addr.get("administrativeArea"):
            address_parts.append(addr["administrativeArea"])
        if addr.get("postalCode"):
            address_parts.append(addr["postalCode"])

        metadata = loc.get("metadata", {})
        locations.append({
            "location_id": loc.get("name", ""),
            "name": loc.get("title", ""),
            "address": ", ".join(address_parts),
            "place_id": metadata.get("placeId", ""),
        })

    return locations
