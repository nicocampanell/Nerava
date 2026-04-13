"""
Tests for DELETE /v1/auth/tesla/vehicles/{vehicle_id} (soft-delete).

Self-contained test file with its own fixtures, following the
test_payout_full_paths.py pattern. Uses in-memory SQLite via conftest.

Coverage:
  1. Happy path: remove own vehicle, verify deleted_at set
  2. Vehicle not found: non-existent vehicle_id returns 403
  3. Wrong driver: cannot remove another driver's vehicle
  4. Active session: returns 409 when driver has an active session
  5. Removed vehicle not in status: soft-deleted vehicle excluded
     from GET /v1/auth/tesla/status
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from app.core.token_encryption import encrypt_token
from app.models.session_event import SessionEvent
from app.models.tesla_connection import TeslaConnection
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(db: Session, label: str = "vehicle-driver") -> User:
    user = User(
        public_id=str(uuid.uuid4()),
        email=f"{label}-{uuid.uuid4().hex[:8]}@test.nerava.network",
        password_hash="hashed",
        is_active=True,
        role_flags="driver",
    )
    db.add(user)
    db.flush()
    return user


def _make_tesla_connection(
    db: Session,
    user: User,
    *,
    vehicle_name: str = "Test Tesla",
    vin: str = "5YJ3E1EA0PF000001",
) -> TeslaConnection:
    conn = TeslaConnection(
        id=str(uuid.uuid4()),
        user_id=user.id,
        access_token=encrypt_token("fake-access-token"),
        refresh_token=encrypt_token("fake-refresh-token"),
        token_expires_at=datetime.utcnow() + timedelta(hours=1),
        vehicle_id=str(uuid.uuid4()),
        vin=vin,
        vehicle_name=vehicle_name,
        vehicle_model="Model 3",
        is_active=True,
    )
    db.add(conn)
    db.flush()
    return conn


def _make_active_session(db: Session, driver: User) -> SessionEvent:
    """Create an active (session_end=None) charging session."""
    evt = SessionEvent(
        id=str(uuid.uuid4()),
        driver_user_id=driver.id,
        session_start=datetime.utcnow(),
        session_end=None,
        source="tesla_api",
    )
    db.add(evt)
    db.flush()
    return evt


def _auth_header(user: User) -> dict:
    """Create a valid JWT for the given user."""
    from app.core.security import create_access_token

    token = create_access_token(user.public_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_remove_vehicle_success(client: TestClient, db: Session):
    """Happy path: driver removes their own vehicle."""
    driver = _make_driver(db, "remove-ok")
    conn = _make_tesla_connection(db, driver)
    db.commit()

    vehicle_id = conn.id
    resp = client.delete(
        f"/v1/auth/tesla/vehicles/{vehicle_id}",
        headers=_auth_header(driver),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "removed"
    assert body["vehicle_id"] == vehicle_id

    # Verify soft-delete fields in DB
    db.expire_all()
    updated = db.query(TeslaConnection).filter(TeslaConnection.id == vehicle_id).first()
    assert updated is not None
    assert updated.deleted_at is not None
    assert updated.is_active is False
    assert updated.access_token == ""
    assert updated.refresh_token == ""


def test_remove_vehicle_not_found(client: TestClient, db: Session):
    """Non-existent vehicle_id returns 403."""
    driver = _make_driver(db, "remove-nf")
    db.commit()

    resp = client.delete(
        f"/v1/auth/tesla/vehicles/{uuid.uuid4()}",
        headers=_auth_header(driver),
    )
    assert resp.status_code == 403


def test_remove_vehicle_wrong_driver(client: TestClient, db: Session):
    """Cannot remove another driver's vehicle."""
    owner = _make_driver(db, "remove-owner")
    attacker = _make_driver(db, "remove-attacker")
    conn = _make_tesla_connection(db, owner)
    db.commit()

    resp = client.delete(
        f"/v1/auth/tesla/vehicles/{conn.id}",
        headers=_auth_header(attacker),
    )
    assert resp.status_code == 403


def test_remove_vehicle_active_session(client: TestClient, db: Session):
    """Returns 409 when driver has an active charging session."""
    driver = _make_driver(db, "remove-active")
    conn = _make_tesla_connection(db, driver)
    _make_active_session(db, driver)
    db.commit()

    resp = client.delete(
        f"/v1/auth/tesla/vehicles/{conn.id}",
        headers=_auth_header(driver),
    )
    assert resp.status_code == 409
    assert "active charging session" in resp.json()["detail"]


def test_removed_vehicle_not_in_status(client: TestClient, db: Session):
    """After soft delete, GET /v1/auth/tesla/status returns connected=False."""
    driver = _make_driver(db, "remove-status")
    conn = _make_tesla_connection(db, driver)
    db.commit()

    headers = _auth_header(driver)

    # Remove the vehicle
    resp = client.delete(
        f"/v1/auth/tesla/vehicles/{conn.id}",
        headers=headers,
    )
    assert resp.status_code == 200

    # Now status should show not connected
    status_resp = client.get("/v1/auth/tesla/status", headers=headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["connected"] is False
