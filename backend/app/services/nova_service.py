"""
Nova Service - Nova balance management and transactions
for Domain Charge Party MVP
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models_domain import (
    DomainMerchant,
    DriverWallet,
    NovaTransaction,
)
from app.services.wallet_activity import mark_wallet_activity

logger = logging.getLogger(__name__)

# Module-level cached check for whether NovaTransaction has payload_hash column
# Avoids calling inspect() on every redeem request
_payload_hash_column_exists: Optional[bool] = None


def _check_payload_hash_column(db: Session) -> bool:
    """Check if payload_hash column exists, with module-level caching."""
    global _payload_hash_column_exists
    if _payload_hash_column_exists is not None:
        return _payload_hash_column_exists
    try:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db.bind)
        columns = [col['name'] for col in inspector.get_columns('nova_transactions')]
        _payload_hash_column_exists = 'payload_hash' in columns
    except Exception:
        # Fallback: check the ORM model attribute
        _payload_hash_column_exists = hasattr(NovaTransaction, 'payload_hash')
    return _payload_hash_column_exists


def compute_payload_hash(payload: dict) -> str:
    """
    Compute canonical payload hash for idempotency conflict detection.
    
    Uses canonical JSON (sorted keys, no spaces) and SHA256.
    Truncates to 16 chars for storage efficiency.
    
    Args:
        payload: Dict to hash
        
    Returns:
        16-character hex hash string
    """
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    hash_obj = hashlib.sha256(canonical_json.encode())
    return hash_obj.hexdigest()[:16]


class NovaService:
    """Service for Nova balance and transaction management"""
    
    @staticmethod
    def grant_to_driver(
        db: Session,
        driver_id: int,
        amount: int,
        *,
        type: str = "driver_earn",
        session_id: Optional[str] = None,
        event_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        auto_commit: bool = True,
    ) -> NovaTransaction:
        """
        Grant Nova to a driver.
        
        Args:
            driver_id: User ID of the driver
            amount: Nova amount (always positive)
            type: Transaction type (driver_earn, admin_grant, etc.)
            session_id: Optional charging session ID
            metadata: Optional metadata dict
            idempotency_key: Optional idempotency key for deduplication
        """
        # Check idempotency if key provided
        if idempotency_key:
            payload = {
                "driver_id": driver_id,
                "amount": amount,
                "type": type,
                "session_id": session_id,
                "event_id": event_id
            }
            payload_hash = compute_payload_hash(payload)
            
            existing_txn = db.query(NovaTransaction).filter(
                NovaTransaction.idempotency_key == idempotency_key,
                NovaTransaction.type == type
            ).first()
            
            if existing_txn:
                # Check payload_hash if column exists
                try:
                    existing_hash = getattr(existing_txn, 'payload_hash', None)
                    if existing_hash and existing_hash != payload_hash:
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=409,
                            detail="Idempotency key conflict: same key with different payload"
                        )
                except OperationalError as e:
                    if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=500,
                            detail="Database schema is out of date. Run: alembic upgrade head (and ensure you're pointing at the same DB file)."
                        )
                    raise
                # Same key + same hash (or no hash column) → return existing
                logger.info(f"Idempotent Nova grant: returning existing transaction {existing_txn.id}")
                return existing_txn
        
        # Get or create driver wallet
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_id).first()
        if not wallet:
            wallet = DriverWallet(user_id=driver_id, nova_balance=0, energy_reputation_score=0)
            db.add(wallet)
            db.flush()
        
        # Update balance
        wallet.nova_balance += amount
        wallet.updated_at = datetime.utcnow()
        
        # Increment reputation points when Nova is awarded for charging
        # Formula: 1 Nova = 1 reputation point (for charging rewards)
        # All driver_earn transactions represent charging rewards and should increment reputation
        if type == "driver_earn":
            # This is a charging reward - increment reputation points
            rep_earned = amount  # 1 Nova = 1 reputation point
            wallet.energy_reputation_score = (wallet.energy_reputation_score or 0) + rep_earned
            logger.info(f"Incremented reputation by {rep_earned} points for driver {driver_id} (Nova: {amount}, session: {session_id or 'none'})")
        
        # Compute payload hash
        payload = {
            "driver_id": driver_id,
            "amount": amount,
            "type": type,
            "session_id": session_id,
            "event_id": event_id
        }
        payload_hash = compute_payload_hash(payload)
        
        # Create transaction record
        transaction = NovaTransaction(
            id=str(uuid.uuid4()),
            type=type,
            driver_user_id=driver_id,
            amount=amount,
            session_id=session_id,
            event_id=event_id,
            transaction_meta=metadata or {},
            idempotency_key=idempotency_key
        )
        # Set payload_hash if column exists (added in migration 036)
        try:
            if hasattr(NovaTransaction, 'payload_hash'):
                transaction.payload_hash = payload_hash
            db.add(transaction)
            if auto_commit:
                db.commit()
                db.refresh(transaction)
            else:
                db.flush()
        except OperationalError as e:
            if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                db.rollback()
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=500,
                    detail="Database schema is out of date. Run: alembic upgrade head (and ensure you're pointing at the same DB file)."
                )
            raise

        # Mark wallet activity for pass refresh
        mark_wallet_activity(db, driver_id)
        if auto_commit:
            db.commit()
        
        logger.info(f"Granted {amount} Nova to driver {driver_id} (type: {type}, session: {session_id})")
        
        # Emit nova_earned event (non-blocking)
        try:
            from app.events.domain import NovaEarnedEvent
            from app.events.outbox import store_outbox_event
            event = NovaEarnedEvent(
                user_id=str(driver_id),
                amount_cents=amount,
                session_id=session_id or "",
                new_balance_cents=wallet.nova_balance,
                earned_at=datetime.utcnow()
            )
            store_outbox_event(db, event)
        except Exception as e:
            logger.warning(f"Failed to emit nova_earned event: {e}")
        
        return transaction
    
    @staticmethod
    def redeem_from_driver(
        db: Session,
        driver_id: int,
        merchant_id: str,
        amount: int,
        *,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Redeem Nova from driver to merchant.
        
        This performs atomic operations:
        1. Decreases driver wallet balance (atomic UPDATE with balance check)
        2. Increases merchant Nova balance
        3. Creates transaction records with idempotency
        
        Args:
            driver_id: User ID of the driver
            merchant_id: Merchant ID
            amount: Nova amount to redeem (always positive)
            session_id: Optional charging session ID
            metadata: Optional metadata dict
            idempotency_key: Optional idempotency key for deduplication
        
        Returns:
            Dict with transaction info and new balances
        """
        from sqlalchemy import text
        
        # Check idempotency if key provided
        if idempotency_key:
            payload = {
                "driver_id": driver_id,
                "merchant_id": merchant_id,
                "amount": amount,
                "session_id": session_id
            }
            payload_hash = compute_payload_hash(payload)
            
            # Check for existing transaction - handle missing payload_hash column
            try:
                existing_txn = db.query(NovaTransaction).filter(
                    NovaTransaction.idempotency_key == idempotency_key,
                    NovaTransaction.type == "driver_redeem"
                ).first()
            except OperationalError as e:
                if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                    # Column doesn't exist - use raw SQL
                    from sqlalchemy import text
                    result = db.execute(text("""
                        SELECT id, type, driver_user_id, merchant_id, amount, stripe_payment_id,
                               session_id, event_id, metadata, idempotency_key, created_at
                        FROM nova_transactions
                        WHERE idempotency_key = :idempotency_key AND type = 'driver_redeem'
                        LIMIT 1
                    """), {"idempotency_key": idempotency_key})
                    row = result.fetchone()
                    if row:
                        # Convert to dict-like object
                        existing_txn = type('obj', (object,), {
                            'id': row[0],
                            'type': row[1],
                            'driver_user_id': row[2],
                            'merchant_id': row[3],
                            'amount': row[4],
                            'stripe_payment_id': row[5],
                            'session_id': row[6],
                            'event_id': row[7],
                            'metadata': row[8],
                            'idempotency_key': row[9],
                            'created_at': row[10],
                            'payload_hash': None  # Column doesn't exist
                        })()
                    else:
                        existing_txn = None
                else:
                    raise
            
            if existing_txn:
                # Check payload_hash if column exists
                try:
                    existing_hash = getattr(existing_txn, 'payload_hash', None)
                    if existing_hash and existing_hash != payload_hash:
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=409,
                            detail="Idempotency key conflict: same key with different payload"
                        )
                except OperationalError as e:
                    if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=500,
                            detail="Database schema is out of date. Run: alembic upgrade head (and ensure you're pointing at the same DB file)."
                        )
                    raise
                # Same key + same hash (or no hash column) → return existing
                wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_id).first()
                merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
                logger.info(f"Idempotent Nova redemption: returning existing transaction {existing_txn.id}")
                return {
                    "transaction_id": existing_txn.id,
                    "driver_balance": wallet.nova_balance if wallet else 0,
                    "merchant_balance": merchant.nova_balance if merchant else 0,
                    "amount": existing_txn.amount,
                    "idempotent": True
                }
        
        # Validate merchant exists and is active
        merchant = db.query(DomainMerchant).filter(
            and_(
                DomainMerchant.id == merchant_id,
                DomainMerchant.status == "active"
            )
        ).first()
        if not merchant:
            raise ValueError(f"Merchant {merchant_id} not found or not active")
        
        # P0-3: Negative balance prevention (application layer)
        # Add explicit check before acquiring lock for better error messages and early validation
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_id).first()
        if not wallet:
            raise ValueError(f"Driver wallet not found for user {driver_id}")
        if wallet.nova_balance < amount:
            raise ValueError(f"Insufficient Nova balance. Has {wallet.nova_balance}, needs {amount}")
        
        # Atomic balance update with row lock (P0 race condition fix)
        # Use SELECT ... FOR UPDATE to lock the row, then atomic UPDATE
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_id).with_for_update().first()
        if not wallet:
            raise ValueError(f"Driver wallet not found for user {driver_id}")
        
        # Atomic update: only proceed if balance >= amount
        # This prevents double-spend even with concurrent requests
        # Note: Using raw SQL for atomicity; table name is driver_wallets (plural)
        result = db.execute(text("""
            UPDATE driver_wallets
            SET nova_balance = nova_balance - :amount,
                updated_at = :updated_at
            WHERE driver_id = :driver_id
            AND nova_balance >= :amount
        """), {
            "amount": amount,
            "driver_id": driver_id,
            "updated_at": datetime.utcnow()
        })
        
        if result.rowcount == 0:
            # Balance check failed - insufficient funds or race condition
            db.refresh(wallet)
            raise ValueError(f"Insufficient Nova balance. Has {wallet.nova_balance}, needs {amount}")
        
        # Refresh wallet to get updated balance
        db.refresh(wallet)
        
        # Update merchant balance (no race condition here - only increases)
        merchant.nova_balance += amount
        merchant.updated_at = datetime.utcnow()
        
        # Compute payload hash for new transaction
        payload = {
            "driver_id": driver_id,
            "merchant_id": merchant_id,
            "amount": amount,
            "session_id": session_id
        }
        payload_hash = compute_payload_hash(payload)
        
        # Check if payload_hash column exists (cached at module level)
        has_payload_hash_column = _check_payload_hash_column(db)
        
        driver_txn_id = str(uuid.uuid4())
        merchant_txn_id = str(uuid.uuid4())
        
        if has_payload_hash_column:
            # Column exists - use ORM
            driver_txn = NovaTransaction(
                id=driver_txn_id,
                type="driver_redeem",
                driver_user_id=driver_id,
                merchant_id=merchant_id,
                amount=amount,
                session_id=session_id,
                idempotency_key=idempotency_key,
                transaction_meta=metadata or {},
                payload_hash=payload_hash
            )
            merchant_txn = NovaTransaction(
                id=merchant_txn_id,
                type="merchant_earn",
                driver_user_id=driver_id,
                merchant_id=merchant_id,
                amount=amount,
                session_id=session_id,
                transaction_meta={**(metadata or {}), "source": "driver_redeem"}
            )
            db.add(driver_txn)
            db.add(merchant_txn)
            db.commit()
        else:
            # Column doesn't exist - use raw SQL without payload_hash
            db.execute(text("""
                INSERT INTO nova_transactions 
                (id, type, driver_user_id, merchant_id, amount, stripe_payment_id, session_id, event_id, metadata, idempotency_key, created_at)
                VALUES (:id, :type, :driver_user_id, :merchant_id, :amount, :stripe_payment_id, :session_id, :event_id, :metadata, :idempotency_key, :created_at)
            """), {
                "id": driver_txn_id,
                "type": "driver_redeem",
                "driver_user_id": driver_id,
                "merchant_id": merchant_id,
                "amount": amount,
                "stripe_payment_id": None,
                "session_id": session_id,
                "event_id": None,
                "metadata": json.dumps(metadata or {}),
                "idempotency_key": idempotency_key,
                "created_at": datetime.utcnow()
            })
            db.execute(text("""
                INSERT INTO nova_transactions 
                (id, type, driver_user_id, merchant_id, amount, stripe_payment_id, session_id, event_id, metadata, created_at)
                VALUES (:id, :type, :driver_user_id, :merchant_id, :amount, :stripe_payment_id, :session_id, :event_id, :metadata, :created_at)
            """), {
                "id": merchant_txn_id,
                "type": "merchant_earn",
                "driver_user_id": driver_id,
                "merchant_id": merchant_id,
                "amount": amount,
                "stripe_payment_id": None,
                "session_id": session_id,
                "event_id": None,
                "metadata": json.dumps({**(metadata or {}), "source": "driver_redeem"}),
                "created_at": datetime.utcnow()
            })
            db.commit()
            # Create minimal objects for return value
            driver_txn = type('obj', (object,), {'id': driver_txn_id})()
            merchant_txn = type('obj', (object,), {'id': merchant_txn_id})()
        db.refresh(merchant)
        
        # Mark wallet activity for pass refresh
        mark_wallet_activity(db, driver_id)
        db.commit()
        
        logger.info(f"Redeemed {amount} Nova from driver {driver_id} to merchant {merchant_id} (idempotency_key: {idempotency_key})")
        
        # Emit nova_redeemed and first_redemption_completed events (non-blocking)
        try:
            from app.events.domain import FirstRedemptionCompletedEvent, NovaRedeemedEvent
            from app.events.outbox import store_outbox_event
            from app.models.domain import MerchantRedemption
            
            # Check if this is the first redemption for this driver
            previous_redemptions = db.query(MerchantRedemption).filter(
                MerchantRedemption.driver_user_id == driver_id
            ).count()
            
            is_first_redemption = previous_redemptions == 0
            
            # Emit nova_redeemed event
            redeem_event = NovaRedeemedEvent(
                user_id=str(driver_id),
                amount_cents=amount,
                merchant_id=merchant_id,
                redemption_id=driver_txn_id,
                new_balance_cents=wallet.nova_balance,
                redeemed_at=datetime.utcnow()
            )
            store_outbox_event(db, redeem_event)
            
            # Emit first_redemption_completed if this is the first
            if is_first_redemption:
                first_event = FirstRedemptionCompletedEvent(
                    user_id=str(driver_id),
                    redemption_id=driver_txn_id,
                    merchant_id=merchant_id,
                    amount_cents=amount,
                    completed_at=datetime.utcnow()
                )
                store_outbox_event(db, first_event)
        except Exception as e:
            logger.warning(f"Failed to emit nova_redeemed event: {e}")
        
        return {
            "transaction_id": driver_txn.id,
            "driver_balance": wallet.nova_balance,
            "merchant_balance": merchant.nova_balance,
            "amount": amount
        }
    
    @staticmethod
    def grant_to_merchant(
        db: Session,
        merchant_id: str,
        amount: int,
        *,
        type: str = "merchant_topup",
        stripe_payment_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None
    ) -> NovaTransaction:
        """
        Grant Nova to a merchant (e.g., from Stripe purchase).
        Supports idempotency to prevent double grants.
        
        Args:
            merchant_id: Merchant ID
            amount: Nova amount (always positive)
            type: Transaction type (merchant_topup, admin_grant, etc.)
            stripe_payment_id: Optional Stripe payment ID
            metadata: Optional metadata dict
            idempotency_key: Optional idempotency key for deduplication
        """
        # Check idempotency if key provided
        if idempotency_key:
            payload = {
                "merchant_id": merchant_id,
                "amount": amount,
                "type": type,
                "stripe_payment_id": stripe_payment_id
            }
            payload_hash = compute_payload_hash(payload)
            
            existing_txn = db.query(NovaTransaction).filter(
                NovaTransaction.idempotency_key == idempotency_key,
                NovaTransaction.type == type,
                NovaTransaction.merchant_id == merchant_id
            ).first()
            
            if existing_txn:
                # Check payload_hash if column exists
                try:
                    existing_hash = getattr(existing_txn, 'payload_hash', None)
                    if existing_hash and existing_hash != payload_hash:
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=409,
                            detail="Idempotency key conflict: same key with different payload"
                        )
                except OperationalError as e:
                    if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=500,
                            detail="Database schema is out of date. Run: alembic upgrade head (and ensure you're pointing at the same DB file)."
                        )
                    raise
                # Same key + same hash (or no hash column) → return existing
                logger.info(f"Idempotent Nova grant: returning existing transaction {existing_txn.id}")
                return existing_txn
        
        # Validate merchant exists
        merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
        if not merchant:
            raise ValueError(f"Merchant {merchant_id} not found")
        
        # Update merchant balance
        merchant.nova_balance += amount
        merchant.updated_at = datetime.utcnow()
        
        # Compute payload hash
        payload = {
            "merchant_id": merchant_id,
            "amount": amount,
            "type": type,
            "stripe_payment_id": stripe_payment_id
        }
        payload_hash = compute_payload_hash(payload)
        
        # Create transaction record with idempotency key
        transaction = NovaTransaction(
            id=str(uuid.uuid4()),
            type=type,
            merchant_id=merchant_id,
            amount=amount,
            stripe_payment_id=stripe_payment_id,
            idempotency_key=idempotency_key,
            transaction_meta=metadata or {}
        )
        # Set payload_hash if column exists (added in migration 036)
        try:
            if hasattr(NovaTransaction, 'payload_hash'):
                transaction.payload_hash = payload_hash
            db.add(transaction)
            db.commit()
            db.refresh(transaction)
            db.refresh(merchant)
        except OperationalError as e:
            if "no such column" in str(e).lower() and "payload_hash" in str(e).lower():
                db.rollback()
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=500,
                    detail="Database schema is out of date. Run: alembic upgrade head (and ensure you're pointing at the same DB file)."
                )
            raise
        
        logger.info(f"Granted {amount} Nova to merchant {merchant_id} (type: {type}, stripe: {stripe_payment_id}, idempotency_key: {idempotency_key})")
        return transaction
    
    @staticmethod
    def get_driver_wallet(db: Session, driver_id: int) -> DriverWallet:
        """Get or create driver wallet"""
        wallet = db.query(DriverWallet).filter(DriverWallet.user_id == driver_id).first()
        if not wallet:
            wallet = DriverWallet(user_id=driver_id, nova_balance=0, energy_reputation_score=0)
            db.add(wallet)
            db.commit()
            db.refresh(wallet)
        return wallet
    
    @staticmethod
    def get_driver_transactions(
        db: Session,
        driver_id: int,
        limit: int = 50
    ) -> list[NovaTransaction]:
        """Get recent transactions for a driver"""
        return db.query(NovaTransaction).filter(
            NovaTransaction.driver_user_id == driver_id
        ).order_by(NovaTransaction.created_at.desc()).limit(limit).all()
    
    @staticmethod
    def get_merchant_transactions(
        db: Session,
        merchant_id: str,
        limit: int = 50
    ) -> list[NovaTransaction]:
        """Get recent transactions for a merchant"""
        return db.query(NovaTransaction).filter(
            NovaTransaction.merchant_id == merchant_id
        ).order_by(NovaTransaction.created_at.desc()).limit(limit).all()

