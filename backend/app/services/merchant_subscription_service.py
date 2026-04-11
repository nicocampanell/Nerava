"""
Merchant Subscription Service

Handles Stripe Checkout for Pro tier and Nerava Ads subscriptions.
"""
import logging
import uuid
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import MerchantAccount
from app.models.merchant_subscription import MerchantSubscription

logger = logging.getLogger(__name__)

# Stripe price ID mapping
PLAN_PRICE_IDS = {
    "pro": lambda: settings.STRIPE_PRICE_PRO_MONTHLY,
    "ads_flat": lambda: settings.STRIPE_PRICE_ADS_FLAT_MONTHLY,
}


def _get_stripe():
    """Lazy import and configure stripe."""
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_checkout_session(
    db: Session,
    merchant_account_id: str,
    place_id: str,
    plan: str,
    success_url: str,
    cancel_url: str,
) -> Dict[str, str]:
    """
    Create a Stripe Checkout Session in subscription mode.
    Returns dict with checkout_url.
    """
    if plan not in PLAN_PRICE_IDS:
        raise ValueError(f"Unknown plan: {plan}. Must be one of: {list(PLAN_PRICE_IDS.keys())}")

    price_id = PLAN_PRICE_IDS[plan]()
    if not price_id:
        raise ValueError(f"Stripe price ID not configured for plan '{plan}'")

    stripe = _get_stripe()

    # Get or create Stripe customer
    account = db.query(MerchantAccount).filter(MerchantAccount.id == merchant_account_id).first()
    if not account:
        raise ValueError(f"Merchant account {merchant_account_id} not found")

    # Check for existing active subscription of same plan
    existing = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.merchant_account_id == merchant_account_id,
            MerchantSubscription.plan == plan,
            MerchantSubscription.status == "active",
        )
        .first()
    )
    if existing:
        raise ValueError(f"Already have an active '{plan}' subscription")

    # Find existing Stripe customer or create one
    existing_sub = (
        db.query(MerchantSubscription)
        .filter(MerchantSubscription.merchant_account_id == merchant_account_id)
        .filter(MerchantSubscription.stripe_customer_id.isnot(None))
        .first()
    )

    customer_id = existing_sub.stripe_customer_id if existing_sub else None

    checkout_params = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "merchant_account_id": merchant_account_id,
            "place_id": place_id,
            "plan": plan,
        },
    }

    if customer_id:
        checkout_params["customer"] = customer_id
    else:
        checkout_params["customer_creation"] = "always"

    session = stripe.checkout.Session.create(**checkout_params)

    return {"checkout_url": session.url, "session_id": session.id}


def handle_checkout_completed(db: Session, stripe_session: Dict) -> Optional[MerchantSubscription]:
    """Handle checkout.session.completed webhook — create MerchantSubscription row."""
    metadata = stripe_session.get("metadata", {})
    merchant_account_id = metadata.get("merchant_account_id")
    plan = metadata.get("plan")
    place_id = metadata.get("place_id")

    if not merchant_account_id or not plan:
        logger.warning(f"Checkout completed missing metadata: {metadata}")
        return None

    stripe_sub_id = stripe_session.get("subscription")
    stripe_customer_id = stripe_session.get("customer")

    # Fetch subscription details from Stripe for period info
    stripe = _get_stripe()
    stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)

    sub = MerchantSubscription(
        id=str(uuid.uuid4()),
        merchant_account_id=merchant_account_id,
        place_id=place_id,
        plan=plan,
        status="active",
        stripe_subscription_id=stripe_sub_id,
        stripe_customer_id=stripe_customer_id,
        current_period_start=datetime.utcfromtimestamp(stripe_sub.current_period_start),
        current_period_end=datetime.utcfromtimestamp(stripe_sub.current_period_end),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info(f"Created subscription {sub.id} for merchant {merchant_account_id} plan={plan}")
    return sub


def handle_subscription_updated(db: Session, stripe_sub: Dict) -> None:
    """Handle customer.subscription.updated webhook."""
    sub = (
        db.query(MerchantSubscription)
        .filter(MerchantSubscription.stripe_subscription_id == stripe_sub["id"])
        .first()
    )
    if not sub:
        logger.warning(f"Subscription not found for stripe_sub {stripe_sub['id']}")
        return

    sub.status = stripe_sub.get("status", sub.status)
    if stripe_sub.get("current_period_start"):
        sub.current_period_start = datetime.utcfromtimestamp(stripe_sub["current_period_start"])
    if stripe_sub.get("current_period_end"):
        sub.current_period_end = datetime.utcfromtimestamp(stripe_sub["current_period_end"])
    if stripe_sub.get("canceled_at"):
        sub.canceled_at = datetime.utcfromtimestamp(stripe_sub["canceled_at"])
    sub.updated_at = datetime.utcnow()
    db.commit()


def handle_subscription_deleted(db: Session, stripe_sub: Dict) -> None:
    """Handle customer.subscription.deleted webhook."""
    sub = (
        db.query(MerchantSubscription)
        .filter(MerchantSubscription.stripe_subscription_id == stripe_sub["id"])
        .first()
    )
    if not sub:
        return

    sub.status = "canceled"
    sub.canceled_at = datetime.utcnow()
    sub.updated_at = datetime.utcnow()
    db.commit()
    logger.info(f"Subscription {sub.id} canceled")


def is_pro(db: Session, merchant_account_id: str, place_id: Optional[str] = None) -> bool:
    """Check if merchant has an active 'pro' subscription."""
    query = db.query(MerchantSubscription).filter(
        MerchantSubscription.merchant_account_id == merchant_account_id,
        MerchantSubscription.plan == "pro",
        MerchantSubscription.status == "active",
    )
    if place_id:
        query = query.filter(MerchantSubscription.place_id == place_id)
    return query.first() is not None


def get_subscription(db: Session, merchant_account_id: str) -> Optional[Dict]:
    """Get current subscription details for a merchant."""
    sub = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.merchant_account_id == merchant_account_id,
            MerchantSubscription.status.in_(["active", "past_due"]),
        )
        .order_by(MerchantSubscription.created_at.desc())
        .first()
    )
    if not sub:
        return None

    return {
        "id": sub.id,
        "plan": sub.plan,
        "status": sub.status,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "canceled_at": sub.canceled_at.isoformat() if sub.canceled_at else None,
    }


def cancel_subscription(db: Session, merchant_account_id: str) -> bool:
    """Cancel the merchant's active subscription at period end."""
    sub = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.merchant_account_id == merchant_account_id,
            MerchantSubscription.status == "active",
        )
        .first()
    )
    if not sub or not sub.stripe_subscription_id:
        return False

    stripe = _get_stripe()
    stripe.Subscription.modify(
        sub.stripe_subscription_id,
        cancel_at_period_end=True,
    )
    sub.canceled_at = datetime.utcnow()
    sub.updated_at = datetime.utcnow()
    db.commit()
    logger.info(f"Subscription {sub.id} set to cancel at period end")
    return True
