"""
Twilio SMS reply webhook — /v1/webhooks/twilio-arrival-sms

Handles merchant SMS replies for EV Arrival sessions.
Parses DONE {reply_code}, HELP, and CANCEL.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.arrival_session import ArrivalSession
from app.models.billing_event import BillingEvent
from app.services.analytics import get_analytics_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


def _twiml_response(message: str) -> Response:
    """Return a TwiML response for Twilio."""
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@router.post("/twilio-arrival-sms")
async def handle_twilio_sms(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle inbound SMS from Twilio.
    Expected formats:
        DONE 1234  — merchant confirms session with reply code 1234
        HELP       — send help link
        CANCEL     — cancel session
    """
    form = await request.form()
    body = (form.get("Body") or "").strip().upper()
    from_phone = form.get("From", "")

    logger.info(f"Twilio SMS received from {from_phone[:6]}***: '{body}'")

    # Parse DONE {code}
    if body.startswith("DONE"):
        parts = body.split()
        if len(parts) >= 2:
            reply_code = parts[1]
        else:
            # No code provided — try to find session by phone
            return _twiml_response(
                "Please include the 4-digit code from the notification. "
                "Example: DONE 1234"
            )

        # Look up session by reply code
        session = (
            db.query(ArrivalSession)
            .filter(
                ArrivalSession.merchant_reply_code == reply_code,
                ArrivalSession.status.in_(("arrived", "merchant_notified")),
            )
            .first()
        )

        if not session:
            return _twiml_response(
                f"No active arrival found for code {reply_code}. "
                "Please check the code in the notification."
            )

        # Confirm delivery
        now = datetime.utcnow()
        session.merchant_confirmed_at = now

        # Determine billing total: POS > merchant_reported > driver_estimate
        billing_total = None
        total_source = None

        if session.total_source == "pos" and session.order_total_cents:
            billing_total = session.order_total_cents
            total_source = "pos"
        elif session.merchant_reported_total_cents and session.merchant_reported_total_cents > 0:
            billing_total = session.merchant_reported_total_cents
            total_source = "merchant_reported"
        elif session.driver_estimate_cents and session.driver_estimate_cents > 0:
            billing_total = session.driver_estimate_cents
            total_source = "driver_estimate"

        if billing_total and billing_total > 0:
            billable = (billing_total * session.platform_fee_bps) // 10000
            session.billable_amount_cents = billable
            session.billing_status = "pending"
            session.status = "completed"
            session.completed_at = now

            billing_event = BillingEvent(
                arrival_session_id=session.id,
                merchant_id=session.merchant_id,
                order_total_cents=billing_total,
                fee_bps=session.platform_fee_bps,
                billable_cents=billable,
                total_source=total_source,
            )
            db.add(billing_event)
        else:
            session.status = "completed_unbillable"
            session.completed_at = now

        db.commit()

        try:
            analytics = get_analytics_client()
            if analytics:
                analytics.capture(
                    distinct_id=str(session.driver_id),
                    event="ev_arrival.merchant_confirmed",
                    properties={
                        "session_id": str(session.id),
                        "method": "sms_reply",
                        "reply_code": reply_code,
                    },
                )
        except Exception:
            pass

        return _twiml_response(
            f"Order #{session.order_number or 'N/A'} marked as delivered. Thank you!"
        )

    elif body == "HELP":
        return _twiml_response(
            "Visit your Nerava merchant dashboard at merchant.nerava.network "
            "for arrival details and settings."
        )

    elif body == "CANCEL":
        # Find most recent active session for this phone
        # This is a best-effort match by phone number
        return _twiml_response(
            "To cancel an arrival, please use the merchant dashboard at "
            "merchant.nerava.network or reply DONE when delivered."
        )

    else:
        return _twiml_response(
            "Reply DONE followed by the 4-digit code when the order is delivered. "
            "Example: DONE 1234. Reply HELP for support."
        )
