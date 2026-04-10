"""
Consolidated Stripe Webhook Endpoint

Single entry point for all Stripe webhook events, routing to the appropriate
service handler based on event type and metadata.

Existing per-service webhook endpoints remain for backward compatibility.
"""
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stripe", tags=["stripe_webhooks"])


def _construct_event(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify Stripe signature and construct event object."""
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    if not webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not configured, skipping signature verification")
        try:
            return stripe.Event.construct_from(
                stripe.util.convert_to_stripe_object(
                    stripe.util.json.loads(payload),
                    stripe.api_key,
                ),
                stripe.api_key,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    try:
        return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")


@router.post("/webhooks", summary="Consolidated Stripe webhook endpoint")
async def stripe_webhooks(request: Request, db: Session = Depends(get_db)):
    """
    Single Stripe webhook endpoint that routes events to the appropriate handler.

    Handles:
    - checkout.session.completed (campaign funding, merchant subscriptions)
    - customer.subscription.updated / deleted (merchant subscriptions)
    - transfer.created / transfer.reversed (driver payouts)
    - payout.paid / payout.failed (driver payouts)

    Returns 200 for unhandled event types to avoid Stripe retries.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    event = _construct_event(payload, sig_header)

    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    logger.info(f"Stripe webhook received: {event_type}")

    try:
        if event_type == "checkout.session.completed":
            return _handle_checkout_completed(db, data_object, event)

        elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            return _handle_subscription_event(db, event_type, data_object)

        elif event_type in ("transfer.created", "transfer.reversed"):
            return _handle_transfer_event(db, event_type, data_object)

        elif event_type in ("payout.paid", "payout.failed"):
            return _handle_payout_event(db, event_type, data_object)

        else:
            logger.debug(f"Unhandled Stripe event type: {event_type}")
            return {"received": True, "handled": False, "event_type": event_type}

    except Exception as e:
        logger.error(f"Error processing Stripe webhook {event_type}: {e}", exc_info=True)
        # Return 200 to prevent Stripe from retrying on application errors.
        # The error is logged for investigation.
        return {"received": True, "error": str(e)}


def _handle_checkout_completed(db: Session, data_object: dict, event) -> dict:
    """Route checkout.session.completed based on metadata type."""
    metadata = data_object.get("metadata", {})
    checkout_type = metadata.get("type", "")

    if checkout_type == "campaign_funding":
        from app.services.campaign_service import CampaignService

        stripe_session_id = data_object.get("id", "")
        payment_intent_id = data_object.get("payment_intent")
        result = CampaignService.fund_campaign(
            db,
            checkout_session_id=stripe_session_id,
            payment_intent_id=payment_intent_id,
        )
        logger.info(f"Campaign funding processed: {result}")
        return {"received": True, "handler": "campaign_funding", **result}

    elif checkout_type == "merchant_subscription" or metadata.get("plan"):
        # Merchant subscription checkout — delegate to subscription service
        from app.services.merchant_subscription_service import handle_checkout_completed

        result = handle_checkout_completed(db, data_object)
        logger.info(f"Merchant subscription checkout processed: sub_id={getattr(result, 'id', None)}")
        return {"received": True, "handler": "merchant_subscription"}

    else:
        # Unknown checkout type — log and acknowledge
        logger.debug(f"Checkout completed with unrecognized type: {checkout_type}")
        return {"received": True, "handler": "checkout_unhandled", "metadata_type": checkout_type}


def _handle_subscription_event(db: Session, event_type: str, data_object: dict) -> dict:
    """Handle merchant subscription lifecycle events."""
    from app.services.merchant_subscription_service import (
        handle_subscription_deleted,
        handle_subscription_updated,
    )

    if event_type == "customer.subscription.updated":
        handle_subscription_updated(db, data_object)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_deleted(db, data_object)

    logger.info(f"Subscription event processed: {event_type} sub={data_object.get('id')}")
    return {"received": True, "handler": "merchant_subscription", "event_type": event_type}


def _handle_transfer_event(db: Session, event_type: str, data_object: dict) -> dict:
    """Handle transfer events for driver payouts."""
    from app.services.payout_service import PayoutService

    transfer_id = data_object.get("id", "")

    if event_type == "transfer.created":
        result = PayoutService._handle_transfer_paid(db, data_object)
    elif event_type == "transfer.reversed":
        result = PayoutService._handle_transfer_failed(db, data_object)
    else:
        result = {"status": "ignored"}

    logger.info(f"Transfer event processed: {event_type} transfer={transfer_id} result={result}")
    return {"received": True, "handler": "payout_transfer", "event_type": event_type, **result}


def _handle_payout_event(db: Session, event_type: str, data_object: dict) -> dict:
    """Handle payout lifecycle events."""
    from app.services.payout_service import PayoutService

    payout_id = data_object.get("id", "")

    if event_type == "payout.paid":
        result = PayoutService._handle_transfer_paid(db, data_object)
    elif event_type == "payout.failed":
        result = PayoutService._handle_transfer_failed(db, data_object)
    else:
        result = {"status": "ignored"}

    logger.info(f"Payout event processed: {event_type} payout={payout_id} result={result}")
    return {"received": True, "handler": "payout", "event_type": event_type, **result}
