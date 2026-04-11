"""
Background service for accruing Nova while charging is detected (demo mode only)
Accrues 1 Nova every 5 seconds while charging_detected = true
1 Nova = 0.01 USD equivalent
"""
import asyncio
import logging
import os
import uuid
from typing import Optional

from app.models import DriverWallet, NovaTransaction
from app.services.wallet_activity import mark_wallet_activity

logger = logging.getLogger(__name__)

class NovaAccrualService:
    """Service to accrue Nova for wallets with charging detected"""
    
    def __init__(self, accrual_interval: int = 5):
        """
        Initialize the Nova accrual service.
        
        Args:
            accrual_interval: Seconds between each accrual (default: 5)
        """
        self.accrual_interval = accrual_interval
        self.running = False
        self.task: Optional[asyncio.Task] = None
    
    def is_enabled(self) -> bool:
        """Check if Nova accrual is enabled (demo mode only)"""
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
        demo_qr = os.getenv("DEMO_QR_ENABLED", "false").lower() == "true"
        return demo_mode or demo_qr
    
    async def start(self):
        """Start the Nova accrual service"""
        if not self.is_enabled():
            logger.info("Nova accrual service disabled (demo mode not enabled)")
            return
        
        if self.running:
            logger.warning("Nova accrual service is already running")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info(f"Nova accrual service started (interval: {self.accrual_interval}s)")
    
    async def stop(self):
        """Stop the Nova accrual service"""
        if not self.running:
            return
        
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Nova accrual service stopped")
    
    async def _run(self):
        """Main accrual loop"""
        while self.running:
            try:
                # Check if still enabled (in case env var changes)
                if not self.is_enabled():
                    logger.info("Nova accrual service disabled, stopping")
                    self.running = False
                    break
                
                await self._accrue_nova_for_charging_wallets()
                await asyncio.sleep(self.accrual_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Nova accrual service: {e}", exc_info=True)
                await asyncio.sleep(self.accrual_interval)
    
    async def _accrue_nova_for_charging_wallets(self):
        """Accrue 1 Nova for each wallet with charging_detected = true"""
        try:
            # Get database session
            from app.db import SessionLocal
            db = SessionLocal()
            try:
                # Find all wallets with charging detected
                charging_wallets = db.query(DriverWallet).filter(
                    DriverWallet.charging_detected == True
                ).all()
                
                if not charging_wallets:
                    return
                
                # Accrue 1 Nova for each wallet
                for wallet in charging_wallets:
                    wallet.nova_balance += 1
                    
                    # Increment reputation points (1 Nova = 1 reputation point for charging rewards)
                    wallet.energy_reputation_score = (wallet.energy_reputation_score or 0) + 1
                    
                    # Create Nova transaction record (1 Nova = 0.01 USD equivalent)
                    transaction = NovaTransaction(
                        id=str(uuid.uuid4()),
                        type="driver_earn",
                        driver_user_id=wallet.user_id,
                        amount=1,  # 1 Nova
                        transaction_meta={
                            "source": "demo_charging_accrual",
                            "rate": "1_nova_per_5_seconds",
                            "usd_equivalent": 0.01
                        }
                    )
                    db.add(transaction)
                    
                    # Update activity timestamp to trigger pass refresh
                    mark_wallet_activity(db, wallet.user_id)
                
                db.commit()
                
                if charging_wallets:
                    logger.debug(
                        f"Accrued 1 Nova for {len(charging_wallets)} wallet(s) "
                        f"(total accrual: {len(charging_wallets)} Nova)"
                    )
            except Exception as e:
                db.rollback()
                logger.error(f"Error accruing Nova (rolling back): {e}", exc_info=True)
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error in Nova accrual service: {e}", exc_info=True)


# Global instance
nova_accrual_service = NovaAccrualService()

