"""
Demo Charging Router

Sandbox-only endpoints for simulating charging detection and Nova accrual.
Gated behind DEMO_MODE=true or DEMO_QR_ENABLED=true.
"""
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.driver import get_current_driver
from app.models import User
from app.models.domain import DriverWallet
from app.services.wallet_activity import mark_wallet_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/demo", tags=["demo-charging"])


def _is_demo_enabled() -> bool:
    """Check if demo mode is enabled via env vars."""
    demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
    demo_qr = os.getenv("DEMO_QR_ENABLED", "false").lower() == "true"
    return demo_mode or demo_qr


class ChargingStartResponse(BaseModel):
    """Response from charging start"""
    status: str
    charging_detected: bool
    charging_detected_at: str  # ISO string


class ChargingStopResponse(BaseModel):
    """Response from charging stop"""
    status: str


class WalletStatusResponse(BaseModel):
    """Wallet status including charging state"""
    charging_detected: bool
    charging_detected_at: Optional[str]  # ISO string or null
    message: str


@router.options("/charging/start")
async def options_charging_start():
    """Handle CORS preflight for charging start endpoint"""
    return {"status": "OK"}


@router.post("/charging/start", response_model=ChargingStartResponse)
def start_charging_demo(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Demo endpoint: Mark charging as detected for the current driver.
    
    Behavior:
    - Sets charging_detected=true
    - Sets charging_detected_at=now()
    - Bumps wallet_activity_updated_at (for pass refresh)
    
    Gated: Only works if DEMO_MODE=true or DEMO_QR_ENABLED=true
    """
    if not _is_demo_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "DEMO_DISABLED",
                "message": "Demo mode is not enabled in this environment."
            }
        )
    
    # Get or create wallet
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        wallet = DriverWallet(
            user_id=user.id,
            nova_balance=0,
            energy_reputation_score=0,
            charging_detected=False
        )
        db.add(wallet)
        db.flush()
    
    # Mark charging detected
    now = datetime.utcnow()
    wallet.charging_detected = True
    wallet.charging_detected_at = now
    
    # Bump wallet activity (for pass refresh)
    mark_wallet_activity(db, user.id)
    
    db.commit()
    db.refresh(wallet)
    
    logger.info(f"Demo: Charging detected for driver {user.id}")
    
    return ChargingStartResponse(
        status="OK",
        charging_detected=True,
        charging_detected_at=wallet.charging_detected_at.isoformat() + "Z"
    )


@router.options("/charging/stop")
async def options_charging_stop():
    """Handle CORS preflight for charging stop endpoint"""
    return {"status": "OK"}


@router.post("/charging/stop", response_model=ChargingStopResponse)
def stop_charging_demo(
    user: User = Depends(get_current_driver),
    db: Session = Depends(get_db)
):
    """
    Demo endpoint: Mark charging as stopped for the current driver.
    
    Behavior:
    - Sets charging_detected=false
    
    Gated: Only works if DEMO_MODE=true or DEMO_QR_ENABLED=true
    """
    if not _is_demo_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "DEMO_DISABLED",
                "message": "Demo mode is not enabled in this environment."
            }
        )
    
    # Get wallet
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == user.id).first()
    if not wallet:
        # If wallet doesn't exist, nothing to stop
        return ChargingStopResponse(status="OK")
    
    # Mark charging stopped
    wallet.charging_detected = False
    db.commit()
    
    logger.info(f"Demo: Charging stopped for driver {user.id}")
    
    return ChargingStopResponse(status="OK")


# Note: /v1/wallet/status endpoint is in wallet_pass.py router, not here
# This router only handles /v1/demo/* endpoints

