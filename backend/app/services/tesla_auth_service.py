"""
Tesla OIDC ID token verification and userinfo service.

Verifies Tesla ID tokens using JWKS (RS256) and fetches user profile
from the Tesla userinfo endpoint.
"""
from typing import Any, Dict

import httpx
import jwt
from fastapi import HTTPException, status
from jwt.algorithms import RSAAlgorithm

from ..core.config import settings

# Tesla OIDC endpoints
_tesla_jwks_url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/discovery/thirdparty/keys"
_tesla_issuer = "https://auth.tesla.com/oauth2/v3/nts"
_tesla_userinfo_url = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/userinfo"

# Cache for Tesla JWKS
_tesla_jwks_cache: Dict[str, Any] = {}


def get_tesla_jwks() -> Dict[str, Any]:
    """Fetch Tesla's public keys (JWKS)."""
    global _tesla_jwks_cache

    if _tesla_jwks_cache:
        return _tesla_jwks_cache

    try:
        response = httpx.get(_tesla_jwks_url, timeout=10.0)
        response.raise_for_status()
        _tesla_jwks_cache = response.json()
        return _tesla_jwks_cache
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch Tesla JWKS: {str(e)}",
        )


def verify_tesla_id_token(id_token: str) -> Dict[str, Any]:
    """
    Verify Tesla ID token and extract claims.

    Args:
        id_token: Tesla ID token (JWT) from the token exchange response.

    Returns:
        Dict with at least ``{"sub": "..."}`` (Tesla subject ID).
        The Tesla id_token does NOT contain email.

    Raises:
        HTTPException 503: If Tesla client is not configured.
        HTTPException 401: If token verification fails.
    """
    if not settings.TESLA_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tesla authentication is not configured. Set TESLA_CLIENT_ID.",
        )

    try:
        unverified_header = jwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")

        if not kid:
            raise ValueError("Token missing key ID")

        jwks = get_tesla_jwks()

        key = None
        for jwk in jwks.get("keys", []):
            if jwk.get("kid") == kid:
                key = RSAAlgorithm.from_jwk(jwk)
                break

        if not key:
            # Refresh JWKS cache and retry (keys may have rotated)
            global _tesla_jwks_cache
            _tesla_jwks_cache = {}
            jwks = get_tesla_jwks()
            for jwk in jwks.get("keys", []):
                if jwk.get("kid") == kid:
                    key = RSAAlgorithm.from_jwk(jwk)
                    break

        if not key:
            raise ValueError(f"Key {kid} not found in Tesla JWKS")

        payload = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=settings.TESLA_CLIENT_ID,
            issuer=_tesla_issuer,
        )

        return {"sub": payload.get("sub")}

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tesla ID token has expired",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Tesla ID token: {str(e)}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Tesla token verification failed: {str(e)}",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Tesla authentication error: {str(e)}",
        )


async def fetch_tesla_user_profile(access_token: str) -> Dict[str, Any]:
    """
    Fetch the user's profile from Tesla userinfo endpoint (best-effort).

    Args:
        access_token: Valid Tesla access token.

    Returns:
        Dict with ``email`` and ``name`` (both nullable).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _tesla_userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            data = response.json()
            return {
                "email": data.get("email"),
                "name": data.get("name"),
            }
    except Exception:
        # Best-effort — don't block login if userinfo fails
        return {"email": None, "name": None}
