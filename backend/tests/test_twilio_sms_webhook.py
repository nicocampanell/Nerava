from datetime import datetime, timedelta

import pytest
from app.models import User
from app.models.arrival_session import ArrivalSession
from app.models.billing_event import BillingEvent
from app.models.while_you_charge import Merchant
from tests.helpers.ev_arrival_test_utils import ensure_ev_arrival_routers


@pytest.fixture(scope="session", autouse=True)
def _ensure_twilio_routes():
    ensure_ev_arrival_routers()


@pytest.fixture
def driver(db):
    user = User(email="driver@sms.test", is_active=True, role_flags="driver")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def merchant(db):
    merchant = Merchant(
        id="m_sms_1",
        name="SMS Merchant",
        lat=30.2672,
        lng=-97.7431,
        category="coffee",
    )
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant


def _create_session(db, driver_id, merchant_id, reply_code, total_cents=None):
    session = ArrivalSession(
        driver_id=driver_id,
        merchant_id=merchant_id,
        arrival_type="ev_curbside",
        status="arrived",
        merchant_reply_code=reply_code,
        order_number="1234",
        order_total_cents=total_cents,
        total_source="pos" if total_cents else None,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_twilio_done_confirms_session(client, db, driver, merchant):
    session = _create_session(db, driver.id, merchant.id, "1234", total_cents=2500)
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE 1234", "From": "+15125551234"},
    )
    assert response.status_code == 200
    db.refresh(session)
    assert session.status == "completed"


def test_twilio_done_without_code_returns_error_message(client):
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE", "From": "+15125551234"},
    )
    assert response.status_code == 200
    assert "Please include the 4-digit code" in response.text


def test_twilio_done_invalid_code_returns_no_active_message(client):
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE 9999", "From": "+15125551234"},
    )
    assert response.status_code == 200
    assert "No active arrival found" in response.text


def test_twilio_help_returns_dashboard_url(client):
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "HELP", "From": "+15125551234"},
    )
    assert response.status_code == 200
    assert "merchant.nerava.network" in response.text


def test_twilio_random_text_returns_usage(client):
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "HELLO", "From": "+15125551234"},
    )
    assert response.status_code == 200
    assert "Reply DONE" in response.text


def test_twilio_done_creates_billing_event_if_total_available(client, db, driver, merchant):
    session = _create_session(db, driver.id, merchant.id, "5678", total_cents=3200)
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE 5678", "From": "+15125551234"},
    )
    assert response.status_code == 200
    billing = db.query(BillingEvent).filter(BillingEvent.arrival_session_id == session.id).first()
    assert billing is not None


def test_twilio_done_without_total_marks_unbillable(client, db, driver, merchant):
    session = _create_session(db, driver.id, merchant.id, "7777", total_cents=None)
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE 7777", "From": "+15125551234"},
    )
    assert response.status_code == 200
    db.refresh(session)
    assert session.status == "completed_unbillable"


def test_twilio_response_is_valid_twiml_xml(client, db, driver, merchant):
    _create_session(db, driver.id, merchant.id, "8888", total_cents=1000)
    response = client.post(
        "/v1/webhooks/twilio-arrival-sms",
        data={"Body": "DONE 8888", "From": "+15125551234"},
    )
    assert response.headers["content-type"].startswith("application/xml")
    assert response.text.startswith("<?xml")
    assert "<Response><Message>" in response.text
