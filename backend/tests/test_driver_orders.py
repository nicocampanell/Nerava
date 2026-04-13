"""
Tests for the driver in-app browser ordering endpoints.

Covers:
  1. POST /v1/driver/orders/start — creates an order record
  2. POST /v1/driver/orders/complete — marks order as completed
  3. Ownership check — driver cannot complete another driver's order (403)
  4. Not-found check — completing a nonexistent order returns 404
"""

from __future__ import annotations

import logging
import uuid

import pytest
from app.dependencies.driver import get_current_driver
from app.main_simple import app
from app.models.driver_order import DriverOrder
from app.models.user import User

logger = logging.getLogger(__name__)

TOAST_URL = "https://www.toasttab.com/local/order/the-heights"


def _make_user(db, label: str = "driver") -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@test.nerava.network",
        password_hash="hashed",
        is_active=True,
        role_flags="driver",
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def driver_a(db):
    return _make_user(db, label="driver-a")


@pytest.fixture()
def driver_b(db):
    return _make_user(db, label="driver-b")


@pytest.fixture()
def auth_as_driver_a(driver_a):
    """Override get_current_driver to return driver_a."""
    app.dependency_overrides[get_current_driver] = lambda: driver_a
    yield driver_a
    app.dependency_overrides.pop(get_current_driver, None)


@pytest.fixture()
def auth_as_driver_b(driver_b):
    """Override get_current_driver to return driver_b."""
    app.dependency_overrides[get_current_driver] = lambda: driver_b
    yield driver_b
    app.dependency_overrides.pop(get_current_driver, None)


def test_start_order(client, db, auth_as_driver_a):
    """POST /v1/driver/orders/start creates an order with status=started."""
    resp = client.post(
        "/v1/driver/orders/start",
        json={
            "merchant_id": "m_heights_pizzeria",
            "ordering_url": TOAST_URL,
            "merchant_name": "The Heights Pizzeria",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["order_id"] is not None

    # Verify the row in the database
    order = db.query(DriverOrder).filter(DriverOrder.id == data["order_id"]).first()
    assert order is not None
    assert order.driver_id == auth_as_driver_a.id
    assert order.ordering_url == TOAST_URL
    assert order.merchant_name == "The Heights Pizzeria"
    assert order.status == "started"


def test_complete_order(client, db, auth_as_driver_a):
    """Start then complete an order — status should become completed."""
    # Start
    start_resp = client.post(
        "/v1/driver/orders/start",
        json={
            "merchant_id": "m_heights_pizzeria",
            "ordering_url": TOAST_URL,
            "merchant_name": "Heights Pizzeria",
        },
    )
    assert start_resp.status_code == 200
    order_id = start_resp.json()["order_id"]

    # Complete
    complete_resp = client.post(
        "/v1/driver/orders/complete",
        json={
            "order_id": order_id,
            "completion_url": "https://www.toasttab.com/local/order/the-heights/confirmation/123",
        },
    )
    assert complete_resp.status_code == 200
    data = complete_resp.json()
    assert data["status"] == "completed"
    assert data["order_id"] == order_id

    # Verify in DB
    order = db.query(DriverOrder).filter(DriverOrder.id == order_id).first()
    assert order is not None
    assert order.status == "completed"
    assert order.completed_at is not None
    assert (
        order.completion_url == "https://www.toasttab.com/local/order/the-heights/confirmation/123"
    )


def test_complete_order_wrong_driver(client, db, driver_a, driver_b):
    """Driver B cannot complete driver A's order — expect 403."""
    # Authenticate as driver A and start an order
    app.dependency_overrides[get_current_driver] = lambda: driver_a
    start_resp = client.post(
        "/v1/driver/orders/start",
        json={
            "merchant_id": "m_heights_pizzeria",
            "ordering_url": TOAST_URL,
            "merchant_name": "Heights Pizzeria",
        },
    )
    assert start_resp.status_code == 200
    order_id = start_resp.json()["order_id"]

    # Switch to driver B and try to complete driver A's order
    app.dependency_overrides[get_current_driver] = lambda: driver_b
    complete_resp = client.post(
        "/v1/driver/orders/complete",
        json={"order_id": order_id},
    )
    assert complete_resp.status_code == 403

    # Cleanup
    app.dependency_overrides.pop(get_current_driver, None)


def test_complete_nonexistent_order(client, db, auth_as_driver_a):
    """Completing an order that does not exist returns 404."""
    fake_id = str(uuid.uuid4())
    resp = client.post(
        "/v1/driver/orders/complete",
        json={"order_id": fake_id},
    )
    assert resp.status_code == 404
