"""
Session Service for Domain Charge Party v1

Provides canonical session management using DomainChargingSession
and bridges to existing verify_dwell logic.
"""
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models_domain import DomainChargingSession
from app.services.nova import cents_to_nova
from app.services.verify_dwell import ping as verify_dwell_ping
from app.utils.log import get_logger
from app.utils.pwa_responses import normalize_number

logger = get_logger(__name__)


class SessionService:
    """Service for managing charging sessions with v1 Domain models"""
    
    @staticmethod
    def initialize_verify_dwell_session(
        db: Session,
        *,
        session_id: str,
        driver_user_id: int,
        charger_id: Optional[str] = None,
        merchant_id: Optional[str] = None,
        user_lat: Optional[float] = None,
        user_lng: Optional[float] = None
    ) -> None:
        """
        Initialize session in old sessions table for verify_dwell bridge.
        
        This is temporary until verify_dwell is migrated to DomainChargingSession.
        Creates a minimal entry that verify_dwell can use. Also calls verify_dwell.start_session
        to properly initialize target selection.
        """
        from datetime import datetime

        from sqlalchemy import text

        from app.services.verify_dwell import start_session as verify_dwell_start
        
        try:
            # Check if entry already exists
            existing = db.execute(text("""
                SELECT id FROM sessions WHERE id = :session_id
            """), {"session_id": session_id}).first()
            
            if existing:
                # Already exists - just update merchant_id in meta if provided
                if merchant_id:
                    try:
                        db.execute(text("""
                            UPDATE sessions
                            SET meta = json_set(COALESCE(meta, '{}'), '$.merchant_id', :merchant_id)
                            WHERE id = :session_id
                        """), {"merchant_id": merchant_id, "session_id": session_id})
                        db.commit()
                    except Exception:
                        pass  # Meta column may not support JSON functions
                return
            
            # Create minimal entry for verify_dwell
            now = datetime.utcnow()
            db.execute(text("""
                INSERT INTO sessions (id, user_id, status, started_at, created_at)
                VALUES (:id, :user_id, 'pending', :started_at, :created_at)
            """), {
                "id": session_id,
                "user_id": driver_user_id,
                "started_at": now,
                "created_at": now
            })
            
            # Store merchant_id in meta if provided
            if merchant_id:
                try:
                    db.execute(text("""
                        UPDATE sessions
                        SET meta = json_set('{}', '$.merchant_id', :merchant_id)
                        WHERE id = :session_id
                    """), {"merchant_id": merchant_id, "session_id": session_id})
                except Exception:
                    pass  # Meta may not support JSON functions
            
            db.commit()
            
            # Call verify_dwell.start_session to initialize target selection
            # This requires lat/lng - if not provided, verify_dwell will handle on first ping
            if user_lat is not None and user_lng is not None:
                try:
                    verify_dwell_start(
                        db=db,
                        session_id=session_id,
                        user_id=driver_user_id,
                        lat=user_lat,
                        lng=user_lng,
                        accuracy_m=50.0,
                        ua="Domain-Charge-Party-v1",
                        event_id=None
                    )
                except Exception as e:
                    logger.warning(f"Could not initialize verify_dwell target: {e}")
                    # Continue - verify_dwell will handle on first ping
        except Exception as e:
            logger.warning(f"Could not initialize verify_dwell session entry: {e}")
            db.rollback()
            # Don't fail - verify_dwell can create entry on first ping if needed
    
    @staticmethod
    def ping_session(
        db: Session,
        *,
        session_id: str,
        driver_user_id: int,
        lat: float,
        lng: float,
        accuracy_m: float = 50.0
    ) -> Dict[str, Any]:
        """
        Ping a session to update location and verification status.
        
        Returns the same response shape as pilot verify_ping for compatibility:
        - verified
        - verified_at_charger
        - reward_earned
        - ready_to_claim
        - nova_awarded
        - wallet_balance_nova
        - distance_to_charger_m
        - dwell_seconds
        - needed_seconds
        - distance_to_merchant_m
        - within_merchant_radius
        - etc.
        """
        # Get DomainChargingSession
        session = db.query(DomainChargingSession).filter(
            DomainChargingSession.id == session_id,
            DomainChargingSession.driver_user_id == driver_user_id
        ).first()
        
        if not session:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found"
            )
        
        # Check if already verified
        if session.verified:
            # Return verified state - get wallet balance
            wallet_balance_nova = 0
            try:
                from app.services.nova_service import NovaService
                wallet = NovaService.get_driver_wallet(db, driver_user_id)
                wallet_balance_nova = wallet.nova_balance if wallet else 0
            except Exception:
                pass
            
            return {
                "verified": True,
                "reward_earned": True,
                "verified_at_charger": True,
                "ready_to_claim": False,
                "nova_awarded": 0,  # Already awarded
                "wallet_balance_nova": wallet_balance_nova,
                "distance_to_charger_m": 0,
                "dwell_seconds": 0,
                "charger_radius_m": 60,
            }
        
        # For now, bridge to old sessions table for verify_dwell logic
        # TODO: Migrate verify_dwell to work directly with DomainChargingSession
        
        # Ensure session exists in old sessions table (for verify_dwell)
        # This is a temporary bridge until we fully migrate
        old_session = db.execute(text("""
            SELECT id, user_id, status FROM sessions WHERE id = :session_id
        """), {"session_id": session_id}).first()
        
        if not old_session:
            # Initialize old sessions table entry if missing
            # This should have been created on join, but handle gracefully
            SessionService.initialize_verify_dwell_session(
                db=db,
                session_id=session_id,
                driver_user_id=driver_user_id
            )
        
        # Call verify_dwell ping (uses old sessions table)
        result = verify_dwell_ping(
            db=db,
            session_id=session_id,
            lat=lat,
            lng=lng,
            accuracy_m=accuracy_m
        )
        
        if not result.get("ok"):
            reason = result.get("reason", "Verification failed")
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
        
        is_verified = result.get("verified", False)
        is_rewarded = is_verified and result.get("rewarded", False)
        
        # Update DomainChargingSession if verified
        if is_verified and not session.verified:
            session.verified = True
            session.verification_source = "dwell_verification"
            db.commit()
            db.refresh(session)
        
        # Get wallet balance
        wallet_balance_nova = 0
        try:
            from app.services.nova_service import NovaService
            wallet = NovaService.get_driver_wallet(db, driver_user_id)
            wallet_balance_nova = wallet.nova_balance if wallet else 0
        except Exception as ledger_err:
            logger.warning(f"Could not query wallet balance: {ledger_err}")
        
        # Calculate distance to charger (if we have charger info)
        distance_to_charger_m = normalize_number(result.get("distance_m", 0))
        charger_radius_m = normalize_number(result.get("radius_m", 60))
        
        # Get merchant info if available from session metadata or related data
        distance_to_merchant_m = None
        within_merchant_radius = False
        
        # Build response matching pilot verify_ping shape
        response = {
            "verified": is_verified,
            "reward_earned": is_rewarded,
            "verified_at_charger": is_verified,
            "distance_to_charger_m": distance_to_charger_m,
            "dwell_seconds": normalize_number(result.get("dwell_seconds", 0)),
            "verification_score": normalize_number(result.get("verification_score", 100 if is_verified else 0)),
            "wallet_balance": 0,  # Cents - not used by PWA
            "wallet_balance_nova": wallet_balance_nova,
            "ready_to_claim": is_verified and not is_rewarded,
        }
        
        # Add merchant distance if available (would need merchant_id from session)
        if distance_to_merchant_m is not None:
            response["distance_to_merchant_m"] = distance_to_merchant_m
            response["within_merchant_radius"] = within_merchant_radius
        
        # Add charger radius info
        if charger_radius_m:
            response["charger_radius_m"] = charger_radius_m
        
        # Add score components if available
        if "score_components" in result:
            components = result["score_components"]
            response["score_components"] = {
                k: normalize_number(v) for k, v in components.items()
            }
        
        # If verified and rewarded, add Nova fields
        if is_rewarded:
            response["nova_awarded"] = cents_to_nova(result.get("reward_cents", 0))
            response["wallet_delta_cents"] = normalize_number(result.get("wallet_delta_cents", 0))
            response["wallet_delta_nova"] = cents_to_nova(result.get("wallet_delta_cents", 0))
            response["ready_to_claim"] = False  # Already claimed
        else:
            response["nova_awarded"] = 0
        
        # Add remaining needed time if not verified
        if not is_verified:
            needed = result.get("needed_seconds", 0)
            response["needed_seconds"] = normalize_number(needed)
        
        # Add drift info if available
        if "drift_m" in result:
            response["drift_m"] = normalize_number(result["drift_m"])
        
        return response
    
    @staticmethod
    def cancel_session(
        db: Session,
        *,
        session_id: str,
        driver_user_id: int
    ) -> None:
        """
        Cancel a charging session.
        
        Marks the session as cancelled. Idempotent.
        """
        session = db.query(DomainChargingSession).filter(
            DomainChargingSession.id == session_id,
            DomainChargingSession.driver_user_id == driver_user_id
        ).first()
        
        if not session:
            # Idempotent - return success if session doesn't exist
            return
        
        # Mark as cancelled (could add cancelled_at field if needed)
        # For now, we can't easily cancel since DomainChargingSession doesn't have status field
        # But we can update the old sessions table that verify_dwell uses
        try:
            db.execute(text("""
                UPDATE sessions
                SET status = 'cancelled'
                WHERE id = :session_id
            """), {"session_id": session_id})
            db.commit()
            logger.info(f"Session {session_id} cancelled")
        except Exception as e:
            logger.warning(f"Could not cancel session in old table: {e}")
            # Still return success - idempotent

