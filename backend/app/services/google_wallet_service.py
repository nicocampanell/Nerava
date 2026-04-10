"""
Google Wallet Service

Creates and updates Google Wallet passes linked to a driver wallet.
"""
import json
import logging
import os
import time
from typing import Tuple

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain import DriverWallet, GoogleWalletLink

logger = logging.getLogger(__name__)


class GoogleWalletNotConfigured(Exception):
    """Raised when Google Wallet configuration is missing."""


def _get_google_credentials() -> Tuple[str, str]:
    """
    Load Google Wallet issuer ID and service account email / key if configured.
    
    For this implementation, we assume:
    - GOOGLE_WALLET_ISSUER_ID
    - GOOGLE_WALLET_CLASS_ID
    - GOOGLE_WALLET_API_BASE (optional, default Google Wallet REST base)
    - GOOGLE_WALLET_SERVICE_ACCOUNT_JWT (pre-signed JWT for API calls)
    """
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID")
    service_jwt = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_JWT")
    if not issuer_id or not service_jwt:
        raise GoogleWalletNotConfigured("Google Wallet is not fully configured")
    return issuer_id, service_jwt


def _get_google_api_base() -> str:
    return os.getenv("GOOGLE_WALLET_API_BASE", "https://walletobjects.googleapis.com/walletobjects/v1")


def _build_object_payload(wallet: DriverWallet, wallet_pass_token: str) -> dict:
    """
    Build Google Wallet object payload for the driver's wallet.
    
    - Barcode uses wallet_pass_token (opaque, no PII)
    - Link directs to /app/wallet/
    """
    base_url = settings.public_base_url.rstrip("/")
    object_id_suffix = wallet_pass_token

    payload = {
        "id": f"{os.getenv('GOOGLE_WALLET_CLASS_ID', 'nerava.wallet')}.{object_id_suffix}",
        "state": "active",
        "barcode": {
            "type": "qrCode",
            "value": wallet_pass_token,
        },
        "linksModuleData": {
            "uris": [
                {
                    "uri": f"{base_url}/app/wallet/",
                    "description": "Open Nerava Wallet",
                }
            ]
        },
    }

    return payload


def _authorized_client(service_jwt: str) -> httpx.Client:
    """
    Create an HTTP client authorized with a pre-signed JWT (Bearer token).
    """
    headers = {
        "Authorization": f"Bearer {service_jwt}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    return httpx.Client(timeout=30.0, headers=headers)


def ensure_google_wallet_class() -> None:
    """
    Ensure the Google Wallet class exists.
    
    This is a no-op if configuration is missing; endpoints will report ineligibility.
    """
    try:
        issuer_id, service_jwt = _get_google_credentials()
    except GoogleWalletNotConfigured:
        return

    class_id = os.getenv("GOOGLE_WALLET_CLASS_ID", "nerava.wallet")
    api_base = _get_google_api_base()
    url = f"{api_base}/loyaltyClass/{issuer_id}.{class_id}"

    with _authorized_client(service_jwt) as client:
        resp = client.get(url)
        if resp.status_code == 200:
            return
        if resp.status_code == 404:
            # Create a minimal class
            payload = {
                "id": f"{issuer_id}.{class_id}",
                "issuerName": "Nerava",
                "programName": "Nerava Wallet",
            }
            r2 = client.post(f"{api_base}/loyaltyClass", json=payload)
            if r2.status_code not in (200, 201):
                logger.error(f"Failed to create Google Wallet class: {r2.status_code} {r2.text[:200]}")
                raise RuntimeError("Failed to create Google Wallet class")
        else:
            logger.error(f"Failed to fetch Google Wallet class: {resp.status_code} {resp.text[:200]}")
            raise RuntimeError("Failed to fetch Google Wallet class")


def create_or_get_google_wallet_object(db: Session, wallet: DriverWallet, wallet_pass_token: str) -> GoogleWalletLink:
    """
    Create or get a Google Wallet object for a driver wallet.
    
    Updates/creates GoogleWalletLink row and calls Google Wallet API.
    """
    issuer_id, service_jwt = _get_google_credentials()
    class_id = os.getenv("GOOGLE_WALLET_CLASS_ID", "nerava.wallet")
    api_base = _get_google_api_base()

    object_payload = _build_object_payload(wallet, wallet_pass_token)
    object_id = object_payload["id"]

    with _authorized_client(service_jwt) as client:
        # Try to fetch existing object
        resp = client.get(f"{api_base}/loyaltyObject/{object_id}")
        if resp.status_code == 404:
            # Create
            create_resp = client.post(f"{api_base}/loyaltyObject", json=object_payload)
            if create_resp.status_code not in (200, 201):
                logger.error(f"Failed to create Google Wallet object: {create_resp.status_code} {create_resp.text[:200]}")
                raise RuntimeError("Failed to create Google Wallet object")
        elif resp.status_code != 200:
            logger.error(f"Failed to fetch Google Wallet object: {resp.status_code} {resp.text[:200]}")
            raise RuntimeError("Failed to fetch Google Wallet object")

    # Upsert DB link
    link = (
        db.query(GoogleWalletLink)
        .filter(GoogleWalletLink.driver_wallet_id == wallet.user_id)
        .first()
    )
    import uuid

    if link:
        link.issuer_id = issuer_id
        link.class_id = class_id
        link.object_id = object_id
        link.state = "active"
    else:
        link = GoogleWalletLink(
            id=str(uuid.uuid4()),
            driver_wallet_id=wallet.user_id,
            issuer_id=issuer_id,
            class_id=class_id,
            object_id=object_id,
            state="active",
        )
        db.add(link)

    db.commit()
    db.refresh(link)
    return link


def update_google_wallet_object_on_activity(db: Session, wallet: DriverWallet, wallet_pass_token: str) -> None:
    """
    Update Google Wallet object when wallet activity occurs.
    
    For now, we simply ensure the object exists; richer updates could include timeline info.
    """
    try:
        create_or_get_google_wallet_object(db, wallet, wallet_pass_token)
    except GoogleWalletNotConfigured:
        # Silently ignore if not configured
        return
    except Exception as e:
        logger.error(f"Failed to update Google Wallet object for wallet {wallet.user_id}: {e}", exc_info=True)


def generate_google_wallet_add_link(object_id: str) -> str:
    """
    Generate a signed JWT for "Save to Google Wallet" deep link.
    
    Args:
        object_id: Google Wallet object ID (e.g., "issuer.class.token")
        
    Returns:
        URL: https://pay.google.com/gp/v/save/<JWT>
        
    Raises:
        GoogleWalletNotConfigured: If credentials are missing
        RuntimeError: If JWT signing fails
    """
    # Check if we have a service account key file path
    service_account_key_path = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_KEY_PATH")
    
    if not service_account_key_path or not os.path.exists(service_account_key_path):
        logger.warning("GOOGLE_WALLET_SERVICE_ACCOUNT_KEY_PATH not set or file not found - cannot generate Add-to-Wallet link")
        raise GoogleWalletNotConfigured("Service account key file required for Add-to-Wallet links")
    
    # Load service account key and create JWT
    try:
        with open(service_account_key_path) as f:
            key_data = json.load(f)
        
        service_account_email = key_data.get("client_email")
        private_key_pem = key_data.get("private_key")
        
        if not service_account_email or not private_key_pem:
            raise RuntimeError("Service account key missing client_email or private_key")
        
        # Create JWT using PyJWT (must be installed: pip install PyJWT cryptography)
        try:
            import jwt as pyjwt
        except ImportError:
            raise RuntimeError("PyJWT is required for Google Wallet Add-to-Wallet links. Install with: pip install PyJWT")
        
        from cryptography.hazmat.primitives import serialization
        
        # Parse private key
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode(),
            password=None,
        )
        
        # Build JWT payload per Google Wallet spec
        now = int(time.time())
        payload = {
            "iss": service_account_email,
            "aud": "google",
            "typ": "savetowallet",
            "iat": now,
            "exp": now + 3600,  # 1 hour expiry
            "payload": {
                "loyaltyObjects": [
                    {"id": object_id}
                ]
            }
        }
        
        # Sign JWT with RS256
        jwt_token = pyjwt.encode(payload, private_key, algorithm="RS256")
        
        return f"https://pay.google.com/gp/v/save/{jwt_token}"
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Google Wallet service account key JSON: {e}", exc_info=True)
        raise RuntimeError(f"Invalid service account key JSON: {e}")
    except Exception as e:
        logger.error(f"Failed to generate Google Wallet JWT from key file: {e}", exc_info=True)
        raise RuntimeError(f"Failed to generate Google Wallet JWT: {e}")


