"""
Wallet Pass Router

Endpoints for wallet timeline, pass status, and Apple Wallet pass management.
"""
import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.dependencies.feature_flags import require_apple_wallet_signing
from app.models import User
from app.models.domain import ApplePassRegistration, DriverWallet
from app.models.vehicle import VehicleAccount
from app.services.apple_wallet_pass import (
    _ensure_wallet_pass_token,
    create_pkpass_bundle,
    refresh_pkpass_bundle,
)
from app.services.google_wallet_service import (
    GoogleWalletNotConfigured,
    create_or_get_google_wallet_object,
    ensure_google_wallet_class,
    generate_google_wallet_add_link,
)
from app.services.wallet_timeline import get_wallet_timeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/wallet", tags=["wallet-pass"])


def _validate_apple_wallet_config() -> Tuple[bool, Optional[str], List[str]]:
    """
    Validate Apple Wallet signing configuration.
    
    Returns:
        Tuple of (is_valid, error_code, missing_keys)
        - is_valid: True if configuration is valid
        - error_code: Error code if invalid (APPLE_WALLET_SIGNING_DISABLED or APPLE_WALLET_SIGNING_MISCONFIGURED)
        - missing_keys: List of missing environment variable keys
    """
    signing_enabled = os.getenv("APPLE_WALLET_SIGNING_ENABLED", "false").lower() == "true"
    
    if not signing_enabled:
        return (False, "APPLE_WALLET_SIGNING_DISABLED", [])
    
    missing = []
    
    # Required env vars
    required = ["APPLE_WALLET_PASS_TYPE_ID", "APPLE_WALLET_TEAM_ID"]
    for key in required:
        if not os.getenv(key):
            missing.append(key)
    
    # P0-2: WWDR certificate is required
    wwdr_path = os.getenv("APPLE_WALLET_WWDR_CERT_PATH")
    if not wwdr_path:
        missing.append("APPLE_WALLET_WWDR_CERT_PATH (required - download from https://www.apple.com/certificateauthority/)")
    elif not os.path.exists(wwdr_path):
        missing.append(f"APPLE_WALLET_WWDR_CERT_PATH file not found: {wwdr_path}")
    
    # Check cert/key or P12
    has_p12 = bool(os.getenv("APPLE_WALLET_CERT_P12_PATH"))
    has_pem = bool(os.getenv("APPLE_WALLET_CERT_PATH") and os.getenv("APPLE_WALLET_KEY_PATH"))
    
    if not (has_p12 or has_pem):
        missing.append("APPLE_WALLET_CERT_P12_PATH (or APPLE_WALLET_CERT_PATH + APPLE_WALLET_KEY_PATH)")
    
    # Check if files exist
    if has_p12:
        p12_path = os.getenv("APPLE_WALLET_CERT_P12_PATH")
        if p12_path and not os.path.exists(p12_path):
            missing.append(f"APPLE_WALLET_CERT_P12_PATH file not found: {p12_path}")
    elif has_pem:
        cert_path = os.getenv("APPLE_WALLET_CERT_PATH")
        key_path = os.getenv("APPLE_WALLET_KEY_PATH")
        if cert_path and not os.path.exists(cert_path):
            missing.append(f"APPLE_WALLET_CERT_PATH file not found: {cert_path}")
        if key_path and not os.path.exists(key_path):
            missing.append(f"APPLE_WALLET_KEY_PATH file not found: {key_path}")
    
    if missing:
        return (False, "APPLE_WALLET_SIGNING_MISCONFIGURED", missing)
    
    return (True, None, [])


class TimelineEvent(BaseModel):
    """Timeline event response model"""
    id: str
    type: str  # "EARNED" | "SPENT"
    amount_cents: int
    title: str
    subtitle: str
    created_at: str  # ISO string
    merchant_id: Optional[str] = None
    redemption_id: Optional[str] = None


class PassStatusResponse(BaseModel):
    """Pass status response"""
    wallet_activity_updated_at: Optional[str]  # ISO string or null
    wallet_pass_last_generated_at: Optional[str]  # ISO string or null
    needs_refresh: bool


class EligibilityResponse(BaseModel):
    """Apple Wallet eligibility response"""
    eligible: bool
    reason: Optional[str] = None


class GoogleEligibilityResponse(BaseModel):
    """Google Wallet eligibility response"""
    eligible: bool
    reason: Optional[str] = None


class GoogleWalletLinkResponse(BaseModel):
    """Google Wallet link response"""
    object_id: str
    state: str
    add_to_google_wallet_url: Optional[str] = None


@router.get("/pass/google/eligibility", response_model=GoogleEligibilityResponse)
def get_google_wallet_eligibility(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Check if driver is eligible for Google Wallet pass.
    
    Eligibility requires Google Wallet configuration to be present.
    """
    try:
        ensure_google_wallet_class()
        return GoogleEligibilityResponse(eligible=True)
    except GoogleWalletNotConfigured:
        return GoogleEligibilityResponse(
            eligible=False,
            reason="Google Wallet is not configured for this environment",
        )
    except Exception as e:
        logger.error(f"Google Wallet eligibility check failed: {e}", exc_info=True)
        return GoogleEligibilityResponse(
            eligible=False,
            reason="Google Wallet is temporarily unavailable",
        )


@router.post("/pass/google/create", response_model=GoogleWalletLinkResponse, dependencies=[Depends(require_apple_wallet_signing)])
def create_google_wallet_pass(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Create a Google Wallet pass for the driver's wallet.
    
    - barcode uses wallet_pass_token (opaque)
    - link points to /app/wallet/
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        wallet = DriverWallet(
            user_id=user.id,
            nova_balance=0,
            energy_reputation_score=0,
        )
        db.add(wallet)
        db.flush()

    token = _ensure_wallet_pass_token(db, user.id)

    try:
        link = create_or_get_google_wallet_object(db, wallet, token)
    except GoogleWalletNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "GOOGLE_WALLET_DISABLED",
                "message": "Google Wallet is not configured for this environment.",
            },
        )
    except Exception as e:
        logger.error(f"Failed to create Google Wallet object: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "GOOGLE_WALLET_CREATION_FAILED",
                "message": "Failed to create Google Wallet pass",
            },
        )

    # Generate Add-to-Wallet link (non-blocking - if it fails, return object_id without link)
    add_link = None
    try:
        add_link = generate_google_wallet_add_link(link.object_id)
    except Exception as e:
        logger.warning(f"Failed to generate Google Wallet Add-to-Wallet link: {e}", exc_info=True)
        # Continue without link - object creation succeeded

    # P3: HubSpot tracking (dry run)
    try:
        from app.events.hubspot_adapter import adapt_wallet_pass_install_event
        from app.services.hubspot import track_event
        hubspot_payload = adapt_wallet_pass_install_event({
            "user_id": str(user.id),
            "pass_type": "google",
            "installed_at": datetime.utcnow().isoformat()
        })
        track_event(db, "wallet_pass_install", hubspot_payload)
        db.commit()
    except Exception as e:
        # Don't fail pass creation if HubSpot tracking fails
        logger.warning(f"HubSpot tracking failed: {e}")

    return GoogleWalletLinkResponse(
        object_id=link.object_id,
        state=link.state,
        add_to_google_wallet_url=add_link,
    )


@router.post("/pass/google/refresh", response_model=GoogleWalletLinkResponse)
def refresh_google_wallet_pass(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Refresh Google Wallet pass for the driver's wallet.
    
    No separate push channel; updates happen immediately.
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "GOOGLE_WALLET_NO_WALLET",
                "message": "Driver wallet not found",
            },
        )

    if not wallet.wallet_pass_token:
        token = _ensure_wallet_pass_token(db, user.id)
    else:
        token = wallet.wallet_pass_token

    try:
        link = create_or_get_google_wallet_object(db, wallet, token)
    except GoogleWalletNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "GOOGLE_WALLET_DISABLED",
                "message": "Google Wallet is not configured for this environment.",
            },
        )
    except Exception as e:
        logger.error(f"Failed to refresh Google Wallet object: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "GOOGLE_WALLET_REFRESH_FAILED",
                "message": "Failed to refresh Google Wallet pass",
            },
        )

    # Generate Add-to-Wallet link (non-blocking - if it fails, return object_id without link)
    add_link = None
    try:
        add_link = generate_google_wallet_add_link(link.object_id)
    except Exception as e:
        logger.warning(f"Failed to generate Google Wallet Add-to-Wallet link: {e}", exc_info=True)
        # Continue without link - object refresh succeeded

    return GoogleWalletLinkResponse(
        object_id=link.object_id,
        state=link.state,
        add_to_google_wallet_url=add_link,
    )


class PreviewHeaderError(BaseModel):
    error: str
    message: str


@router.get("/timeline", response_model=List[TimelineEvent])
def get_timeline(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get wallet timeline (earned/spent events).
    
    Returns unified timeline of wallet activity:
    - EARNED events from NovaTransaction (driver_earn)
    - SPENT events from MerchantRedemption (excludes NovaTransaction driver_redeem to avoid duplicates)
    """
    try:
        events = get_wallet_timeline(db, driver_user_id=user.id, limit=limit)
        return events
    except Exception as e:
        logger.error(f"Failed to get wallet timeline for user {user.id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "TIMELINE_FETCH_FAILED",
                "message": f"Failed to fetch wallet timeline: {str(e)}"
            }
        )


class WalletStatusResponse(BaseModel):
    """Wallet status including charging state"""
    charging_detected: bool
    charging_detected_at: Optional[str]  # ISO string or null
    message: str


@router.get("/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get wallet status including charging detection state.
    
    Returns:
    - charging_detected: bool
    - charging_detected_at: ISO string or null
    - message: "Charging detected. Nova is accruing." if charging, else ""
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    
    if not wallet or not wallet.charging_detected:
        return WalletStatusResponse(
            charging_detected=False,
            charging_detected_at=None,
            message=""
        )
    
    # Charging detected
    detected_at_iso = wallet.charging_detected_at.isoformat() + "Z" if wallet.charging_detected_at else None
    
    return WalletStatusResponse(
        charging_detected=True,
        charging_detected_at=detected_at_iso,
        message="Charging detected. Nova is accruing."
    )


@router.get("/pass/status", response_model=PassStatusResponse)
def get_pass_status(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get Apple Wallet pass refresh status.
    
    Returns:
    - wallet_activity_updated_at: timestamp of last wallet activity (earn/spend)
    - wallet_pass_last_generated_at: timestamp when pass was last generated
    - needs_refresh: true if activity updated after pass was generated
    
    Logic:
    - If wallet_activity_updated_at is null -> needs_refresh=false
    - If wallet_pass_last_generated_at is null AND wallet_activity_updated_at not null -> needs_refresh=true
    - Else needs_refresh = wallet_activity_updated_at > wallet_pass_last_generated_at
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    
    if not wallet:
        # Wallet doesn't exist yet - no activity, no pass generated
        return PassStatusResponse(
            wallet_activity_updated_at=None,
            wallet_pass_last_generated_at=None,
            needs_refresh=False
        )
    
    activity_at = wallet.wallet_activity_updated_at.isoformat() if wallet.wallet_activity_updated_at else None
    pass_at = wallet.wallet_pass_last_generated_at.isoformat() if wallet.wallet_pass_last_generated_at else None
    
    # Determine needs_refresh
    if wallet.wallet_activity_updated_at is None:
        needs_refresh = False
    elif wallet.wallet_pass_last_generated_at is None:
        needs_refresh = True
    else:
        needs_refresh = wallet.wallet_activity_updated_at > wallet.wallet_pass_last_generated_at
    
    return PassStatusResponse(
        wallet_activity_updated_at=activity_at,
        wallet_pass_last_generated_at=pass_at,
        needs_refresh=needs_refresh
    )


class WalletStatusResponse(BaseModel):
    """Wallet status including charging state"""
    charging_detected: bool
    charging_detected_at: Optional[str]  # ISO string or null
    message: str


@router.get("/status", response_model=WalletStatusResponse)
def get_wallet_status(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Get wallet status including charging detection state.
    
    Returns:
    - charging_detected: bool
    - charging_detected_at: ISO string or null
    - message: "Charging detected. Nova is accruing." if charging, else ""
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    
    if not wallet or not wallet.charging_detected:
        return WalletStatusResponse(
            charging_detected=False,
            charging_detected_at=None,
            message=""
        )
    
    # Charging detected
    detected_at_iso = wallet.charging_detected_at.isoformat() + "Z" if wallet.charging_detected_at else None
    
    return WalletStatusResponse(
        charging_detected=True,
        charging_detected_at=detected_at_iso,
        message="Charging detected. Nova is accruing."
    )


@router.get("/pass/apple/eligibility", response_model=EligibilityResponse)
def get_apple_wallet_eligibility(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Check if driver is eligible for Apple Wallet pass.
    
    Driver is eligible only if they have VehicleAccount (Smartcar connected).
    """
    vehicle_account = db.query(VehicleAccount).filter(
        VehicleAccount.user_id == user.id,
        VehicleAccount.is_active == True
    ).first()
    
    if vehicle_account:
        return EligibilityResponse(eligible=True)
    else:
        return EligibilityResponse(
            eligible=False,
            reason="Connect your EV first"
        )


@router.post("/pass/apple/create", dependencies=[Depends(require_apple_wallet_signing)])
def create_apple_pass(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Create Apple Wallet pass for driver.
    
    Returns:
    - 200: Signed .pkpass file (if signing enabled)
    - 501: Structured error if signing disabled/misconfigured
    
    Eligibility:
    - Driver must have VehicleAccount (Smartcar connected)
    """
    # Check eligibility
    vehicle_account = db.query(VehicleAccount).filter(
        VehicleAccount.user_id == user.id,
        VehicleAccount.is_active == True
    ).first()
    
    if not vehicle_account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "APPLE_WALLET_INELIGIBLE",
                "message": "Connect your EV first"
            }
        )
    
    # Validate signing configuration
    is_valid, error_code, missing_keys = _validate_apple_wallet_config()
    
    if not is_valid:
        error_message = "Apple Wallet pass signing is not enabled on this environment."
        if missing_keys:
            error_message = f"Missing required configuration: {', '.join(missing_keys)}"
        
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": error_code,
                "message": error_message
            }
        )
    
    # Generate pass bundle
    try:
        bundle_bytes, is_signed = create_pkpass_bundle(db, user.id)
        
        if not is_signed:
            # This shouldn't happen if we checked above, but handle it
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "APPLE_WALLET_SIGNING_DISABLED",
                    "message": "Apple Wallet pass signing failed."
                }
            )
        
        # Update wallet_pass_last_generated_at
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
        if wallet:
            wallet.wallet_pass_last_generated_at = datetime.utcnow()
            db.commit()
        
        # P3: HubSpot tracking (dry run)
        try:
            from app.events.hubspot_adapter import adapt_wallet_pass_install_event
            from app.services.hubspot import track_event
            hubspot_payload = adapt_wallet_pass_install_event({
                "user_id": str(user.id),
                "pass_type": "apple",
                "installed_at": datetime.utcnow().isoformat()
            })
            track_event(db, "wallet_pass_install", hubspot_payload)
            db.commit()
        except Exception as e:
            # Don't fail pass creation if HubSpot tracking fails
            logger.warning(f"HubSpot tracking failed: {e}")
        
        # Return .pkpass file
        # Safari iOS requires:
        # 1. Content-Type must be exactly application/vnd.apple.pkpass (no charset)
        # 2. Content-Disposition header for reliable iOS Safari handling
        # 3. Content-Length header
        from fastapi import Response as FastAPIResponse
        return FastAPIResponse(
            content=bundle_bytes,
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Length": str(len(bundle_bytes)),
                "Content-Disposition": 'attachment; filename="nerava.pkpass"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
    except ValueError as e:
        # Asset validation error
        error_msg = str(e)
        if "Missing required pass assets" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "APPLE_WALLET_ASSETS_MISSING",
                    "message": error_msg
                }
            )
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create Apple Wallet pass: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "PASS_GENERATION_FAILED",
                "message": "Failed to generate Apple Wallet pass"
            }
        )


@router.post("/pass/apple/refresh", dependencies=[Depends(require_apple_wallet_signing)])
def refresh_apple_pass(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Refresh Apple Wallet pass for driver.
    
    Same as create, but updates existing pass.
    """
    # Check eligibility
    vehicle_account = db.query(VehicleAccount).filter(
        VehicleAccount.user_id == user.id,
        VehicleAccount.is_active == True
    ).first()
    
    if not vehicle_account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "APPLE_WALLET_INELIGIBLE",
                "message": "Connect your EV first"
            }
        )
    
    # Check if signing is enabled
    signing_enabled = os.getenv("APPLE_WALLET_SIGNING_ENABLED", "false").lower() == "true"
    cert_path = os.getenv("APPLE_WALLET_CERT_PATH")
    key_path = os.getenv("APPLE_WALLET_KEY_PATH")
    
    if not signing_enabled or not cert_path or not key_path or not os.path.exists(cert_path) or not os.path.exists(key_path):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "APPLE_WALLET_SIGNING_DISABLED",
                "message": "Apple Wallet pass signing is not enabled on this environment."
            }
        )
    
    # Generate refreshed pass bundle
    try:
        bundle_bytes, is_signed = refresh_pkpass_bundle(db, user.id)
        
        if not is_signed:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "APPLE_WALLET_SIGNING_DISABLED",
                    "message": "Apple Wallet pass signing failed."
                }
            )
        
        # Update wallet_pass_last_generated_at
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
        if wallet:
            wallet.wallet_pass_last_generated_at = datetime.utcnow()
            db.commit()
        
        # Return .pkpass file
        # Safari iOS requires:
        # 1. Content-Type must be exactly application/vnd.apple.pkpass (no charset)
        # 2. Content-Disposition header for reliable iOS Safari handling
        # 3. Content-Length header
        from fastapi import Response as FastAPIResponse
        return FastAPIResponse(
            content=bundle_bytes,
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Length": str(len(bundle_bytes)),
                "Content-Disposition": 'attachment; filename="nerava.pkpass"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh Apple Wallet pass: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "PASS_GENERATION_FAILED",
                "message": "Failed to refresh Apple Wallet pass"
            }
        )


class ReinstallRequest(BaseModel):
    platform: str  # "apple" or "google"


@router.post("/pass/reinstall")
async def reinstall_wallet_pass(
    request: ReinstallRequest,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Reinstall wallet pass for Apple or Google Wallet.
    
    This endpoint triggers regeneration of the pass and returns the install URL or file.
    For Apple: returns the same flow as /pass/apple/create (downloads .pkpass)
    For Google: returns add_link from /pass/google/create
    """
    # Check eligibility (vehicle connected)
    vehicle_account = db.query(VehicleAccount).filter(
        VehicleAccount.user_id == user.id,
        VehicleAccount.is_active == True
    ).first()
    
    if not vehicle_account:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "WALLET_PASS_INELIGIBLE",
                "message": "Connect your EV first"
            }
        )
    
    platform = request.platform.lower()
    
    if platform == "apple":
        # Reuse the create endpoint logic
        # Check signing configuration
        is_valid, error_code, missing_keys = _validate_apple_wallet_config()
        
        if not is_valid:
            error_message = "Apple Wallet pass signing is not enabled on this environment."
            if missing_keys:
                error_message = f"Missing required configuration: {', '.join(missing_keys)}"
            
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": error_code,
                    "message": error_message
                }
            )
        
        # Generate pass bundle
        try:
            bundle_bytes, is_signed = create_pkpass_bundle(db, user.id)
            
            if not is_signed:
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail={
                        "error": "APPLE_WALLET_SIGNING_DISABLED",
                        "message": "Apple Wallet pass signing failed."
                    }
                )
            
            # Update wallet_pass_last_generated_at
            wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
            if wallet:
                wallet.wallet_pass_last_generated_at = datetime.utcnow()
                db.commit()
            
            # Return .pkpass file
            return Response(
                content=bundle_bytes,
                media_type="application/vnd.apple.pkpass",
                headers={
                    "Content-Disposition": 'attachment; filename="nerava.pkpass"'
                }
            )
            
        except ValueError as e:
            error_msg = str(e)
            if "Missing required pass assets" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_501_NOT_IMPLEMENTED,
                    detail={
                        "error": "APPLE_WALLET_ASSETS_MISSING",
                        "message": error_msg
                    }
                )
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to reinstall Apple Wallet pass: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "PASS_GENERATION_FAILED",
                    "message": "Failed to reinstall Apple Wallet pass"
                }
            )
    
    elif platform == "google":
        # Reuse the create endpoint logic
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
        if not wallet:
            wallet = DriverWallet(
                user_id=user.id,
                nova_balance=0,
                energy_reputation_score=0,
            )
            db.add(wallet)
            db.flush()

        token = _ensure_wallet_pass_token(db, user.id)

        try:
            link = create_or_get_google_wallet_object(db, wallet, token)
        except GoogleWalletNotConfigured:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "GOOGLE_WALLET_DISABLED",
                    "message": "Google Wallet is not configured for this environment.",
                },
            )
        except Exception as e:
            logger.error(f"Failed to reinstall Google Wallet object: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "GOOGLE_WALLET_CREATION_FAILED",
                    "message": "Failed to reinstall Google Wallet pass",
                },
            )

        # Generate Add-to-Wallet link
        add_link = None
        try:
            add_link = generate_google_wallet_add_link(link.object_id)
        except Exception as e:
            logger.warning(f"Failed to generate Google Wallet Add-to-Wallet link: {e}", exc_info=True)

        return GoogleWalletLinkResponse(
            object_id=link.object_id,
            state=link.state,
            add_to_google_wallet_url=add_link,
        )
    
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "INVALID_PLATFORM",
                "message": f"Platform must be 'apple' or 'google', got '{platform}'"
            }
        )


@router.get("/pass/apple/create", dependencies=[Depends(require_apple_wallet_signing)])
@router.get("/pass/apple/create.pkpass", dependencies=[Depends(require_apple_wallet_signing)])
def create_apple_pass_get(
    request: Request,
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    GET endpoint for Apple Wallet pass creation (for direct Safari access).
    
    Safari on iPhone requires user interaction to download files. This endpoint:
    - Returns HTML page with download button if Accept header includes text/html
    - Returns pkpass file directly if Accept header requests application/vnd.apple.pkpass
    - Also handles .pkpass extension in URL for better Safari recognition
    """
    # Check Accept header, query parameter, and URL path to determine response type
    accept_header = request.headers.get("Accept", "")
    download_param = request.query_params.get("download", "")
    is_pkpass_url = request.url.path.endswith(".pkpass")
    
    # If explicitly requesting pkpass file, download param set, or .pkpass extension, return it directly
    if "application/vnd.apple.pkpass" in accept_header or download_param == "true" or is_pkpass_url:
        return create_apple_pass(user, db)
    
    # Otherwise, return HTML page with download button (requires user tap)
    from fastapi.responses import HTMLResponse
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Add to Apple Wallet</title>
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, sans-serif; 
                text-align: center; 
                padding: 2rem; 
                max-width: 600px;
                margin: 0 auto;
            }
            .button { 
                display: inline-block; 
                padding: 1rem 2rem; 
                background: #007AFF; 
                color: white; 
                text-decoration: none; 
                border-radius: 8px; 
                margin: 1rem;
                font-size: 1.1rem;
            }
            .button:hover {
                background: #0051D5;
            }
            h1 {
                color: #1e40af;
            }
        </style>
    </head>
    <body>
        <h1>Nerava Wallet Pass</h1>
        <p>Tap the button below to add your Nerava wallet pass to Apple Wallet:</p>
        <form action="/v1/wallet/pass/apple/create.pkpass" method="GET" style="display: inline;">
            <button type="submit" class="button" style="border: none; cursor: pointer;">Add to Apple Wallet</button>
        </form>
        <p style="color: #666; font-size: 0.9rem; margin-top: 2rem;">
            This will download your wallet pass and prompt you to add it to Apple Wallet.
        </p>
        <p style="color: #999; font-size: 0.8rem; margin-top: 1rem;">
            If the button doesn't work, try <a href="/v1/wallet/pass/apple/create.pkpass" style="color: #007AFF;">opening this link directly</a>.
        </p>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@router.get("/pass/apple/preview", dependencies=[Depends(require_apple_wallet_signing)])
def preview_apple_pass(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Preview Apple Wallet pass without requiring signing.
    
    Returns unsigned ZIP bundle (pass.json + images, no signature).
    Cannot be added to Wallet, but useful for debugging pass structure.
    """
    try:
        bundle_bytes, is_signed = create_pkpass_bundle(db, user.id)
        # For preview, return as ZIP (not installable)
        return Response(
            content=bundle_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="nerava-wallet-preview.zip"',
                "X-Nerava-Pass-Preview": "true",
                "X-Nerava-Pass-Signed": "true" if is_signed else "false",
            },
        )
    except ValueError as e:
        # Asset validation error
        error_msg = str(e)
        if "Missing required pass assets" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "APPLE_WALLET_ASSETS_MISSING",
                    "message": error_msg
                }
            )
        raise
    except Exception as e:
        logger.error(f"Failed to generate Apple Wallet preview pass: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "PASS_PREVIEW_FAILED",
                "message": "Failed to generate Apple Wallet preview pass",
            },
        )


def _extract_auth_token(request) -> str:
    """
    Extract Apple PassKit authentication token from headers.
    
    Supports:
    - AuthenticationToken: <token>
    - Authorization: ApplePass <token>
    """
    auth_token = request.headers.get("AuthenticationToken")
    if auth_token:
        return auth_token

    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.startswith("ApplePass "):
        return auth_header.split(" ", 1)[1].strip()

    return ""


def _get_wallet_by_serial(db: Session, serial: str) -> DriverWallet:
    """
    Resolve PassKit serial number to DriverWallet using wallet_pass_token.
    
    Serial format (no PII):
    - nerava-<wallet_pass_token>
    """
    prefix = "nerava-"
    if not serial or not serial.startswith(prefix):
        return None
    token = serial[len(prefix) :]
    if not token:
        return None
    return (
        db.query(DriverWallet)
        .filter(DriverWallet.wallet_pass_token == token)
        .first()
    )


def _validate_passkit_auth(db: Session, wallet: DriverWallet, request) -> None:
    """
    Validate PassKit authentication token against stored encrypted token.
    """
    from app.services.token_encryption import TokenDecryptionError, decrypt_token

    client_token = _extract_auth_token(request)
    if not client_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "APPLE_WALLET_AUTH_REQUIRED",
                "message": "Missing AuthenticationToken header",
            },
        )

    if not wallet.apple_authentication_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "APPLE_WALLET_AUTH_INVALID",
                "message": "Pass authentication token not provisioned",
            },
        )

    try:
        stored_token = decrypt_token(wallet.apple_authentication_token)
    except TokenDecryptionError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "APPLE_WALLET_AUTH_INVALID",
                "message": "Failed to validate authentication token",
            },
        )

    if client_token != stored_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "APPLE_WALLET_AUTH_INVALID",
                "message": "Invalid authentication token",
            },
        )


@router.post(
    "/pass/apple/devices/{deviceLibraryId}/registrations/{passTypeId}/{serial}",
    status_code=status.HTTP_201_CREATED,
)
async def register_apple_pass_device(
    deviceLibraryId: str,
    passTypeId: str,
    serial: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Apple PassKit: Register a device for pass updates.
    
    Validates AuthenticationToken and stores/upserts ApplePassRegistration.
    """
    # Resolve wallet by serial
    wallet = _get_wallet_by_serial(db, serial)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "APPLE_WALLET_PASS_NOT_FOUND",
                "message": "Pass not found for serial",
            },
        )

    # Validate auth token
    _validate_passkit_auth(db, wallet, request)

    # Read pushToken from body (Apple sends JSON with "pushToken")
    push_token = None
    try:
        import json as json_lib
        body_bytes = await request.body()
        if body_bytes:
            body = json_lib.loads(body_bytes)
            push_token = body.get("pushToken")
    except Exception:
        # If body parsing fails, continue without push_token
        pass

    # Upsert registration
    registration = (
        db.query(ApplePassRegistration)
        .filter(
            ApplePassRegistration.driver_wallet_id == wallet.user_id,
            ApplePassRegistration.device_library_identifier == deviceLibraryId,
            ApplePassRegistration.pass_type_identifier == passTypeId,
            ApplePassRegistration.serial_number == serial,
        )
        .first()
    )

    now = datetime.utcnow()
    import uuid
    
    is_new_registration = False

    if registration:
        registration.push_token = push_token or registration.push_token
        registration.last_seen_at = now
        registration.is_active = True
    else:
        registration = ApplePassRegistration(
            id=str(uuid.uuid4()),
            driver_wallet_id=wallet.user_id,
            device_library_identifier=deviceLibraryId,
            push_token=push_token,
            pass_type_identifier=passTypeId,
            serial_number=serial,
            created_at=now,
            last_seen_at=now,
            is_active=True,
        )
        is_new_registration = True
        db.add(registration)

    db.commit()
    
    # Emit wallet_pass_installed event for new registrations (non-blocking)
    if is_new_registration:
        try:
            from app.events.domain import WalletPassInstalledEvent
            from app.events.outbox import store_outbox_event
            event = WalletPassInstalledEvent(
                user_id=str(wallet.user_id),
                pass_type="apple",
                installed_at=datetime.utcnow()
            )
            store_outbox_event(db, event)
        except Exception as e:
            logger.warning(f"Failed to emit wallet_pass_installed event: {e}")

    # Per Apple PassKit spec: POST registration returns HTTP 201 with empty body
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/pass/apple/devices/{deviceLibraryId}/registrations/{passTypeId}/{serial}",
    status_code=status.HTTP_200_OK,
)
def unregister_apple_pass_device(
    deviceLibraryId: str,
    passTypeId: str,
    serial: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Apple PassKit: Unregister a device from pass updates.
    """
    wallet = _get_wallet_by_serial(db, serial)
    if not wallet:
        # Per spec, 200 is acceptable even if registration doesn't exist
        return {"status": "OK"}

    _validate_passkit_auth(db, wallet, request)

    registration = (
        db.query(ApplePassRegistration)
        .filter(
            ApplePassRegistration.driver_wallet_id == wallet.user_id,
            ApplePassRegistration.device_library_identifier == deviceLibraryId,
            ApplePassRegistration.pass_type_identifier == passTypeId,
            ApplePassRegistration.serial_number == serial,
        )
        .first()
    )

    now = datetime.utcnow()

    if registration:
        registration.is_active = False
        registration.last_seen_at = now
        db.commit()

    # Per Apple PassKit spec: DELETE registration returns HTTP 200 with empty body
    return Response(status_code=status.HTTP_200_OK)


@router.get(
    "/pass/apple/devices/{deviceLibraryId}/registrations/{passTypeId}",
    status_code=status.HTTP_200_OK,
)
def list_apple_pass_registrations(
    deviceLibraryId: str,
    passTypeId: str,
    passesUpdatedSince: Optional[str] = Query(None, alias="passesUpdatedSince"),  # noqa: N803 - match Apple spec
    request: Request = None,  # FastAPI will inject this
    db: Session = Depends(get_db),
):
    """
    Apple PassKit: List serials for a device that should be updated.
    
    Implements passesUpdatedSince filtering:
    - Parse passesUpdatedSince as unix timestamp (int)
    - Only return serialNumbers for wallets where:
      - wallet_activity_updated_at > passesUpdatedSince OR
      - wallet_pass_last_generated_at is NULL
    - Return lastUpdated as unix timestamp (int), not ISO
    """
    # We don't know which serial Apple is asking about here, but Apple will call
    # GET /passes/{passTypeId}/{serial} separately per serial, where we validate auth again.
    # Here, we just ensure the AuthenticationToken is present (basic guard).
    token = _extract_auth_token(request) if request else ""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "APPLE_WALLET_AUTH_REQUIRED",
                "message": "Missing AuthenticationToken header",
            },
        )

    # Find all active registrations for this device + passTypeId
    regs = (
        db.query(ApplePassRegistration)
        .filter(
            ApplePassRegistration.device_library_identifier == deviceLibraryId,
            ApplePassRegistration.pass_type_identifier == passTypeId,
            ApplePassRegistration.is_active == True,  # noqa: E712
        )
        .all()
    )

    # Parse passesUpdatedSince if provided (unix timestamp)
    updated_since_dt = None
    if passesUpdatedSince:
        try:
            updated_since_ts = int(passesUpdatedSince)
            updated_since_dt = datetime.utcfromtimestamp(updated_since_ts)
        except (ValueError, TypeError, OSError):
            # Invalid timestamp - ignore filter
            logger.warning(f"Invalid passesUpdatedSince: {passesUpdatedSince}")
            updated_since_dt = None

    # Filter serials based on wallet activity timestamps
    serials = []
    now = datetime.utcnow()
    now_ts = int(now.timestamp())

    for r in regs:
        # Get wallet for this registration
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == r.driver_wallet_id).first()
        if not wallet:
            continue

        # Apply passesUpdatedSince filter
        if updated_since_dt:
            # Only include if wallet_activity_updated_at > passesUpdatedSince OR wallet_pass_last_generated_at is NULL
            activity_updated = wallet.wallet_activity_updated_at
            pass_generated = wallet.wallet_pass_last_generated_at

            if activity_updated and activity_updated > updated_since_dt:
                # Activity updated after the threshold - include
                serials.append(r.serial_number)
            elif pass_generated is None:
                # Pass never generated - include
                serials.append(r.serial_number)
            # Otherwise, skip (pass was generated and no activity since threshold)
        else:
            # No filter - include all
            serials.append(r.serial_number)

        # Update last_seen_at
        r.last_seen_at = now

    if regs:
        db.commit()

    # Return lastUpdated as unix timestamp (int), not ISO
    return {
        "serialNumbers": serials,
        "lastUpdated": str(now_ts),  # Apple expects string representation of unix timestamp
    }


@router.get(
    "/pass/apple/passes/{passTypeId}/{serial}",
    status_code=status.HTTP_200_OK,
)
def get_apple_pass_for_device(
    passTypeId: str,
    serial: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Apple PassKit: Return the signed .pkpass for a given serial.
    
    Rules:
    - Validate AuthenticationToken
    - Look up wallet by serialNumber (nerava-<wallet_pass_token>)
    - Update last_seen_at on any matching registrations
    - Return ONLY signed pkpass (no unsigned responses)
    - Update wallet_pass_last_generated_at on fetch
    """
    wallet = _get_wallet_by_serial(db, serial)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "APPLE_WALLET_PASS_NOT_FOUND",
                "message": "Pass not found for serial",
            },
        )

    _validate_passkit_auth(db, wallet, request)

    # Ensure signing is enabled; PassKit web service must never serve unsigned passes
    import os as _os

    signing_enabled = _os.getenv("APPLE_WALLET_SIGNING_ENABLED", "false").lower() == "true"
    cert_path = _os.getenv("APPLE_WALLET_CERT_PATH")
    key_path = _os.getenv("APPLE_WALLET_KEY_PATH")

    if (
        not signing_enabled
        or not cert_path
        or not key_path
        or not _os.path.exists(cert_path)
        or not _os.path.exists(key_path)
    ):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={
                "error": "APPLE_WALLET_SIGNING_DISABLED",
                "message": "Apple Wallet pass signing is not enabled on this environment.",
            },
        )

    try:
        bundle_bytes, is_signed = refresh_pkpass_bundle(db, wallet.user_id)
        if not is_signed:
            # Unsigned pkpass must never pretend to be installable
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail={
                    "error": "APPLE_WALLET_SIGNING_DISABLED",
                    "message": "Apple Wallet pass signing failed.",
                },
            )

        # Update wallet_pass_last_generated_at
        wallet.wallet_pass_last_generated_at = datetime.utcnow()

        # Update last_seen_at on registrations for this serial
        now = datetime.utcnow()
        regs = (
            db.query(ApplePassRegistration)
            .filter(
                ApplePassRegistration.driver_wallet_id == wallet.user_id,
                ApplePassRegistration.serial_number == serial,
                ApplePassRegistration.is_active == True,  # noqa: E712
            )
            .all()
        )
        for r in regs:
            r.last_seen_at = now

        db.commit()

        return Response(
            content=bundle_bytes,
            media_type="application/vnd.apple.pkpass",
            headers={
                "Content-Disposition": 'attachment; filename="nerava.pkpass"'
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve Apple Wallet pass via PassKit service: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "PASS_GENERATION_FAILED",
                "message": "Failed to generate Apple Wallet pass",
            },
        )
