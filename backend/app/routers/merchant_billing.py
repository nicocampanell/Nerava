"""
Merchant Billing Router

Handles subscription management via Stripe Checkout:
- Create checkout session for Pro / Ads subscriptions
- Get subscription status
- Cancel subscription
- Stripe webhook for subscription events
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.dependencies_domain import get_current_user
from app.models import User
from app.services.merchant_onboarding_service import create_or_get_merchant_account
from app.services.merchant_subscription_service import (
    cancel_subscription,
    create_checkout_session,
    get_subscription,
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/merchant/billing", tags=["merchant_billing"])


class SubscribeRequest(BaseModel):
    place_id: str
    plan: str  # "pro" | "ads_flat"


class SubscribeResponse(BaseModel):
    checkout_url: str
    session_id: str


@router.post("/subscribe", response_model=SubscribeResponse, summary="Create subscription checkout")
async def subscribe(
    request: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Checkout session for a subscription plan."""
    try:
        merchant_account = create_or_get_merchant_account(db, current_user.id)
        portal_url = settings.MERCHANT_PORTAL_URL

        result = create_checkout_session(
            db=db,
            merchant_account_id=merchant_account.id,
            place_id=request.place_id,
            plan=request.plan,
            success_url=f"{portal_url}/billing?success=true",
            cancel_url=f"{portal_url}/billing?canceled=true",
        )

        return SubscribeResponse(
            checkout_url=result["checkout_url"],
            session_id=result["session_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating checkout session: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create checkout session",
        )


@router.get("/subscription", summary="Get subscription status")
async def get_subscription_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns current subscription details or null."""
    merchant_account = create_or_get_merchant_account(db, current_user.id)
    sub = get_subscription(db, merchant_account.id)
    return {"subscription": sub}


@router.post("/cancel", summary="Cancel subscription at period end")
async def cancel_subscription_endpoint(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel the active subscription at end of current billing period."""
    merchant_account = create_or_get_merchant_account(db, current_user.id)
    success = cancel_subscription(db, merchant_account.id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active subscription found")
    return {"ok": True, "message": "Subscription will be canceled at end of billing period"}


@router.post("/portal", summary="Create Stripe Billing Portal session")
async def create_billing_portal(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Billing Portal session for the merchant to manage subscriptions and payment methods."""
    import stripe as stripe_module

    stripe_module.api_key = settings.STRIPE_SECRET_KEY
    if not stripe_module.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    merchant_account = create_or_get_merchant_account(db, current_user.id)

    # Find the stripe_customer_id from an existing subscription
    from app.models.merchant_subscription import MerchantSubscription

    sub = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.merchant_account_id == merchant_account.id,
            MerchantSubscription.stripe_customer_id.isnot(None),
        )
        .order_by(MerchantSubscription.created_at.desc())
        .first()
    )
    if not sub or not sub.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Stripe customer found. Subscribe to a plan first.",
        )

    try:
        portal_url = settings.MERCHANT_PORTAL_URL
        session = stripe_module.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f"{portal_url}/billing",
        )
        return {"url": session.url}
    except Exception as e:
        logger.error(f"Error creating billing portal session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create billing portal session")


@router.get("/invoices", summary="Get invoice history")
async def get_invoices(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List Stripe invoices for the merchant's billing account."""
    import stripe as stripe_module

    stripe_module.api_key = settings.STRIPE_SECRET_KEY
    if not stripe_module.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    merchant_account = create_or_get_merchant_account(db, current_user.id)

    from app.models.merchant_subscription import MerchantSubscription

    sub = (
        db.query(MerchantSubscription)
        .filter(
            MerchantSubscription.merchant_account_id == merchant_account.id,
            MerchantSubscription.stripe_customer_id.isnot(None),
        )
        .order_by(MerchantSubscription.created_at.desc())
        .first()
    )
    if not sub or not sub.stripe_customer_id:
        return {"invoices": []}

    try:
        invoices = stripe_module.Invoice.list(
            customer=sub.stripe_customer_id,
            limit=min(limit, 100),
        )
        result = []
        for inv in invoices.data:
            result.append({
                "id": inv.id,
                "amount_due": inv.amount_due,
                "status": inv.status,
                "created": inv.created,
                "invoice_pdf": inv.invoice_pdf,
                "hosted_invoice_url": inv.hosted_invoice_url,
            })
        return {"invoices": result}
    except Exception as e:
        logger.error(f"Error fetching invoices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch invoices")


@router.get("/payment-status", summary="Get card-on-file and billing status")
async def get_payment_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns card-on-file status and billing type for the merchant."""
    from app.services.auth_service import AuthService

    merchant = AuthService.get_user_merchant(db, current_user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    return {
        "has_card": merchant.card_last4 is not None,
        "card_last4": merchant.card_last4,
        "card_brand": merchant.card_brand,
        "billing_type": merchant.billing_type or "free",
        "stripe_customer_id": merchant.stripe_customer_id,
    }


@router.post("/setup-card", summary="Create Stripe Checkout session to save a card")
async def setup_card(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Checkout session in setup mode to save a card for pay-as-you-go billing."""
    import stripe as stripe_module

    stripe_module.api_key = settings.STRIPE_SECRET_KEY
    if not stripe_module.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    from app.services.auth_service import AuthService

    merchant = AuthService.get_user_merchant(db, current_user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    # Create or reuse Stripe customer
    if not merchant.stripe_customer_id:
        customer = stripe_module.Customer.create(
            name=merchant.name,
            metadata={"merchant_id": str(merchant.id), "user_id": str(current_user.id)},
        )
        merchant.stripe_customer_id = customer.id
        db.commit()

    portal_url = settings.MERCHANT_PORTAL_URL
    session = stripe_module.checkout.Session.create(
        mode="setup",
        customer=merchant.stripe_customer_id,
        payment_method_types=["card"],
        success_url=f"{portal_url}/billing?card_saved=true",
        cancel_url=f"{portal_url}/billing?card_saved=false",
        metadata={"merchant_id": str(merchant.id), "type": "card_setup"},
    )

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/set-billing-type", summary="Set billing type for the merchant")
async def set_billing_type(
    request: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set the billing type: 'pay_as_you_go' or 'campaign'."""
    from app.services.auth_service import AuthService

    billing_type = request.get("billing_type")
    if billing_type not in ("pay_as_you_go", "campaign", "free"):
        raise HTTPException(status_code=400, detail="Invalid billing_type")

    merchant = AuthService.get_user_merchant(db, current_user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    if billing_type == "pay_as_you_go" and not merchant.card_last4:
        raise HTTPException(status_code=400, detail="Add a card before selecting pay-as-you-go")

    merchant.billing_type = billing_type
    db.commit()

    return {"ok": True, "billing_type": billing_type}


@router.post("/remove-card", summary="Remove card on file")
async def remove_card(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove the saved card and revert billing to free."""
    from app.services.auth_service import AuthService

    merchant = AuthService.get_user_merchant(db, current_user.id)
    if not merchant:
        raise HTTPException(status_code=404, detail="Merchant not found")

    if not merchant.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No card on file")

    # Detach all payment methods from the Stripe customer
    import stripe as stripe_module
    stripe_module.api_key = settings.STRIPE_SECRET_KEY
    try:
        pms = stripe_module.PaymentMethod.list(customer=merchant.stripe_customer_id, type="card")
        for pm in pms.data:
            stripe_module.PaymentMethod.detach(pm.id)
    except Exception as e:
        logger.error(f"Failed to detach payment methods: {e}")

    merchant.card_last4 = None
    merchant.card_brand = None
    if merchant.billing_type == "pay_as_you_go":
        merchant.billing_type = "free"
    db.commit()

    return {"ok": True}


@router.post("/webhook", summary="Stripe merchant billing webhook")
async def stripe_merchant_webhook(request: Request, db: Session = Depends(get_db)):
    """Handle Stripe webhook events for merchant subscriptions."""
    import stripe

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    webhook_secret = settings.STRIPE_MERCHANT_WEBHOOK_SECRET
    if not webhook_secret:
        logger.warning("STRIPE_MERCHANT_WEBHOOK_SECRET not configured, skipping verification")
        try:
            event = stripe.Event.construct_from(
                stripe.util.convert_to_stripe_object(
                    stripe.util.json.loads(payload),
                    stripe.api_key,
                ),
                stripe.api_key,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        metadata = data_object.get("metadata", {})
        if metadata.get("type") == "card_setup":
            _handle_card_setup_completed(db, data_object)
        else:
            handle_checkout_completed(db, data_object)
    elif event_type == "customer.subscription.updated":
        handle_subscription_updated(db, data_object)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_deleted(db, data_object)
    else:
        logger.debug(f"Unhandled merchant billing event: {event_type}")

    return {"received": True}


def _handle_card_setup_completed(db: Session, data_object: dict):
    """Handle a Stripe Checkout session in setup mode — save card details to DomainMerchant."""
    import stripe as stripe_module

    stripe_module.api_key = settings.STRIPE_SECRET_KEY

    metadata = data_object.get("metadata", {})
    merchant_id = metadata.get("merchant_id")
    setup_intent_id = data_object.get("setup_intent")

    if not merchant_id or not setup_intent_id:
        logger.warning(f"Card setup webhook missing data: merchant_id={merchant_id}, setup_intent={setup_intent_id}")
        return

    from app.models.domain import DomainMerchant

    merchant = db.query(DomainMerchant).filter(DomainMerchant.id == merchant_id).first()
    if not merchant:
        logger.warning(f"Card setup: merchant {merchant_id} not found")
        return

    try:
        setup_intent = stripe_module.SetupIntent.retrieve(setup_intent_id)
        pm_id = setup_intent.payment_method
        if pm_id:
            pm = stripe_module.PaymentMethod.retrieve(pm_id)
            card = pm.get("card", {})
            merchant.card_last4 = card.get("last4")
            merchant.card_brand = card.get("brand")
            if merchant.billing_type == "free":
                merchant.billing_type = "pay_as_you_go"
            db.commit()
            logger.info(f"Card saved for merchant {merchant_id}: {card.get('brand')} ****{card.get('last4')}")
    except Exception as e:
        logger.error(f"Card setup webhook failed for merchant {merchant_id}: {e}", exc_info=True)
