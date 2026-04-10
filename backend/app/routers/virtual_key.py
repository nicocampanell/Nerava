"""
Virtual Key Router — /v1/virtual-key/*

Handles Tesla Virtual Key provisioning, pairing status, and management.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.dependencies.feature_flags import require_feature_flag
from app.models import User
from app.services.virtual_key_service import get_virtual_key_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/virtual-key", tags=["virtual-key"])


# ─── Request/Response Models ────────────────────────────────────────

class ProvisionVirtualKeyRequest(BaseModel):
    vin: Optional[str] = None


class ProvisionVirtualKeyResponse(BaseModel):
    virtual_key_id: str
    provisioning_token: str
    qr_code_url: str
    expires_at: str


class ProvisioningStatusResponse(BaseModel):
    status: str  # 'pending', 'paired', 'expired', 'not_found'
    virtual_key_id: Optional[str] = None


class VirtualKeyInfo(BaseModel):
    id: str
    status: str
    tesla_vehicle_id: Optional[str] = None
    vin: Optional[str] = None
    vehicle_name: Optional[str] = None
    created_at: str
    paired_at: Optional[str] = None
    activated_at: Optional[str] = None


class ActiveVirtualKeyResponse(BaseModel):
    virtual_key: Optional[VirtualKeyInfo] = None
    arrival_tracking_enabled: bool = False


class TeslaWebhookPayload(BaseModel):
    type: str  # 'vehicle_paired', 'vehicle_location', etc.
    token: Optional[str] = None
    vehicle_id: Optional[str] = None
    vin: Optional[str] = None
    vehicle_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


# ─── Endpoints ──────────────────────────────────────────────────────

@router.post("/provision", response_model=ProvisionVirtualKeyResponse, dependencies=[Depends(require_feature_flag("FEATURE_VIRTUAL_KEY_ENABLED"))])
async def provision_virtual_key(
    req: ProvisionVirtualKeyRequest,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Start Virtual Key provisioning process.
    Returns QR code URL for Tesla app scanning.
    """
    try:
        service = get_virtual_key_service()
        virtual_key = await service.create_provisioning_request(
            db=db,
            user_id=current_user.id,
            vin=req.vin,
        )
        
        return ProvisionVirtualKeyResponse(
            virtual_key_id=str(virtual_key.id),
            provisioning_token=virtual_key.provisioning_token,
            qr_code_url=virtual_key.qr_code_url,
            expires_at=virtual_key.expires_at.isoformat() if virtual_key.expires_at else "",
        )
    except Exception as e:
        logger.error(f"Error provisioning Virtual Key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to provision Virtual Key",
        )


@router.get("/status/{provisioning_token}", response_model=ProvisioningStatusResponse, dependencies=[Depends(require_feature_flag("FEATURE_VIRTUAL_KEY_ENABLED"))])
async def check_provisioning_status(
    provisioning_token: str,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Check if Virtual Key pairing is complete.
    Frontend polls this during QR display.
    """
    try:
        service = get_virtual_key_service()
        status_result = await service.check_pairing_status(db, provisioning_token)
        
        return ProvisioningStatusResponse(
            status=status_result["status"],
            virtual_key_id=status_result.get("virtual_key_id"),
        )
    except Exception as e:
        logger.error(f"Error checking provisioning status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check provisioning status",
        )


@router.get("/active", response_model=ActiveVirtualKeyResponse, dependencies=[Depends(require_feature_flag("FEATURE_VIRTUAL_KEY_ENABLED"))])
async def get_active_virtual_key(
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Get user's active Virtual Key (if any).
    Used to determine if user has arrival tracking enabled.
    """
    try:
        service = get_virtual_key_service()
        virtual_key = await service.get_active_virtual_key(db, current_user.id)
        
        if virtual_key:
            return ActiveVirtualKeyResponse(
                virtual_key=VirtualKeyInfo(
                    id=str(virtual_key.id),
                    status=virtual_key.status,
                    tesla_vehicle_id=virtual_key.tesla_vehicle_id,
                    vin=virtual_key.vin,
                    vehicle_name=virtual_key.vehicle_name,
                    created_at=virtual_key.created_at.isoformat(),
                    paired_at=virtual_key.paired_at.isoformat() if virtual_key.paired_at else None,
                    activated_at=virtual_key.activated_at.isoformat() if virtual_key.activated_at else None,
                ),
                arrival_tracking_enabled=True,
            )
        else:
            return ActiveVirtualKeyResponse(
                virtual_key=None,
                arrival_tracking_enabled=False,
            )
    except Exception as e:
        logger.error(f"Error getting active Virtual Key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get active Virtual Key",
        )


@router.delete("/{virtual_key_id}", dependencies=[Depends(require_feature_flag("FEATURE_VIRTUAL_KEY_ENABLED"))])
async def revoke_virtual_key(
    virtual_key_id: str,
    current_user: User = Depends(get_current_driver),
    db: Session = Depends(get_db),
):
    """
    Revoke a Virtual Key.
    """
    try:
        service = get_virtual_key_service()
        success = await service.revoke_virtual_key(db, virtual_key_id, current_user.id)
        
        if success:
            return {"status": "revoked", "virtual_key_id": virtual_key_id}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Virtual Key not found",
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error revoking Virtual Key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke Virtual Key",
        )


@router.post("/webhook/tesla", dependencies=[Depends(require_feature_flag("FEATURE_VIRTUAL_KEY_ENABLED"))])
async def tesla_fleet_webhook(
    payload: TeslaWebhookPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Webhook endpoint for Tesla Fleet API callbacks.
    Receives pairing confirmations and telemetry updates.
    
    TODO: Add webhook signature verification using TESLA_WEBHOOK_SECRET
    """
    try:
        # Verify webhook signature (TODO: implement)
        # webhook_secret = settings.TESLA_WEBHOOK_SECRET
        # signature = request.headers.get("X-Tesla-Signature")
        # verify_signature(payload, signature, webhook_secret)
        
        service = get_virtual_key_service()
        
        if payload.type == "vehicle_paired":
            # Pairing completed
            if not payload.token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing provisioning token",
                )
            if not payload.vehicle_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Missing vehicle ID",
                )
            
            virtual_key = await service.confirm_pairing(
                db=db,
                provisioning_token=payload.token,
                tesla_vehicle_id=payload.vehicle_id,
                vin=payload.vin,
                vehicle_name=payload.vehicle_name,
            )
            
            logger.info(f"Virtual Key {virtual_key.id} paired with vehicle {payload.vehicle_id}")
            
            return {"status": "paired", "virtual_key_id": str(virtual_key.id)}
        
        elif payload.type == "vehicle_location":
            # Vehicle location update (for arrival detection)
            # TODO: Implement arrival detection logic
            # This will check if vehicle is near any active arrival sessions
            # and trigger arrival automatically
            logger.info(f"Vehicle location update: {payload.vehicle_id} at ({payload.latitude}, {payload.longitude})")
            
            return {"status": "received"}
        
        else:
            logger.warning(f"Unknown webhook type: {payload.type}")
            return {"status": "ignored", "type": payload.type}
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error processing Tesla webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process webhook",
        )
