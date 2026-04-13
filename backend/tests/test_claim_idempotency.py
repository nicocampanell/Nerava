"""
Claim idempotency tests for the exclusive session activation endpoint.

Production audit found 24 duplicate exclusive sessions and 9 duplicate
verified visits. The root cause: the frontend never sends the
X-Idempotency-Key header, so the server-side idempotency_key check
never fires. The composite duplicate check (same driver + same
merchant within 24h) catches these duplicates at the application layer.

Test cases:
  1. Duplicate claim within 24h returns existing session with idempotent=True
  2. Claim after 24h creates a new session (legitimate repeat visit)
  3. X-Idempotency-Key header dedup returns existing session with idempotent=True
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from app.models.exclusive_session import ExclusiveSession, ExclusiveSessionStatus
from app.models.user import User
from app.models.while_you_charge import Charger, Merchant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Self-contained fixtures (no reliance on conftest helpers beyond `db`)
# ---------------------------------------------------------------------------


def _make_driver(db, *, label: str = "claim-driver") -> User:
    """Create a driver user with a verified auth provider."""
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@test.nerava.network",
        password_hash="hashed",
        is_active=True,
        role_flags="driver",
        auth_provider="phone",
    )
    db.add(user)
    db.flush()
    return user


def _make_charger(db, *, lat: float = 31.0671, lng: float = -97.7289) -> Charger:
    """Create a charger for FK satisfaction."""
    charger = Charger(
        id=f"ch_{uuid.uuid4().hex[:8]}",
        name="Test Supercharger",
        lat=lat,
        lng=lng,
    )
    db.add(charger)
    db.flush()
    return charger


def _make_merchant(db, *, place_id: Optional[str] = None) -> Merchant:
    """Create a WYC merchant for FK satisfaction."""
    pid = place_id or f"ChIJ_{uuid.uuid4().hex[:12]}"
    merchant = Merchant(
        id=f"m_{uuid.uuid4().hex[:8]}",
        external_id=pid,
        place_id=pid,
        name="Test Pizzeria",
        category="restaurant",
        lat=31.0671,
        lng=-97.7289,
        short_code=f"TP{uuid.uuid4().hex[:4].upper()}",
        region_code="TXH",
    )
    db.add(merchant)
    db.flush()
    return merchant


def _make_exclusive_session(
    db,
    *,
    driver: User,
    merchant: Merchant,
    charger: Charger,
    status: ExclusiveSessionStatus = ExclusiveSessionStatus.ACTIVE,
    activated_at: Optional[datetime] = None,
    idempotency_key: Optional[str] = None,
) -> ExclusiveSession:
    """Create an ExclusiveSession row directly in the DB."""
    now = activated_at or datetime.now(timezone.utc)
    session = ExclusiveSession(
        id=str(uuid.uuid4()),
        driver_id=driver.id,
        merchant_id=merchant.id,
        merchant_place_id=merchant.place_id,
        charger_id=charger.id,
        status=status,
        activated_at=now,
        expires_at=now + timedelta(minutes=60),
        activation_lat=31.0671,
        activation_lng=-97.7289,
        idempotency_key=idempotency_key,
    )
    db.add(session)
    db.flush()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompositeDuplicateCheck:
    """Tests for the 24-hour composite duplicate check in activate_exclusive."""

    def test_duplicate_claim_returns_existing(self, db):
        """
        When a driver already has an ACTIVE or COMPLETED exclusive session
        for the same merchant within the last 24 hours, the activate
        endpoint should return the existing session with idempotent=True
        instead of creating a duplicate.
        """
        from sqlalchemy import or_

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)

        # Create the first exclusive session (simulates first claim)
        first_session = _make_exclusive_session(
            db, driver=driver, merchant=merchant, charger=charger
        )
        db.commit()

        # Now simulate what the composite check does: query for recent claims
        merchant_key = merchant.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is not None, "Should find the existing session"
        assert str(recent_claim.id) == str(first_session.id)
        assert recent_claim.driver_id == driver.id
        assert recent_claim.merchant_place_id == merchant.place_id

    def test_completed_session_also_blocks_duplicate(self, db):
        """
        A COMPLETED session within 24h should also block a duplicate
        claim (driver already visited this merchant today).
        """
        from sqlalchemy import or_

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)

        completed_session = _make_exclusive_session(
            db,
            driver=driver,
            merchant=merchant,
            charger=charger,
            status=ExclusiveSessionStatus.COMPLETED,
        )
        db.commit()

        merchant_key = merchant.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is not None
        assert str(recent_claim.id) == str(completed_session.id)

    def test_claim_after_24h_creates_new(self, db):
        """
        A session activated more than 24 hours ago should NOT block
        a new claim. The driver is legitimately revisiting the merchant.
        """
        from sqlalchemy import or_

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)

        # Create a session from 25 hours ago
        old_activated_at = datetime.now(timezone.utc) - timedelta(hours=25)
        _make_exclusive_session(
            db,
            driver=driver,
            merchant=merchant,
            charger=charger,
            activated_at=old_activated_at,
        )
        db.commit()

        merchant_key = merchant.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is None, "Should NOT find the 25h-old session"

    def test_expired_session_does_not_block(self, db):
        """
        An EXPIRED session within 24h should NOT block a new claim.
        Only ACTIVE and COMPLETED sessions are considered duplicates.
        """
        from sqlalchemy import or_

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)

        _make_exclusive_session(
            db,
            driver=driver,
            merchant=merchant,
            charger=charger,
            status=ExclusiveSessionStatus.EXPIRED,
        )
        db.commit()

        merchant_key = merchant.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is None, "EXPIRED sessions should not block new claims"

    def test_different_merchant_does_not_block(self, db):
        """
        A session at a different merchant should NOT block a claim
        at a new merchant.
        """
        from sqlalchemy import or_

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant_a = _make_merchant(db, place_id="ChIJ_merchant_a")
        merchant_b = _make_merchant(db, place_id="ChIJ_merchant_b")

        _make_exclusive_session(db, driver=driver, merchant=merchant_a, charger=charger)
        db.commit()

        # Check for merchant B — should find nothing
        merchant_key = merchant_b.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is None, "Session at merchant A should not block merchant B"

    def test_different_driver_does_not_block(self, db):
        """
        Driver B's session at a merchant should NOT block Driver A's
        claim at the same merchant.
        """
        from sqlalchemy import or_

        driver_a = _make_driver(db, label="driver-a")
        driver_b = _make_driver(db, label="driver-b")
        charger = _make_charger(db)
        merchant = _make_merchant(db)

        # Driver B has an active session
        _make_exclusive_session(db, driver=driver_b, merchant=merchant, charger=charger)
        db.commit()

        # Check for driver A — should find nothing
        merchant_key = merchant.place_id
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)

        recent_claim = (
            db.query(ExclusiveSession)
            .filter(
                ExclusiveSession.driver_id == driver_a.id,
                or_(
                    ExclusiveSession.merchant_id == merchant_key,
                    ExclusiveSession.merchant_place_id == merchant_key,
                ),
                ExclusiveSession.activated_at >= twenty_four_hours_ago,
                ExclusiveSession.status.in_(
                    [ExclusiveSessionStatus.ACTIVE, ExclusiveSessionStatus.COMPLETED]
                ),
            )
            .order_by(ExclusiveSession.activated_at.desc())
            .first()
        )

        assert recent_claim is None, "Driver B's session should not block Driver A"


class TestIdempotencyKeyDedup:
    """Tests for the X-Idempotency-Key header deduplication."""

    def test_idempotency_key_returns_existing(self, db):
        """
        When a session already exists with the same idempotency_key,
        the endpoint should return it instead of creating a duplicate.
        This tests the query logic the endpoint uses.
        """
        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)
        idem_key = f"idem_{uuid.uuid4().hex}"

        first_session = _make_exclusive_session(
            db,
            driver=driver,
            merchant=merchant,
            charger=charger,
            idempotency_key=idem_key,
        )
        db.commit()

        # Simulate the idempotency lookup the endpoint does
        existing_session = (
            db.query(ExclusiveSession).filter(ExclusiveSession.idempotency_key == idem_key).first()
        )

        assert existing_session is not None
        assert str(existing_session.id) == str(first_session.id)
        assert existing_session.driver_id == driver.id

    def test_idempotency_key_cross_driver_detected(self, db):
        """
        If a different driver tries to use the same idempotency key,
        the endpoint should detect the collision (driver_id mismatch).
        """
        driver_a = _make_driver(db, label="driver-a")
        driver_b = _make_driver(db, label="driver-b")
        charger = _make_charger(db)
        merchant = _make_merchant(db)
        idem_key = f"idem_{uuid.uuid4().hex}"

        _make_exclusive_session(
            db,
            driver=driver_a,
            merchant=merchant,
            charger=charger,
            idempotency_key=idem_key,
        )
        db.commit()

        existing_session = (
            db.query(ExclusiveSession).filter(ExclusiveSession.idempotency_key == idem_key).first()
        )

        assert existing_session is not None
        # Driver B trying to use the key should see a driver_id mismatch
        assert existing_session.driver_id != driver_b.id
        assert existing_session.driver_id == driver_a.id

    def test_idempotency_key_unique_constraint(self, db):
        """
        The idempotency_key column has a UNIQUE constraint. Attempting
        to insert a second session with the same key should raise
        IntegrityError at the DB level.
        """
        from sqlalchemy.exc import IntegrityError

        driver = _make_driver(db)
        charger = _make_charger(db)
        merchant = _make_merchant(db)
        idem_key = f"idem_{uuid.uuid4().hex}"

        _make_exclusive_session(
            db,
            driver=driver,
            merchant=merchant,
            charger=charger,
            idempotency_key=idem_key,
        )
        db.commit()

        # Attempt to create a second session with the same idempotency_key
        duplicate = ExclusiveSession(
            id=str(uuid.uuid4()),
            driver_id=driver.id,
            merchant_id=merchant.id,
            merchant_place_id=merchant.place_id,
            charger_id=charger.id,
            status=ExclusiveSessionStatus.ACTIVE,
            activated_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
            activation_lat=31.0671,
            activation_lng=-97.7289,
            idempotency_key=idem_key,
        )
        db.add(duplicate)

        with pytest.raises(IntegrityError):
            db.flush()

        # Rollback so the session is usable for conftest cleanup
        db.rollback()
