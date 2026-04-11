"""
Wallet Activity Service

Helper functions to mark wallet activity for pass refresh tracking.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.domain import DriverWallet
from app.services.apple_pass_push import send_updates_for_wallet
from app.services.google_wallet_service import update_google_wallet_object_on_activity


def mark_wallet_activity(db: Session, driver_user_id: int) -> None:
    """
    Mark wallet activity by updating wallet_activity_updated_at timestamp.
    
    This should be called whenever:
    - Nova is earned (grant_to_driver)
    - Nova is spent (redeem_from_driver or MerchantRedemption created)
    
    Args:
        db: Database session
        driver_user_id: Driver user ID
        
    Note:
        Does not commit - caller should commit in their transaction pattern.
    """
    wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_user_id).first()
    
    if not wallet:
        # Create wallet if it doesn't exist
        wallet = DriverWallet(
            user_id=driver_user_id,
            nova_balance=0,
            energy_reputation_score=0
        )
        db.add(wallet)
        db.flush()
    
    wallet.wallet_activity_updated_at = datetime.utcnow()
    # Note: updated_at is handled by SQLAlchemy onupdate

    import os

    # Optionally trigger PassKit silent push (non-blocking)
    if os.getenv("APPLE_PASS_PUSH_ENABLED", "false").lower() == "true":
        try:
            # Fire-and-forget; errors are logged inside send_updates_for_wallet
            send_updates_for_wallet(db, wallet)
        except Exception:
            # Never block wallet activity on push failures
            pass

    # Optionally update Google Wallet object immediately
    if os.getenv("GOOGLE_WALLET_ENABLED", "false").lower() == "true":
        try:
            if wallet.wallet_pass_token:
                update_google_wallet_object_on_activity(db, wallet, wallet.wallet_pass_token)
        except Exception:
            # Never block wallet activity on Google Wallet failures
            pass
