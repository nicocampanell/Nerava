"""
Smartcar API client service
Handles OAuth token exchange, refresh, and vehicle API calls
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import httpx
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..config import settings
from ..core.retry import retry_with_backoff
from ..models.vehicle import VehicleAccount, VehicleToken

logger = logging.getLogger(__name__)


class SmartcarTokenExpiredError(Exception):
    """Raised when Smartcar refresh token is expired and re-auth is required"""
    pass

# Timeout for Smartcar API calls
SMARTCAR_TIMEOUT = 30.0


async def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """
    Exchange authorization code for access and refresh tokens
    
    Args:
        code: Authorization code from Smartcar callback
        
    Returns:
        Dict with access_token, refresh_token, expires_in, scope, etc.
        
    Raises:
        httpx.HTTPStatusError: If Smartcar API returns an error
    """
    url = f"{settings.smartcar_auth_url}/oauth/token"
    
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.smartcar_redirect_uri,
        "client_id": settings.smartcar_client_id,
        "client_secret": settings.smartcar_client_secret,
    }
    
    # Log validation info (no secrets)
    logger.debug(f"Token exchange request: grant_type={data['grant_type']}, redirect_uri={data['redirect_uri']}, client_id={data['client_id'][:8]}...")
    
    async def _exchange_tokens():
        """Internal function for token exchange with retry"""
        async with httpx.AsyncClient(timeout=SMARTCAR_TIMEOUT) as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            return response.json()
    
    try:
        # Use retry logic for transient failures
        return await retry_with_backoff(_exchange_tokens, max_attempts=3)
    except httpx.HTTPStatusError as e:
        # Don't log response body - may contain tokens/secrets
        logger.error(f"Smartcar token exchange failed: {e.response.status_code}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during token exchange: {e}")
        raise


async def refresh_tokens(db: Session, vehicle_account: VehicleAccount) -> VehicleToken:
    """
    Refresh access token for a vehicle account if needed
    
    Args:
        db: Database session
        vehicle_account: VehicleAccount to refresh tokens for
        
    Returns:
        VehicleToken (existing if still valid, or newly refreshed)
        
    Raises:
        httpx.HTTPStatusError: If Smartcar API returns an error
    """
    # Get the most recent token
    latest_token = (
        db.query(VehicleToken)
        .filter(VehicleToken.vehicle_account_id == vehicle_account.id)
        .order_by(desc(VehicleToken.created_at))
        .first()
    )
    
    if not latest_token:
        raise ValueError(f"No token found for vehicle account {vehicle_account.id}")
    
    # Check if token expires in more than 5 minutes
    if latest_token.expires_at > datetime.utcnow() + timedelta(minutes=5):
        logger.debug(f"Token for vehicle {vehicle_account.id} still valid, returning existing token")
        return latest_token
    
    # Refresh the token
    url = f"{settings.smartcar_auth_url}/oauth/token"
    
    # Decrypt refresh token before using (P0 security fix)
    from app.services.token_encryption import decrypt_token
    try:
        decrypted_refresh_token = decrypt_token(latest_token.refresh_token)
    except Exception:
        # If decryption fails, assume it's plaintext (migration compatibility)
        decrypted_refresh_token = latest_token.refresh_token
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": decrypted_refresh_token,
        "client_id": settings.smartcar_client_id,
        "client_secret": settings.smartcar_client_secret,
    }
    
    # Log validation info (no secrets)
    logger.debug(f"Token refresh request: grant_type={data['grant_type']}, client_id={data['client_id'][:8]}...")
    
    async with httpx.AsyncClient(timeout=SMARTCAR_TIMEOUT) as client:
        try:
            response = await client.post(url, data=data)
            response.raise_for_status()
            token_data = response.json()
        except httpx.HTTPStatusError as e:
            # Check if it's an expired refresh token
            if e.response.status_code in (400, 401):
                try:
                    error_body = e.response.json() if e.response.headers.get("content-type", "").startswith("application/json") else {}
                    error_description = str(error_body).lower() if error_body else ""
                    error_code = error_body.get("error", "").lower() if isinstance(error_body, dict) else ""
                    
                    # Check for expired/invalid token indicators
                    if any(keyword in error_description or keyword in error_code for keyword in ["expired", "invalid", "revoked", "invalid_grant"]):
                        logger.warning(f"Smartcar refresh token expired for vehicle account {vehicle_account.id}")
                        raise SmartcarTokenExpiredError("Refresh token expired, re-auth required")
                except SmartcarTokenExpiredError:
                    raise
                except Exception:
                    # If we can't parse the error, still raise SmartcarTokenExpiredError for 400/401
                    logger.warning(f"Smartcar token refresh failed with {e.response.status_code}, assuming expired token")
                    raise SmartcarTokenExpiredError("Refresh token expired, re-auth required")
            
            # Don't log response body - may contain tokens/secrets
            logger.error(f"Smartcar token refresh failed: {e.response.status_code}")
            raise
        except SmartcarTokenExpiredError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error during token refresh: {e}")
            raise
    
    # Encrypt tokens before storing (P0 security fix)
    from app.services.token_encryption import encrypt_token
    
    # Validate access_token is present
    if "access_token" not in token_data:
        raise ValueError("Smartcar response missing access_token")
    
    # Use refresh_token from response, or fallback to decrypted_refresh_token (plaintext)
    # Note: decrypted_refresh_token is already plaintext from line 94
    new_refresh_token = token_data.get("refresh_token")
    if not new_refresh_token:
        # If response doesn't include refresh_token, use the one we already decrypted
        new_refresh_token = decrypted_refresh_token
        if not new_refresh_token:
            raise ValueError("Both Smartcar response refresh_token and decrypted_refresh_token are missing")
    
    new_access_token = token_data["access_token"]
    
    # Encrypt new tokens
    encrypted_access_token = encrypt_token(new_access_token)
    encrypted_refresh_token = encrypt_token(new_refresh_token)
    
    # Create new token record with encrypted tokens
    import uuid
    new_token = VehicleToken(
        id=str(uuid.uuid4()),
        vehicle_account_id=vehicle_account.id,
        access_token=encrypted_access_token,
        refresh_token=encrypted_refresh_token,
        encryption_version=1,  # Mark as encrypted
        expires_at=datetime.utcnow() + timedelta(seconds=token_data.get("expires_in", 3600)),
        scope=token_data.get("scope", latest_token.scope),
    )
    
    db.add(new_token)
    db.commit()
    db.refresh(new_token)
    
    logger.info(f"Refreshed token for vehicle account {vehicle_account.id}")
    return new_token


async def list_vehicles(access_token: str) -> Dict[str, Any]:
    """
    List vehicles for the authenticated user
    
    Args:
        access_token: Smartcar access token
        
    Returns:
        Dict with vehicles array
        
    Raises:
        httpx.HTTPStatusError: If Smartcar API returns an error
    """
    url = f"{settings.smartcar_base_url}/v2.0/vehicles"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(timeout=SMARTCAR_TIMEOUT) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Don't log response body - may contain sensitive data
            logger.error(f"Smartcar list vehicles failed: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error listing vehicles: {e}")
            raise


async def get_vehicle_location(access_token: str, vehicle_id: str) -> Dict[str, Any]:
    """
    Get vehicle location
    
    Args:
        access_token: Smartcar access token
        vehicle_id: Smartcar vehicle ID
        
    Returns:
        Dict with latitude, longitude, etc.
        
    Raises:
        httpx.HTTPStatusError: If Smartcar API returns an error
    """
    url = f"{settings.smartcar_base_url}/v2.0/vehicles/{vehicle_id}/location"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(timeout=SMARTCAR_TIMEOUT) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Don't log response body - may contain sensitive data
            logger.error(f"Smartcar get location failed: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting location: {e}")
            raise


async def get_vehicle_charge(access_token: str, vehicle_id: str) -> Dict[str, Any]:
    """
    Get vehicle charge state
    
    Args:
        access_token: Smartcar access token
        vehicle_id: Smartcar vehicle ID
        
    Returns:
        Dict with stateOfCharge, isPluggedIn, etc.
        
    Raises:
        httpx.HTTPStatusError: If Smartcar API returns an error
    """
    url = f"{settings.smartcar_base_url}/v2.0/vehicles/{vehicle_id}/charge"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    async with httpx.AsyncClient(timeout=SMARTCAR_TIMEOUT) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # Don't log response body - may contain sensitive data
            logger.error(f"Smartcar get charge failed: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting charge: {e}")
            raise

