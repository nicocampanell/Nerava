"""
Payments Service - Wrapper for Stripe/Square/Toast payment logic
Just wraps existing calls; no new behavior.
"""
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

# Import existing Stripe service
from .stripe_service import StripeService


def create_payment_session(
    db: Session,
    merchant_id: str,
    package_id: str,
    provider: str = "stripe"
) -> Dict[str, Any]:
    """
    Create payment session for Nova purchase.
    
    Args:
        db: Database session
        merchant_id: Merchant ID
        package_id: Package ID (e.g., "nova_100")
        provider: Payment provider ("stripe", "square", "toast" - only stripe implemented)
        
    Returns:
        Dict with checkout_url and session info
    """
    if provider == "stripe":
        return StripeService.create_checkout_session(db, merchant_id, package_id)
    else:
        raise ValueError(f"Payment provider {provider} not yet implemented")


def handle_payment_webhook(
    db: Session,
    provider: str,
    event_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Handle payment webhook event.
    
    Args:
        db: Database session
        provider: Payment provider ("stripe", "square", "toast")
        event_data: Webhook event data
        
    Returns:
        Dict with processing result
    """
    if provider == "stripe":
        return StripeService.handle_webhook_event(db, event_data)
    else:
        raise ValueError(f"Payment provider {provider} not yet implemented")


def get_payment_status(
    db: Session,
    payment_id: str,
    provider: str = "stripe"
) -> Optional[Dict[str, Any]]:
    """
    Get payment status.
    
    Args:
        db: Database session
        payment_id: Payment ID
        provider: Payment provider
        
    Returns:
        Dict with payment status or None if not found
    """
    if provider == "stripe":
        # Import here to avoid circular imports
        from .stripe_service import StripePayment
        payment = db.query(StripePayment).filter(StripePayment.id == payment_id).first()
        if payment:
            return {
                "id": payment.id,
                "status": payment.status,
                "amount_usd": payment.amount_usd,
                "nova_issued": payment.nova_issued,
                "created_at": payment.created_at.isoformat() if payment.created_at else None,
            }
        return None
    else:
        raise ValueError(f"Payment provider {provider} not yet implemented")


