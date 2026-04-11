"""
Merchant Balance API Router

Provides endpoints for managing merchant balance and ledger.
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies.domain import require_merchant_admin
from app.models import User
from app.services.audit import log_merchant_balance_mutation
from app.services.auth_service import AuthService
from app.services.merchant_balance import credit_balance, debit_balance, get_balance
from app.utils.log import get_logger

router = APIRouter(prefix="/v1/merchants", tags=["merchant-balance"])
logger = get_logger(__name__)


# Request/Response Models
class BalanceResponse(BaseModel):
    """Balance response model"""
    merchant_id: str
    balance_cents: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CreditRequest(BaseModel):
    """Credit request model"""
    amount_cents: int = Field(..., gt=0, description="Amount to credit (must be > 0)")
    reason: str = Field(..., min_length=1, description="Reason for the credit")
    session_id: Optional[str] = Field(None, description="Optional session ID reference")


class DebitRequest(BaseModel):
    """Debit request model"""
    amount_cents: int = Field(..., gt=0, description="Amount to debit (must be > 0)")
    reason: str = Field(..., min_length=1, description="Reason for the debit")
    session_id: Optional[str] = Field(None, description="Optional session ID reference")


class CreditResponse(BaseModel):
    """Credit response model"""
    merchant_id: str
    balance_cents: int
    amount_credited: int
    reason: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class DebitResponse(BaseModel):
    """Debit response model"""
    merchant_id: str
    balance_cents: int
    amount_debited: int
    reason: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


@router.get("/{merchant_id}/balance", response_model=BalanceResponse)
def get_merchant_balance(
    merchant_id: str = Path(..., description="Merchant ID"),
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db)
):
    """
    Get the current balance for a merchant.
    
    Creates a zero-balance record if one doesn't exist.
    
    P0-B Security: Requires merchant_admin role and merchant ownership validation.
    """
    # P0-B: Validate merchant ownership - user must own the merchant
    user_merchant = AuthService.get_user_merchant(db, user.id)
    if not user_merchant or user_merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: You do not have permission to access merchant {merchant_id}"
        )
    try:
        balance = get_balance(db, merchant_id)
        
        if balance is None:
            raise HTTPException(status_code=404, detail=f"Merchant {merchant_id} not found")
        
        return BalanceResponse(
            merchant_id=balance.merchant_id,
            balance_cents=balance.balance_cents,
            created_at=balance.created_at.isoformat(),
            updated_at=balance.updated_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get balance for merchant {merchant_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get balance: {str(e)}")


@router.post("/{merchant_id}/credit", response_model=CreditResponse)
def credit_merchant_balance(
    merchant_id: str = Path(..., description="Merchant ID"),
    request: CreditRequest = Body(...),
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db)
):
    """
    Credit (add) amount to merchant balance.
    
    Creates a balance record if one doesn't exist.
    
    P0-B Security: Requires merchant_admin role and merchant ownership validation.
    """
    # P0-B: Validate merchant ownership - user must own the merchant
    user_merchant = AuthService.get_user_merchant(db, user.id)
    if not user_merchant or user_merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: You do not have permission to modify merchant {merchant_id}"
        )
    try:
        # Get balance before mutation
        before_balance_obj = get_balance(db, merchant_id)
        before_balance = before_balance_obj.balance_cents if before_balance_obj else 0
        
        balance = credit_balance(
            db=db,
            merchant_id=merchant_id,
            amount_cents=request.amount_cents,
            reason=request.reason,
            session_id=request.session_id
        )
        
        # P1-1: Admin audit log
        log_merchant_balance_mutation(
            db=db,
            actor_id=user.id,
            action="merchant_credit",
            merchant_id=merchant_id,
            before_balance=before_balance,
            after_balance=balance.balance_cents,
            amount=request.amount_cents,
            metadata={"reason": request.reason, "session_id": request.session_id}
        )
        db.commit()  # Commit audit log
        
        return CreditResponse(
            merchant_id=balance.merchant_id,
            balance_cents=balance.balance_cents,
            amount_credited=request.amount_cents,
            reason=request.reason,
            created_at=balance.created_at.isoformat(),
            updated_at=balance.updated_at.isoformat()
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to credit balance for merchant {merchant_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to credit balance: {str(e)}")


@router.post("/{merchant_id}/debit", response_model=DebitResponse)
def debit_merchant_balance(
    merchant_id: str = Path(..., description="Merchant ID"),
    request: DebitRequest = Body(...),
    user: User = Depends(require_merchant_admin),
    db: Session = Depends(get_db)
):
    """
    Debit (subtract) amount from merchant balance.
    
    Returns 400 if insufficient balance.
    
    P0-B Security: Requires merchant_admin role and merchant ownership validation.
    """
    # P0-B: Validate merchant ownership - user must own the merchant
    user_merchant = AuthService.get_user_merchant(db, user.id)
    if not user_merchant or user_merchant.id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: You do not have permission to modify merchant {merchant_id}"
        )
    try:
        # Get balance before mutation
        before_balance_obj = get_balance(db, merchant_id)
        before_balance = before_balance_obj.balance_cents if before_balance_obj else 0
        
        balance = debit_balance(
            db=db,
            merchant_id=merchant_id,
            amount_cents=request.amount_cents,
            reason=request.reason,
            session_id=request.session_id
        )
        
        # P1-1: Admin audit log
        log_merchant_balance_mutation(
            db=db,
            actor_id=user.id,
            action="merchant_debit",
            merchant_id=merchant_id,
            before_balance=before_balance,
            after_balance=balance.balance_cents,
            amount=request.amount_cents,
            metadata={"reason": request.reason, "session_id": request.session_id}
        )
        db.commit()  # Commit audit log
        
        return DebitResponse(
            merchant_id=balance.merchant_id,
            balance_cents=balance.balance_cents,
            amount_debited=request.amount_cents,
            reason=request.reason,
            created_at=balance.created_at.isoformat(),
            updated_at=balance.updated_at.isoformat()
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to debit balance for merchant {merchant_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to debit balance: {str(e)}")

