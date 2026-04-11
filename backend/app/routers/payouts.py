
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.payouts_visa import get_payout_history, get_payout_stats, send_to_card

router = APIRouter(prefix="/v1/payouts", tags=["payouts"])

@router.post("/visa/direct")
async def create_visa_payout(
    user_id: str,
    amount_cents: int,
    card_number: str,
    db: Session = Depends(get_db)
):
    """Create a Visa Direct payout."""
    try:
        # Validate inputs
        if amount_cents <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        
        if amount_cents < 100:  # Minimum $1.00
            raise HTTPException(status_code=400, detail="Minimum payout is $1.00")
        
        if len(card_number) < 4:
            raise HTTPException(status_code=400, detail="Invalid card number")
        
        # Process payout
        result = send_to_card(user_id, amount_cents, card_number, db)
        
        if result["success"]:
            return {
                "success": True,
                "transaction_id": result["transaction_id"],
                "amount_cents": result["amount_cents"],
                "status": result["status"],
                "message": result["message"]
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Payout failed: {result.get('error', 'Unknown error')}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create payout: {str(e)}")

@router.get("/visa/history")
async def get_visa_payout_history(
    user_id: str,
    limit: int = Query(50, description="Number of payouts to return"),
    db: Session = Depends(get_db)
):
    """Get Visa payout history for a user."""
    try:
        history = get_payout_history(user_id, limit, db)
        return {
            "user_id": user_id,
            "payouts": history,
            "count": len(history)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get payout history: {str(e)}")

@router.get("/visa/stats")
async def get_visa_payout_stats(
    user_id: str,
    db: Session = Depends(get_db)
):
    """Get Visa payout statistics for a user."""
    try:
        stats = get_payout_stats(user_id, db)
        return {
            "user_id": user_id,
            "stats": stats
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get payout stats: {str(e)}")

@router.get("/visa/status/{transaction_id}")
async def get_payout_status(
    transaction_id: str,
    db: Session = Depends(get_db)
):
    """Get status of a specific payout transaction."""
    try:
        from ..services.payouts_visa import VisaPayout
        
        payout = db.query(VisaPayout).filter(
            VisaPayout.transaction_id == transaction_id
        ).first()
        
        if not payout:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        return {
            "transaction_id": payout.transaction_id,
            "user_id": payout.user_id,
            "amount_cents": payout.amount_cents,
            "card_number": f"****{payout.card_number}",
            "status": payout.status,
            "error_message": payout.error_message,
            "created_at": payout.created_at.isoformat(),
            "completed_at": payout.completed_at.isoformat() if payout.completed_at else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get payout status: {str(e)}")
