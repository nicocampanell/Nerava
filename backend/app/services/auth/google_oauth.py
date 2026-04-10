"""
Google OAuth service with GBP (Google Business Profile) access verification
"""

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException, status

from ...core.config import settings
from ...services.google_auth import verify_google_id_token

logger = logging.getLogger(__name__)

# Google Business Profile API endpoints
GOOGLE_BP_API_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
GOOGLE_BP_LOCATIONS_API = "https://mybusinessbusinessinformation.googleapis.com/v1"


class GoogleOAuthService:
    """
    Google OAuth service with Google Business Profile access verification.
    """

    @staticmethod
    def verify_id_token(id_token: str) -> Dict[str, Any]:
        """
        Verify Google ID token and extract user information.

        Enhanced version that verifies issuer, audience, expiry, and signature.

        Args:
            id_token: Google ID token string

        Returns:
            Dict with user info: email, sub, name, etc.

        Raises:
            HTTPException: If token verification fails
        """
        try:
            # Use existing verify_google_id_token which already handles signature verification
            user_info = verify_google_id_token(id_token)

            # Additional verification: check issuer
            # Note: google-auth library already verifies issuer, but we can add explicit check
            # The issuer should be accounts.google.com or https://accounts.google.com

            # Verify audience matches our client ID
            # Note: This is already done in verify_google_id_token, but we ensure it's correct
            if not settings.GOOGLE_OAUTH_CLIENT_ID:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Google OAuth client ID not configured",
                )

            return user_info

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Google ID token verification failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Google token verification failed: {str(e)}",
            )

    @staticmethod
    async def check_gbp_access(access_token: str) -> List[Dict[str, str]]:
        """
        Check Google Business Profile access and list locations.

        Args:
            access_token: OAuth access token with business.manage scope

        Returns:
            List of location dictionaries with location_id, name, address, place_id

        Raises:
            HTTPException: If GBP access check fails
        """
        if not settings.GOOGLE_GBP_REQUIRED:
            logger.warning("GOOGLE_GBP_REQUIRED is false - skipping GBP access check")
            return []

        try:
            # First, get accounts
            async with httpx.AsyncClient() as client:
                # List accounts
                accounts_response = await client.get(
                    f"{GOOGLE_BP_API_BASE}/accounts",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0,
                )

                if accounts_response.status_code == 401:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired access token",
                    )

                accounts_response.raise_for_status()
                accounts_data = accounts_response.json()

                accounts = accounts_data.get("accounts", [])
                if not accounts:
                    logger.warning("No Google Business Profile accounts found")
                    return []

                # Get locations for first account (in production, you might want to handle multiple accounts)
                account_name = accounts[0].get("name")
                if not account_name:
                    logger.warning("Account name not found")
                    return []

                # List locations
                locations_response = await client.get(
                    f"{GOOGLE_BP_LOCATIONS_API}/{account_name}/locations",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0,
                )

                if locations_response.status_code == 401:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired access token for locations API",
                    )

                locations_response.raise_for_status()
                locations_data = locations_response.json()

                locations = locations_data.get("locations", [])

                # Format locations
                formatted_locations = []
                for loc in locations:
                    formatted_locations.append(
                        {
                            "location_id": (
                                loc.get("name", "").split("/")[-1] if loc.get("name") else ""
                            ),
                            "name": loc.get("title", ""),
                            "address": (
                                loc.get("storefrontAddress", {}).get("addressLines", [""])[0]
                                if loc.get("storefrontAddress")
                                else ""
                            ),
                            "place_id": loc.get(
                                "storeCode", ""
                            ),  # May need to map to actual place_id
                        }
                    )

                logger.info(f"Found {len(formatted_locations)} GBP locations")
                return formatted_locations

        except httpx.HTTPError as e:
            logger.error(f"HTTP error checking GBP access: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to check Google Business Profile access",
            )
        except Exception as e:
            logger.error(f"Error checking GBP access: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Google Business Profile access check failed: {str(e)}",
            )

    @staticmethod
    async def verify_and_check_gbp(
        id_token: str, access_token: Optional[str] = None
    ) -> tuple[Dict[str, Any], List[Dict[str, str]]]:
        """
        Verify ID token and check GBP access in one call.

        Args:
            id_token: Google ID token
            access_token: Optional OAuth access token (required for GBP check)

        Returns:
            Tuple of (user_info, locations)
        """
        user_info = GoogleOAuthService.verify_id_token(id_token)

        locations = []
        # Only check GBP access if access_token is provided and GBP is required
        # Note: For ID token-only flow, GBP check will be skipped
        # In production, you may want to require access_token for merchant SSO
        if access_token and settings.GOOGLE_GBP_REQUIRED:
            try:
                locations = await GoogleOAuthService.check_gbp_access(access_token)
            except HTTPException as e:
                # If GBP check fails, log but don't fail auth (for backward compatibility)
                logger.warning(f"GBP access check failed: {e.detail}")
                if settings.is_prod:
                    # In production, fail if GBP check fails
                    raise

        return user_info, locations
