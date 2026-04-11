"""
Merchant Balance Service

Manages merchant balance tracking and ledger operations for discount budgets.
"""
import uuid
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models_while_you_charge import MerchantBalance, MerchantBalanceLedger
from app.utils.log import get_logger

logger = get_logger(__name__)


def get_balance(db: Session, merchant_id: str) -> Optional[MerchantBalance]:
    """
    Get the current balance for a merchant.
    
    Creates a zero-balance record if one doesn't exist.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        
    Returns:
        MerchantBalance object, or None if merchant doesn't exist
    """
    balance = db.query(MerchantBalance).filter(MerchantBalance.merchant_id == merchant_id).first()
    
    if balance is None:
        # Check if merchant exists by trying to create balance
        # Foreign key constraint will fail if merchant doesn't exist
        try:
            balance = MerchantBalance(
                id=str(uuid.uuid4()),
                merchant_id=merchant_id,
                balance_cents=0
            )
            db.add(balance)
            db.commit()
            db.refresh(balance)
            logger.info(f"Created initial balance for merchant {merchant_id}")
        except IntegrityError as e:
            # Could be FK constraint failure (merchant doesn't exist) or race condition
            db.rollback()
            # Try to fetch again in case of race condition
            balance = db.query(MerchantBalance).filter(MerchantBalance.merchant_id == merchant_id).first()
            if balance is None:
                # Merchant doesn't exist
                logger.warning(f"Merchant {merchant_id} does not exist")
                return None
    
    return balance


def credit_balance(
    db: Session,
    merchant_id: str,
    amount_cents: int,
    reason: str,
    session_id: Optional[str] = None
) -> MerchantBalance:
    """
    Credit (add) amount to merchant balance.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        amount_cents: Amount to credit (must be >= 0)
        reason: Reason for the credit (e.g., "initial_deposit", "top_up")
        session_id: Optional session ID reference
        
    Returns:
        Updated MerchantBalance object
        
    Raises:
        ValueError: If amount_cents < 0
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0 for credit operations")
    
    # Get or create balance
    balance = get_balance(db, merchant_id)
    if balance is None:
        raise ValueError(f"Merchant {merchant_id} not found")
    
    # Update balance
    old_balance = balance.balance_cents
    balance.balance_cents += amount_cents
    balance.updated_at = balance.updated_at  # Trigger onupdate
    
    # Create ledger entry
    ledger_entry = MerchantBalanceLedger(
        id=str(uuid.uuid4()),
        merchant_id=merchant_id,
        delta_cents=amount_cents,
        reason=reason,
        session_id=session_id
    )
    db.add(ledger_entry)
    
    try:
        db.commit()
        db.refresh(balance)
        logger.info(f"Credited {amount_cents} cents to merchant {merchant_id} (balance: {old_balance} -> {balance.balance_cents})")
        return balance
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to credit balance for merchant {merchant_id}: {str(e)}")
        raise


def debit_balance(
    db: Session,
    merchant_id: str,
    amount_cents: int,
    reason: str,
    session_id: Optional[str] = None
) -> MerchantBalance:
    """
    Debit (subtract) amount from merchant balance.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        amount_cents: Amount to debit (must be >= 0, will be subtracted)
        reason: Reason for the debit (e.g., "discount_issued", "reward_payout")
        session_id: Optional session ID reference
        
    Returns:
        Updated MerchantBalance object
        
    Raises:
        ValueError: If amount_cents < 0 or insufficient balance
    """
    if amount_cents < 0:
        raise ValueError("amount_cents must be >= 0 for debit operations (will be subtracted)")
    
    # Get or create balance
    balance = get_balance(db, merchant_id)
    if balance is None:
        raise ValueError(f"Merchant {merchant_id} not found")
    
    # Check sufficient balance
    if balance.balance_cents < amount_cents:
        raise ValueError(
            f"Insufficient balance for merchant {merchant_id}: "
            f"current={balance.balance_cents}, requested={amount_cents}"
        )
    
    # Update balance
    old_balance = balance.balance_cents
    balance.balance_cents -= amount_cents
    balance.updated_at = balance.updated_at  # Trigger onupdate
    
    # Create ledger entry (negative delta)
    ledger_entry = MerchantBalanceLedger(
        id=str(uuid.uuid4()),
        merchant_id=merchant_id,
        delta_cents=-amount_cents,  # Negative for debit
        reason=reason,
        session_id=session_id
    )
    db.add(ledger_entry)
    
    try:
        db.commit()
        db.refresh(balance)
        logger.info(f"Debited {amount_cents} cents from merchant {merchant_id} (balance: {old_balance} -> {balance.balance_cents})")
        return balance
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to debit balance for merchant {merchant_id}: {str(e)}")
        raise

