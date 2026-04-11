"""
Push Notification Service — Sends push notifications to iOS (APNs) and Android (FCM) devices.

Uses PyAPNs2 for Apple Push Notification service and firebase-admin for Firebase Cloud Messaging.
Handles token invalidation when APNs returns 410 Gone or FCM returns unregistered.
"""
import json
import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device_token import DeviceToken

logger = logging.getLogger(__name__)

# Lazy-loaded APNs clients (only initialized when needed)
_apns_client_prod = None
_apns_client_sandbox = None
_apns_key_path = None  # Shared key path for both clients


def _ensure_apns_key_path():
    """Ensure the APNs key file is available. Returns the path or None."""
    global _apns_key_path
    if _apns_key_path is not None:
        return _apns_key_path

    key_path = getattr(settings, "APNS_KEY_PATH", None)
    key_content = getattr(settings, "APNS_KEY_CONTENT", None)

    if not key_path and not key_content:
        return None

    # If key content is provided via env var, write it to a temp file
    if not key_path and key_content:
        import tempfile
        # App Runner stores literal \n as two-char escape sequences — restore real newlines
        if "\\n" in key_content:
            key_content = key_content.replace("\\n", "\n")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".p8", delete=False)
        tmp.write(key_content)
        tmp.close()
        key_path = tmp.name
        logger.info("APNs key written from env var to %s (%d bytes)", key_path, len(key_content))

    _apns_key_path = key_path
    return _apns_key_path


def _get_apns_client(use_sandbox: bool = False):
    """Get or create an APNs client. Returns None if not configured."""
    global _apns_client_prod, _apns_client_sandbox

    existing = _apns_client_sandbox if use_sandbox else _apns_client_prod
    if existing is not None:
        return existing

    key_id = getattr(settings, "APNS_KEY_ID", None)
    team_id = getattr(settings, "APNS_TEAM_ID", None)

    if not key_id or not team_id:
        logger.debug("APNs not configured (APNS_KEY_ID, APNS_TEAM_ID required)")
        return None

    key_path = _ensure_apns_key_path()
    if not key_path:
        logger.debug("APNs not configured (APNS_KEY_PATH or APNS_KEY_CONTENT required)")
        return None

    try:
        from apns2.client import APNsClient
        from apns2.credentials import TokenCredentials

        token_credentials = TokenCredentials(
            auth_key_path=key_path,
            auth_key_id=key_id,
            team_id=team_id,
        )
        client = APNsClient(
            credentials=token_credentials,
            use_sandbox=use_sandbox,
        )
        env_label = "sandbox" if use_sandbox else "production"
        logger.info("APNs %s client initialized (key_id=%s)", env_label, key_id)

        if use_sandbox:
            _apns_client_sandbox = client
        else:
            _apns_client_prod = client

        return client
    except Exception as e:
        logger.warning("Failed to initialize APNs client (sandbox=%s): %s", use_sandbox, e)
        return None


# Lazy-loaded Firebase app (only initialized when needed)
_firebase_app = None


def _get_firebase_app():
    """Get or create the Firebase app. Returns None if not configured."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    creds_json = getattr(settings, "FIREBASE_CREDENTIALS_JSON", "") or os.getenv("FIREBASE_CREDENTIALS_JSON", "")
    if not creds_json:
        logger.debug("Firebase not configured (FIREBASE_CREDENTIALS_JSON required)")
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(json.loads(creds_json))
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase app initialized for FCM")
        return _firebase_app
    except Exception as e:
        logger.warning("Failed to initialize Firebase app: %s", e)
        return None


def _send_fcm_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send a single FCM notification. Returns True if successful."""
    app = _get_firebase_app()
    if app is None:
        logger.info("Firebase not configured — skipping FCM push")
        return False

    try:
        from firebase_admin import messaging

        # FCM data values must be strings
        str_data = {k: str(v) for k, v in (data or {}).items()}

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=str_data,
            token=token,
        )
        messaging.send(message, app=app)
        return True
    except Exception as e:
        error_str = str(e)
        logger.warning("FCM send error: %s", error_str)
        # Check for unregistered token
        if "UNREGISTERED" in error_str.upper() or "NOT_FOUND" in error_str.upper():
            raise _TokenInvalidError(error_str)
        return False


class _TokenInvalidError(Exception):
    """Raised when a push token is permanently invalid."""
    pass


def _send_apns_with_fallback(device, payload, bundle_id: str) -> Optional[bool]:
    """
    Try sending APNs notification via production, then sandbox.
    Returns True if sent, False if failed, None if token is permanently invalid.
    """
    # Determine which environment to try first based on APNS_USE_SANDBOX setting
    primary_sandbox = getattr(settings, "APNS_USE_SANDBOX", False)
    environments = [primary_sandbox, not primary_sandbox]

    for use_sandbox in environments:
        client = _get_apns_client(use_sandbox=use_sandbox)
        if client is None:
            continue
        env_label = "sandbox" if use_sandbox else "production"
        try:
            # Log token info for debugging
            token_preview = device.token[:8] + "..." if device.token and len(device.token) > 8 else device.token
            logger.info(
                "APNs sending (%s) to device %s: token_len=%d preview=%s topic=%s",
                env_label, device.id, len(device.token or ""), token_preview, bundle_id,
            )

            # send_notification() returns None on success, raises on failure
            client.send_notification(
                device.token, payload, topic=bundle_id
            )
            # If we reach here, the notification was sent successfully
            logger.info("APNs push sent (%s) to device %s", env_label, device.id)
            return True

        except Exception as e:
            error_str = str(e)
            error_class = type(e).__name__
            logger.info("APNs %s error for device %s: [%s] %s", env_label, device.id, error_class, error_str)

            combined = error_class + " " + error_str
            # BadDeviceToken means wrong environment — try the other one
            if "BadDeviceToken" in combined:
                logger.info("BadDeviceToken on %s — trying other environment", env_label)
                continue
            # BadEnvironmentKeyInToken — key/environment mismatch, try other
            if "BadEnvironment" in combined or "InvalidProviderToken" in combined:
                logger.info("Environment/key mismatch on %s — trying other environment", env_label)
                continue
            # 410 Gone / Unregistered — permanently invalid
            if "410" in error_str or "Unregistered" in combined:
                device.is_active = False
                logger.info("Deactivated expired APNs token %s (410 Gone)", device.id)
                return None
            # Other error — try fallback environment
            continue

    logger.warning("APNs push failed for device %s on both environments", device.id)
    return False


def send_push_notification(
    db: Session,
    user_id: int,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Send a push notification to all active devices for a user.

    Returns the number of notifications successfully sent.
    """
    bundle_id = getattr(settings, "APNS_BUNDLE_ID", "com.nerava.app")

    tokens = (
        db.query(DeviceToken)
        .filter(
            DeviceToken.user_id == user_id,
            DeviceToken.is_active.is_(True),
        )
        .all()
    )

    if not tokens:
        logger.debug("No active device tokens for user %s", user_id)
        return 0

    # Build APNs payload (only if we have iOS tokens)
    apns_payload = None
    has_ios_tokens = any(d.platform == "ios" for d in tokens)
    if has_ios_tokens:
        try:
            from apns2.payload import Payload
            apns_payload = Payload(
                alert={"title": title, "body": body},
                sound="default",
                custom=data or {},
            )
        except ImportError:
            logger.warning("PyAPNs2 not installed — cannot send iOS push notifications")

    sent = 0
    for device in tokens:
        if device.platform == "android":
            # Android: send via FCM
            try:
                success = _send_fcm_notification(device.token, title, body, data)
                if success:
                    sent += 1
                    logger.debug("FCM push sent to device %s for user %s", device.id, user_id)
            except _TokenInvalidError:
                device.is_active = False
                logger.info("Deactivated invalid FCM token %s", device.id)
            except Exception as e:
                logger.warning("FCM push error for device %s: %s", device.id, e)
        elif device.platform == "ios":
            # iOS: send via APNs — try production first, fall back to sandbox
            # (Xcode builds generate sandbox tokens, App Store builds generate production tokens)
            if apns_payload is None:
                continue
            success = _send_apns_with_fallback(device, apns_payload, bundle_id)
            if success:
                sent += 1
            elif success is None:
                # Token is invalid — deactivated inside the helper
                pass
        else:
            logger.warning("Unknown platform '%s' for device %s", device.platform, device.id)

    if sent > 0 or any(not d.is_active for d in tokens):
        db.commit()

    logger.info(
        "Push notification sent to %d/%d devices for user %s: %s",
        sent, len(tokens), user_id, title,
    )
    return sent


def send_incentive_earned_push(
    db: Session,
    user_id: int,
    amount_cents: int,
) -> int:
    """Send push notification when driver earns a charging incentive."""
    amount_str = f"${amount_cents / 100:.2f}"
    return send_push_notification(
        db,
        user_id,
        title="You earned a reward!",
        body=f"You earned {amount_str} from your charging session.",
        data={"type": "incentive_earned", "amount_cents": amount_cents},
    )


def send_exclusive_confirmed_push(
    db: Session,
    user_id: int,
    merchant_name: str,
) -> int:
    """Send push notification when exclusive spot is confirmed."""
    return send_push_notification(
        db,
        user_id,
        title="Spot confirmed!",
        body=f"Your spot at {merchant_name} is confirmed. Head over now!",
        data={"type": "exclusive_confirmed", "merchant_name": merchant_name},
    )


def send_charging_detected_push(
    db: Session,
    user_id: int,
    session_id: str,
    charger_name: Optional[str] = None,
) -> int:
    """Send push notification when charging is detected via Fleet Telemetry."""
    body = "We detected your car is charging"
    if charger_name:
        body += f" at {charger_name}"
    body += ". Tap to see nearby deals."
    return send_push_notification(
        db,
        user_id,
        title="Charging detected!",
        body=body,
        data={"type": "charging_detected", "session_id": str(session_id)},
    )


def send_nearby_merchant_push(
    db: Session,
    user_id: int,
    merchant_name: str,
    exclusive_title: Optional[str] = None,
    charger_id: Optional[str] = None,
    merchant_place_id: Optional[str] = None,
) -> int:
    """Send push notification when a Nerava merchant is nearby during charging."""
    if exclusive_title:
        body = f"{merchant_name} is nearby — claim your {exclusive_title} while you charge!"
    else:
        body = f"{merchant_name} is nearby and has a deal for you while you charge!"
    return send_push_notification(
        db,
        user_id,
        title=f"{merchant_name} nearby",
        body=body,
        data={
            "type": "nearby_merchant",
            "merchant_name": merchant_name,
            "merchant_place_id": merchant_place_id or "",
            "charger_id": charger_id or "",
        },
    )


def send_payout_complete_push(
    db: Session,
    user_id: int,
    amount_cents: int,
) -> int:
    """Send push notification when a payout is completed."""
    amount_str = f"${amount_cents / 100:.2f}"
    return send_push_notification(
        db,
        user_id,
        title="Payout sent!",
        body=f"Your payout of {amount_str} has been sent to your bank.",
        data={"type": "payout_complete", "amount_cents": amount_cents},
    )
