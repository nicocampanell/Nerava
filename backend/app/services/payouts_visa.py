from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from ..db import Base


# Visa payout transaction model
class VisaPayout(Base):
    __tablename__ = "visa_payouts"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    card_number = Column(String, nullable=False)  # Last 4 digits only
    transaction_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending, completed, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

def send_to_card(
    user_id: str,
    amount_cents: int,
    card_number: str,
    db: Session
) -> Dict[str, Any]:
    """
    Send payout to Visa card (stubbed implementation).
    In production, this would integrate with Visa Direct API.
    """
    try:
        # Generate mock transaction ID
        import uuid
        transaction_id = f"visa_{uuid.uuid4().hex[:16]}"
        
        # Create payout record
        payout = VisaPayout(
            user_id=user_id,
            amount_cents=amount_cents,
            card_number=card_number[-4:],  # Store only last 4 digits
            transaction_id=transaction_id,
            status="pending"
        )
        db.add(payout)
        db.flush()
        
        # Simulate processing delay
        import time
        time.sleep(0.1)
        
        # Mock success (90% success rate)
        import random
        if random.random() < 0.9:
            payout.status = "completed"
            payout.completed_at = datetime.utcnow()
            db.commit()
            
            return {
                "success": True,
                "transaction_id": transaction_id,
                "amount_cents": amount_cents,
                "status": "completed",
                "message": "Payout processed successfully"
            }
        else:
            payout.status = "failed"
            payout.error_message = "Mock network error"
            db.commit()
            
            return {
                "success": False,
                "transaction_id": transaction_id,
                "amount_cents": amount_cents,
                "status": "failed",
                "error": "Mock network error"
            }
            
    except Exception as e:
        db.rollback()
        return {
            "success": False,
            "error": f"Failed to process payout: {str(e)}"
        }

def get_payout_history(
    user_id: str,
    limit: int = 50,
    db: Session = None
) -> List[Dict[str, Any]]:
    """Get payout history for a user."""
    try:
        payouts = db.query(VisaPayout).filter(
            VisaPayout.user_id == user_id
        ).order_by(VisaPayout.created_at.desc()).limit(limit).all()
        
        return [
            {
                "id": payout.id,
                "transaction_id": payout.transaction_id,
                "amount_cents": payout.amount_cents,
                "card_number": f"****{payout.card_number}",
                "status": payout.status,
                "error_message": payout.error_message,
                "created_at": payout.created_at.isoformat(),
                "completed_at": payout.completed_at.isoformat() if payout.completed_at else None
            }
            for payout in payouts
        ]
        
    except Exception as e:
        return []

def get_payout_stats(
    user_id: str,
    db: Session = None
) -> Dict[str, Any]:
    """Get payout statistics for a user."""
    try:
        from sqlalchemy import func
        
        # Get total payouts
        total_payouts = db.query(func.sum(VisaPayout.amount_cents)).filter(
            VisaPayout.user_id == user_id,
            VisaPayout.status == "completed"
        ).scalar() or 0
        
        # Get pending payouts
        pending_payouts = db.query(func.sum(VisaPayout.amount_cents)).filter(
            VisaPayout.user_id == user_id,
            VisaPayout.status == "pending"
        ).scalar() or 0
        
        # Get failed payouts
        failed_payouts = db.query(func.sum(VisaPayout.amount_cents)).filter(
            VisaPayout.user_id == user_id,
            VisaPayout.status == "failed"
        ).scalar() or 0
        
        return {
            "total_completed_cents": total_payouts,
            "total_pending_cents": pending_payouts,
            "total_failed_cents": failed_payouts,
            "total_attempted_cents": total_payouts + pending_payouts + failed_payouts
        }
        
    except Exception as e:
        return {
            "total_completed_cents": 0,
            "total_pending_cents": 0,
            "total_failed_cents": 0,
            "total_attempted_cents": 0
        }
