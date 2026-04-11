"""
Apple Wallet Pass Push Service (APNs for PassKit)

Sends silent push notifications to prompt Apple Wallet to refresh passes.
"""
import logging
import os
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.domain import ApplePassRegistration, DriverWallet

logger = logging.getLogger(__name__)


def _apns_client():
    """
    Lazily import and construct an APNs client if configuration is present.
    
    This function avoids importing heavy dependencies if push is disabled.
    """
    from apns2.client import APNsClient  # type: ignore
    from apns2.credentials import TokenCredentials  # type: ignore

    key_id = os.getenv("APPLE_WALLET_APNS_KEY_ID")
    team_id = os.getenv("APPLE_WALLET_TEAM_ID")
    auth_key_path = os.getenv("APPLE_WALLET_APNS_AUTH_KEY_PATH")
    topic = os.getenv("APPLE_WALLET_APNS_TOPIC")

    if not key_id or not team_id or not auth_key_path or not topic:
        raise RuntimeError("Apple Wallet APNs credentials not fully configured")

    credentials = TokenCredentials(
        auth_key_path=auth_key_path,
        key_id=key_id,
        team_id=team_id,
    )
    client = APNsClient(
        credentials,
        use_sandbox=os.getenv("APPLE_WALLET_APNS_ENV", "sandbox") == "sandbox",
    )
    return client, topic


def send_pass_update(push_token: str) -> None:
    """
    Send a silent PassKit push notification for a single device push token.
    
    This should be fire-and-forget (errors are logged but not raised).
    """
    if not push_token:
        return

    if os.getenv("APPLE_PASS_PUSH_ENABLED", "false").lower() != "true":
        return

    try:
        client, topic = _apns_client()
    except Exception as e:
        logger.error(f"Apple Wallet APNs client init failed: {e}", exc_info=True)
        return

    try:
        from apns2.payload import Payload  # type: ignore

        # Empty payload with content-available triggers a background update
        payload = Payload(content_available=True)
        client.send_notification(push_token, payload, topic)
        logger.info("Sent Apple Wallet PassKit push")
    except Exception as e:
        logger.error(f"Failed to send Apple Wallet pass update push: {e}", exc_info=True)


def send_updates_for_wallet(db: Session, wallet: DriverWallet) -> None:
    """
    Send silent PassKit push notifications for all active registrations for a wallet.
    
    Non-blocking: all errors are caught and logged.
    """
    if os.getenv("APPLE_PASS_PUSH_ENABLED", "false").lower() != "true":
        return

    try:
        regs: Iterable[ApplePassRegistration] = (
            db.query(ApplePassRegistration)
            .filter(
                ApplePassRegistration.driver_wallet_id == wallet.user_id,
                ApplePassRegistration.is_active == True,  # noqa: E712
                ApplePassRegistration.push_token.isnot(None),
            )
            .all()
        )
    except Exception as e:
        logger.error(f"Failed to load ApplePassRegistration for wallet {wallet.user_id}: {e}", exc_info=True)
        return

    for reg in regs:
        try:
            send_pass_update(reg.push_token)
        except Exception:
            # send_pass_update already logs errors; continue
            continue










