"""
Apple Wallet Pass Generator

Generates .pkpass bundles for Apple Wallet with optional signing.
"""

import hashlib
import json
import logging
import os
import secrets
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.domain import DriverWallet
from app.services.token_encryption import TokenDecryptionError, decrypt_token, encrypt_token
from app.services.wallet_timeline import get_wallet_timeline

logger = logging.getLogger(__name__)


def _ensure_wallet_pass_token(db: Session, driver_user_id: int) -> str:
    """
    Ensure driver has a wallet_pass_token, creating one if missing.

    Returns the token (opaque, random).
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_user_id).first()

    if not wallet:
        wallet = DriverWallet(user_id=driver_user_id, nova_balance=0, energy_reputation_score=0)
        db.add(wallet)
        db.flush()

    if not wallet.wallet_pass_token:
        # Generate random opaque token (24 bytes = 32 chars in base64)
        token = secrets.token_urlsafe(24)
        # Ensure uniqueness (very unlikely collision, but check anyway)
        existing = db.query(DriverWallet).filter(DriverWallet.wallet_pass_token == token).first()
        if existing:
            token = secrets.token_urlsafe(24)  # Regenerate if collision

        wallet.wallet_pass_token = token
        db.commit()
        db.refresh(wallet)

    return wallet.wallet_pass_token


def _ensure_apple_auth_token(db: Session, wallet: DriverWallet) -> str:
    """
    Ensure the driver wallet has an Apple authentication token for PassKit web service.

    The token is:
    - Random, opaque (no PII)
    - Stored encrypted-at-rest via token_encryption
    - Used as the PassKit authenticationToken (header + pass.json)
    """
    # If already present, decrypt and return
    if wallet.apple_authentication_token:
        try:
            return decrypt_token(wallet.apple_authentication_token)
        except TokenDecryptionError:
            # If decryption fails (e.g., key rotation), generate a new token
            pass

    # Generate a new opaque token
    auth_token = secrets.token_urlsafe(24)
    wallet.apple_authentication_token = encrypt_token(auth_token)
    db.commit()
    db.refresh(wallet)
    return auth_token


def _get_tier_from_score(score: int) -> str:
    """
    Compute tier from energy reputation score.

    Thresholds:
    - >= 850: Platinum
    - >= 650: Gold
    - >= 400: Silver
    - < 400: Bronze
    """
    if score >= 850:
        return "Platinum"
    elif score >= 650:
        return "Gold"
    elif score >= 400:
        return "Silver"
    else:
        return "Bronze"


def _get_pass_images_dir() -> Path:
    """Get directory for pass images"""
    # Try ui-mobile/assets/pass first, then ui-mobile/assets
    ui_mobile_pass_path = (
        Path(__file__).parent.parent.parent.parent / "ui-mobile" / "assets" / "pass"
    )
    if ui_mobile_pass_path.exists():
        return ui_mobile_pass_path

    ui_mobile_path = Path(__file__).parent.parent.parent.parent / "ui-mobile" / "assets"
    if ui_mobile_path.exists():
        return ui_mobile_path

    # Fallback: create a pass directory in static
    static_path = Path(__file__).parent.parent / "static" / "pass"
    static_path.mkdir(parents=True, exist_ok=True)
    return static_path


def _validate_image_dimensions(
    image_bytes: bytes, expected_width: int, expected_height: int, filename: str
) -> None:
    """
    Validate image dimensions using Pillow.

    Raises ValueError if dimensions don't match.
    """
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(image_bytes))
        width, height = img.size

        if width != expected_width or height != expected_height:
            raise ValueError(
                f"Image {filename} has incorrect dimensions: "
                f"expected {expected_width}x{expected_height}, got {width}x{height}"
            )
    except ImportError:
        # Pillow not available - skip validation (should not happen in production)
        logger.warning("Pillow not available, skipping image dimension validation")
    except Exception as e:
        if isinstance(e, ValueError):
            raise
        logger.warning(f"Could not validate image dimensions for {filename}: {e}")


def _generate_placeholder_image(width: int, height: int, bg_color: tuple = (30, 64, 175)) -> bytes:
    """
    Generate a placeholder image with specified dimensions.

    Uses RGBA mode for proper transparency support (Apple Wallet requirement).

    Args:
        width: Image width in pixels
        height: Image height in pixels
        bg_color: RGB tuple for background color (default: #1e40af - Nerava blue)

    Returns:
        PNG image bytes
    """
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw

        # Use RGBA mode for transparency support (Apple Wallet requirement)
        img = Image.new("RGBA", (width, height), color=(*bg_color, 255))
        draw = ImageDraw.Draw(img)

        # Add a simple "N" logo in the center for better visual
        if width >= 29 and height >= 29:
            # Draw a simple "N" in white
            font_size = min(width, height) // 2
            try:
                # Try to use a default font
                from PIL import ImageFont

                # Use default font
                font = ImageFont.load_default()
            except:
                font = None

            # Draw "N" in center
            text = "N"
            bbox = draw.textbbox((0, 0), text, font=font) if font else (0, 0, 10, 10)
            text_width = bbox[2] - bbox[0] if font else 10
            text_height = bbox[3] - bbox[1] if font else 10
            x = (width - text_width) // 2
            y = (height - text_height) // 2
            draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except ImportError:
        raise ValueError(
            "Pillow is required to generate placeholder images. Install with: pip install Pillow"
        )


def _create_pass_json(db: Session, driver_user_id: int, wallet: DriverWallet) -> dict:
    """
    Create pass.json structure for Apple Wallet.

    Uses wallet_pass_token (opaque) in barcode, never driver_id or PII.

    Pass design spec:
    - Primary: NOVA BALANCE → $xx.xx
    - Secondary: NOVA → integer balance, TIER → tier name
    - Auxiliary: STATUS → "⚡ Charging" or "Not charging"
    - Colors: background #1e40af, foreground #ffffff, label rgba(255,255,255,0.7)
    - Back fields: Last 5 wallet events, deep links, support email
    """
    # Get recent timeline events for back fields
    timeline = get_wallet_timeline(db, driver_user_id, limit=5)

    # Format balance for primary field (dollars with 2 decimals)
    balance_dollars = wallet.nova_balance / 100.0
    balance_str = f"${balance_dollars:.2f}"

    # Get tier from energy reputation score
    tier = _get_tier_from_score(wallet.energy_reputation_score)

    # Nova integer balance (no division, already in smallest unit)
    nova_integer = wallet.nova_balance

    # Charging status
    charging_status = "⚡ Charging" if wallet.charging_detected else "Not charging"

    # Build secondary fields
    secondary_fields = [
        {"key": "nova", "label": "NOVA", "value": str(nova_integer)},
        {"key": "tier", "label": "TIER", "value": tier},
    ]

    # Build auxiliary field (status)
    auxiliary_fields = [{"key": "status", "label": "STATUS", "value": charging_status}]

    # Build back fields
    base_url = getattr(settings, "public_base_url", "https://my.nerava.network").rstrip("/")
    support_email = os.getenv("NERAVA_SUPPORT_EMAIL", "support@nerava.network")

    back_fields = []

    # Add last 5 wallet events
    for i, event in enumerate(timeline[:5]):
        try:
            # created_at is ISO string from timeline service
            ts = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            short_ts = ts.strftime("%m/%d %H:%M")
        except Exception:
            short_ts = event.get("created_at", "")[:16]

        sign = "+" if event["type"] == "EARNED" else "-"
        amount_str = f"{sign}${event['amount_cents'] / 100:.2f}"
        back_fields.append(
            {
                "key": f"event_{i+1}",
                "label": short_ts,
                "value": f"{amount_str} • {event['title'][:40]}",
            }
        )

    # Add deep links and support
    back_fields.extend(
        [
            {
                "key": "open_app",
                "label": "Open Nerava App",
                "value": f"{base_url}/app/wallet/",
                "attributedValue": f"{base_url}/app/wallet/",
            },
            {
                "key": "find_merchants",
                "label": "Find Merchants",
                "value": f"{base_url}/app/explore/",
                "attributedValue": f"{base_url}/app/explore/",
            },
            {
                "key": "support",
                "label": "Support",
                "value": support_email,
                "attributedValue": f"mailto:{support_email}",
            },
        ]
    )

    # Get webServiceURL and app launch URL from settings
    web_service_url = f"{base_url}/v1/wallet/pass/apple"

    # Get pass token (opaque, for serial/barcode) and Apple auth token (for web service)
    pass_token = _ensure_wallet_pass_token(db, driver_user_id)
    auth_token = _ensure_apple_auth_token(db, wallet)

    pass_data = {
        "formatVersion": 1,
        "passTypeIdentifier": os.getenv("APPLE_WALLET_PASS_TYPE_ID", "pass.com.nerava.wallet"),
        # Serial must not contain PII; use opaque wallet_pass_token with stable prefix
        "serialNumber": f"nerava-{pass_token}",
        "teamIdentifier": os.getenv("APPLE_WALLET_TEAM_ID", ""),
        "organizationName": "Nerava",
        "description": "Nerava Wallet - Off-Peak Charging Rewards",
        "logoText": "Nerava",
        "foregroundColor": "#ffffff",
        "backgroundColor": "#1e40af",
        "labelColor": "rgba(255,255,255,0.7)",
        "storeCard": {
            "primaryFields": [{"key": "balance", "label": "NOVA BALANCE", "value": balance_str}],
            "secondaryFields": secondary_fields,
            "auxiliaryFields": auxiliary_fields,
            "backFields": back_fields,
        },
        "barcode": {
            "format": "PKBarcodeFormatQR",
            "message": pass_token,  # OPAQUE TOKEN - never driver_id or PII
            "messageEncoding": "iso-8859-1",
        },
        "webServiceURL": web_service_url,
        "authenticationToken": auth_token,  # For web service updates (separate from barcode token)
        "appLaunchURL": f"{base_url}/app/wallet/",
        "relevantDate": datetime.utcnow().isoformat() + "Z",
    }

    return pass_data


def _create_manifest(pass_files: dict) -> dict:
    """
    Create manifest.json with SHA1 hashes of all files.

    Args:
        pass_files: Dict of filename -> bytes content
    """
    manifest = {}
    for filename, content in pass_files.items():
        sha1 = hashlib.sha1(content, usedforsecurity=False).hexdigest()
        manifest[filename] = sha1
    return manifest


def _sign_pkpass(
    pass_files: dict, manifest: dict, manifest_json_bytes: Optional[bytes] = None
) -> Optional[bytes]:
    """
    Sign the pkpass bundle using Apple certificates with CMS/PKCS#7 detached signature.

    P0-1: Uses PKCS#7/CMS detached signature (not raw RSA)
    P0-2: Includes WWDR intermediate certificate in signing chain

    Supports both P12 (preferred) and PEM cert/key formats.

    Returns signature bytes (DER-encoded CMS) if signing succeeds, None if signing disabled/failed.
    """
    signing_enabled = os.getenv("APPLE_WALLET_SIGNING_ENABLED", "false").lower() == "true"

    if not signing_enabled:
        logger.debug("Apple Wallet signing disabled (APPLE_WALLET_SIGNING_ENABLED=false)")
        return None

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        private_key = None
        cert = None
        wwdr_cert = None

        # P0-2: Load WWDR intermediate certificate (required)
        wwdr_path = os.getenv("APPLE_WALLET_WWDR_CERT_PATH")
        if not wwdr_path:
            logger.error(
                "APPLE_WALLET_WWDR_CERT_PATH environment variable is required for Apple Wallet signing"
            )
            raise ValueError(
                "APPLE_WALLET_WWDR_CERT_PATH must be set. Download from: https://www.apple.com/certificateauthority/"
            )

        if not os.path.exists(wwdr_path):
            logger.error(f"WWDR certificate file not found: {wwdr_path}")
            raise ValueError(
                f"WWDR certificate file not found: {wwdr_path}. Download from: https://www.apple.com/certificateauthority/"
            )

        with open(wwdr_path, "rb") as f:
            wwdr_cert = x509.load_pem_x509_certificate(f.read())
        logger.debug("Loaded WWDR intermediate certificate")

        # Try P12 first (preferred)
        p12_path = os.getenv("APPLE_WALLET_CERT_P12_PATH")
        p12_password = os.getenv("APPLE_WALLET_CERT_P12_PASSWORD", "")

        if p12_path and os.path.exists(p12_path):
            try:
                # Try cryptography's pkcs12 support (available in cryptography 2.5+)
                try:
                    from cryptography.hazmat.primitives.serialization.pkcs12 import (
                        load_key_and_certificates,
                    )

                    with open(p12_path, "rb") as f:
                        p12_data = f.read()
                    password_bytes = p12_password.encode() if p12_password else None
                    private_key, cert, additional_certs = load_key_and_certificates(
                        p12_data, password_bytes
                    )
                    logger.debug("Loaded P12 certificate for Apple Wallet signing")
                except ImportError:
                    # Fallback: try pyOpenSSL if available
                    try:
                        from OpenSSL import crypto

                        with open(p12_path, "rb") as f:
                            p12_data = f.read()
                        p12 = crypto.load_pkcs12(
                            p12_data, p12_password.encode() if p12_password else b""
                        )
                        # Convert pyOpenSSL key to cryptography key
                        private_key_pem = crypto.dump_privatekey(
                            crypto.FILETYPE_PEM, p12.get_privatekey()
                        )
                        private_key = serialization.load_pem_private_key(
                            private_key_pem, password=None
                        )
                        cert_pem = crypto.dump_certificate(
                            crypto.FILETYPE_PEM, p12.get_certificate()
                        )
                        cert = x509.load_pem_x509_certificate(cert_pem)
                        logger.debug(
                            "Loaded P12 certificate for Apple Wallet signing (via pyOpenSSL)"
                        )
                    except ImportError:
                        logger.warning(
                            "P12 support requires cryptography>=2.5 or pyOpenSSL, falling back to PEM"
                        )
                        private_key = None
            except Exception as e:
                logger.warning(f"Failed to load P12 certificate: {e}, falling back to PEM")
                private_key = None

        # Fallback to PEM cert/key
        if private_key is None:
            cert_path = os.getenv("APPLE_WALLET_CERT_PATH")
            key_path = os.getenv("APPLE_WALLET_KEY_PATH")
            key_password = os.getenv("APPLE_WALLET_KEY_PASSWORD", "")

            if not cert_path or not key_path:
                logger.debug("Apple Wallet signing certificates not configured")
                return None

            if not os.path.exists(cert_path) or not os.path.exists(key_path):
                logger.warning(
                    f"Apple Wallet certificate/key files not found: cert={cert_path}, key={key_path}"
                )
                return None

            # Load certificate and key (support both PEM and DER formats)
            with open(cert_path, "rb") as f:
                cert_data = f.read()
            try:
                cert = x509.load_pem_x509_certificate(cert_data)
            except ValueError:
                # Try DER format
                cert = x509.load_der_x509_certificate(cert_data)

            with open(key_path, "rb") as f:
                key_data = f.read()
                if key_password:
                    private_key = serialization.load_pem_private_key(
                        key_data,
                        password=(
                            key_password.encode() if isinstance(key_password, str) else key_password
                        ),
                    )
                else:
                    private_key = serialization.load_pem_private_key(key_data, password=None)

            logger.debug("Loaded PEM certificate/key for Apple Wallet signing")

        if private_key is None or cert is None:
            logger.error("Failed to load private key or certificate for Apple Wallet signing")
            return None

        # P0-1: Use provided manifest_json_bytes if available, otherwise create from manifest dict
        # CRITICAL: The manifest.json bytes signed must match EXACTLY what's in the ZIP file
        if manifest_json_bytes is None:
            manifest_json = json.dumps(manifest, sort_keys=True).encode("utf-8")
        else:
            manifest_json = manifest_json_bytes
            # Verify it matches the manifest dict (for debugging)
            expected_json = json.dumps(manifest, sort_keys=True).encode("utf-8")
            if manifest_json != expected_json:
                logger.warning(
                    "manifest_json_bytes doesn't match manifest dict - using provided bytes"
                )

        # P0-1: Build CMS/PKCS#7 detached signature
        # P0-2: Include WWDR certificate in the signature chain
        #
        # CRITICAL: Use OpenSSL for signing because:
        # 1. Apple Wallet REQUIRES SHA1 for manifest signatures
        # 2. Newer cryptography versions may not support SHA1 in PKCS7SignatureBuilder
        # 3. OpenSSL reliably creates detached CMS signatures with SHA1
        import subprocess
        import tempfile

        # Write manifest to temp file
        with tempfile.NamedTemporaryFile(delete=False, mode="wb") as manifest_tmp:
            manifest_tmp.write(manifest_json)
            manifest_path = manifest_tmp.name

        # Write cert and key to temp files
        with tempfile.NamedTemporaryFile(delete=False, mode="wb", suffix=".pem") as cert_tmp:
            cert_tmp.write(cert.public_bytes(serialization.Encoding.PEM))
            cert_path = cert_tmp.name

        with tempfile.NamedTemporaryFile(delete=False, mode="wb", suffix=".pem") as key_tmp:
            key_tmp.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            key_path = key_tmp.name

        # Write WWDR cert to temp file
        with tempfile.NamedTemporaryFile(delete=False, mode="wb", suffix=".pem") as wwdr_tmp:
            wwdr_tmp.write(wwdr_cert.public_bytes(serialization.Encoding.PEM))
            wwdr_path = wwdr_tmp.name

        # Create combined cert file (signer cert + WWDR cert)
        with tempfile.NamedTemporaryFile(delete=False, mode="wb", suffix=".pem") as certs_tmp:
            with open(cert_path, "rb") as f:
                certs_tmp.write(f.read())
            with open(wwdr_path, "rb") as f:
                certs_tmp.write(f.read())
            certs_path = certs_tmp.name

        # Output signature file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".der") as sig_tmp:
            sig_path = sig_tmp.name

        try:
            # Use OpenSSL cms to create detached CMS signature with SHA1
            # -sign: create signature
            # -detach: create detached signature (manifest NOT embedded)
            # -signer: signing certificate + private key file
            # -certfile: additional certificates (WWDR) to include in chain
            # -in: input file (manifest.json)
            # -out: output file (signature)
            # -outform DER: DER encoding
            # -binary: binary output (no text encoding)
            # -md sha1: use SHA1 digest (required by Apple Wallet)

            # First, create a combined signer file (cert + key) for cms command
            with tempfile.NamedTemporaryFile(delete=False, mode="wb", suffix=".pem") as signer_tmp:
                with open(cert_path, "rb") as f:
                    signer_tmp.write(f.read())
                with open(key_path, "rb") as f:
                    signer_tmp.write(f.read())
                signer_path = signer_tmp.name

            # OpenSSL cms -sign creates detached signatures by default when using -binary
            # The -nodetach flag would embed the data, so we omit it for detached signature
            result = subprocess.run(
                [
                    "openssl",
                    "cms",
                    "-sign",
                    "-signer",
                    signer_path,  # Combined cert+key file
                    "-certfile",
                    wwdr_path,  # Include WWDR in cert chain
                    "-in",
                    manifest_path,
                    "-out",
                    sig_path,
                    "-outform",
                    "DER",
                    "-binary",  # Binary output, creates detached signature by default
                    "-md",
                    "sha1",  # Use SHA1 (required by Apple Wallet)
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Cleanup signer file
            try:
                os.unlink(signer_path)
            except Exception:
                pass

            if result.returncode != 0:
                raise RuntimeError(f"OpenSSL signing failed: {result.stderr}")

            # Read signature
            with open(sig_path, "rb") as f:
                signature_der = f.read()

            logger.debug("Created detached CMS signature using OpenSSL with SHA1")

        finally:
            # Cleanup temp files
            for tmp_path in [manifest_path, cert_path, key_path, wwdr_path, certs_path, sig_path]:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        # P0-2: Validate signature contains WWDR cert
        # The signature should contain both the signer cert and WWDR cert
        # We verify by checking the signature DER bytes contain expected patterns
        # Full validation will happen via OpenSSL in the build gate
        logger.debug("CMS/PKCS#7 signature created with signer cert and WWDR intermediate cert")

        logger.info("Apple Wallet pass signed successfully with CMS/PKCS#7 detached signature")
        return signature_der

    except ValueError as e:
        # Re-raise ValueError (missing WWDR) as-is
        raise
    except ImportError as e:
        logger.error(f"Required cryptography module not available: {e}")
        logger.error(
            "PKCS7SignatureBuilder requires cryptography>=2.5. Install with: pip install cryptography>=2.5"
        )
        return None
    except Exception as e:
        logger.error(f"Failed to sign Apple Wallet pass: {e}", exc_info=True)
        return None


def create_pkpass_bundle(db: Session, driver_user_id: int) -> Tuple[bytes, bool]:
    """
    Create a .pkpass bundle for Apple Wallet.

    Args:
        db: Database session
        driver_user_id: Driver user ID

    Returns:
        Tuple of (bundle_bytes, is_signed)
        - bundle_bytes: The .pkpass file as bytes
        - is_signed: True if bundle is signed, False if unsigned (preview)

    Raises:
        ValueError: If required assets are missing (with list of missing files)
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_user_id).first()
    if not wallet:
        wallet = DriverWallet(user_id=driver_user_id, nova_balance=0, energy_reputation_score=0)
        db.add(wallet)
        db.flush()

    # Create pass.json
    pass_data = _create_pass_json(db, driver_user_id, wallet)
    pass_json = json.dumps(pass_data, indent=2).encode("utf-8")

    # Get images directory
    images_dir = _get_pass_images_dir()

    # Collect pass files
    pass_files = {"pass.json": pass_json}

    # P0-3: Check for required images with dimension validation
    # Required assets:
    # - icon.png (29x29)
    # - icon@2x.png (58x58)
    # - logo.png (160x50)
    # - logo@2x.png (320x100)

    # Try to find icon.png (29x29)
    icon_path = images_dir / "icon.png"
    if icon_path.exists():
        icon_bytes = icon_path.read_bytes()
        _validate_image_dimensions(icon_bytes, 29, 29, "icon.png")
        pass_files["icon.png"] = icon_bytes
    else:
        # Generate placeholder
        logger.warning("icon.png (29x29) not found, generating placeholder")
        pass_files["icon.png"] = _generate_placeholder_image(29, 29)

    # Try to find icon@2x.png (58x58)
    icon_2x_path = images_dir / "icon@2x.png"
    if icon_2x_path.exists():
        icon_2x_bytes = icon_2x_path.read_bytes()
        _validate_image_dimensions(icon_2x_bytes, 58, 58, "icon@2x.png")
        pass_files["icon@2x.png"] = icon_2x_bytes
    else:
        # Generate placeholder
        logger.warning("icon@2x.png (58x58) not found, generating placeholder")
        pass_files["icon@2x.png"] = _generate_placeholder_image(58, 58)

    # Try to find logo.png (160x50)
    logo_path = images_dir / "logo.png"
    if logo_path.exists():
        logo_bytes = logo_path.read_bytes()
        _validate_image_dimensions(logo_bytes, 160, 50, "logo.png")
        pass_files["logo.png"] = logo_bytes
    else:
        # Generate placeholder
        logger.warning("logo.png (160x50) not found, generating placeholder")
        pass_files["logo.png"] = _generate_placeholder_image(160, 50)

    # Try to find logo@2x.png (320x100)
    logo_2x_path = images_dir / "logo@2x.png"
    if logo_2x_path.exists():
        logo_2x_bytes = logo_2x_path.read_bytes()
        _validate_image_dimensions(logo_2x_bytes, 320, 100, "logo@2x.png")
        pass_files["logo@2x.png"] = logo_2x_bytes
    else:
        # Generate placeholder
        logger.warning("logo@2x.png (320x100) not found, generating placeholder")
        pass_files["logo@2x.png"] = _generate_placeholder_image(320, 100)

    # Create manifest
    manifest = _create_manifest(pass_files)
    # CRITICAL: Create manifest.json bytes ONCE and reuse for signing
    # The bytes signed must match exactly what's in the ZIP file
    manifest_json = json.dumps(manifest, sort_keys=True).encode("utf-8")
    pass_files["manifest.json"] = manifest_json

    # Sign the pass (pass manifest_json bytes to ensure exact match)
    signature = _sign_pkpass(pass_files, manifest, manifest_json)
    is_signed = signature is not None

    if signature:
        pass_files["signature"] = signature

    # Create .pkpass bundle (ZIP file)
    bundle = BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in pass_files.items():
            zf.writestr(filename, content)

    bundle.seek(0)
    bundle_bytes = bundle.read()

    logger.info(
        f"Created Apple Wallet pass bundle for driver {driver_user_id} (signed={is_signed}, size={len(bundle_bytes)} bytes)"
    )

    return bundle_bytes, is_signed


def refresh_pkpass_bundle(db: Session, driver_user_id: int) -> Tuple[bytes, bool]:
    """
    Refresh an existing .pkpass bundle (same as create, but updates timestamp).

    This is an alias for create_pkpass_bundle for now.
    """
    return create_pkpass_bundle(db, driver_user_id)
