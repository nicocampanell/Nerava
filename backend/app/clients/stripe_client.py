"""
Stripe client for Connect payouts (test mode)
"""
import os
from typing import Any, Dict, Optional

# Optional Stripe import (will work in simulation mode without it)
try:
    import stripe
    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False
    stripe = None

_stripe_instance: Optional[Any] = None


def get_stripe():
    """
    Get configured Stripe SDK instance.
    Returns None if STRIPE_SECRET not set or stripe package not installed (will use simulation).
    """
    global _stripe_instance
    
    if not HAS_STRIPE:
        return None  # Stripe package not installed, use simulation
    
    if _stripe_instance is not None:
        return _stripe_instance
    
    stripe_secret = os.getenv("STRIPE_SECRET")
    if not stripe_secret:
        return None  # Will use simulation mode
    
    stripe.api_key = stripe_secret
    _stripe_instance = stripe
    return _stripe_instance


def create_express_account_if_needed(user_id: int) -> Optional[str]:
    """
    Create or retrieve Stripe Express account for user (optional).
    In test mode, returns a fake account ID if keys are absent.
    """
    stripe_client = get_stripe()
    if not stripe_client:
        # Simulation mode: return fake account ID
        return f"acct_test_{user_id}"
    
    # TODO: Implement real Express account creation
    # For now, return a test account ID
    return f"acct_test_{user_id}"


def create_transfer(
    connected_account_id: str,
    amount_cents: int,
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a Transfer to a connected account (test mode).
    
    Returns:
        {
            "id": transfer.id,
            "status": "pending" or "paid",
            "amount": amount_cents
        }
    """
    stripe_client = get_stripe()
    if not stripe_client:
        # Simulation mode: return mock transfer
        return {
            "id": f"tr_mock_{metadata.get('payment_id', 'unknown')}",
            "status": "paid",  # Simulate immediate success
            "amount": amount_cents,
            "simulated": True
        }
    
    # Real Stripe API call (test mode)
    try:
        transfer = stripe_client.Transfer.create(
            amount=amount_cents,
            currency="usd",
            destination=connected_account_id,
            metadata=metadata,
            idempotency_key=metadata.get("client_token")
        )
        return {
            "id": transfer.id,
            "status": transfer.status,
            "amount": transfer.amount
        }
    except Exception as e:
        raise Exception(f"Stripe transfer failed: {str(e)}")

