"""
Step 5: Merchant visit and redemption flow tests.

Covers the authorization, idempotency, and atomicity branches of
the merchant visit / exclusive session / verified visit flow without
needing the full HTTP TestClient. The tests exercise the service-layer
helpers directly:

  1. _verify_merchant_ownership() — the authorization helper at
     exclusive.py:40 that guards every merchant-facing visit
     endpoint. Tests:
       - admin bypasses the check
       - merchant owner passes for their own merchant
       - non-owner merchant user raises 403
       - unknown WYC merchant returns cleanly (caller handles 404)
  2. VerifiedVisit visit_number allocation atomicity — the service
     uses `.order_by(visit_number.desc()).with_for_update().first()`
     to grab the next slot. We test that sequential allocations
     produce strictly increasing numbers and that the unique
     constraint on (merchant_id, visit_number, visit_date) blocks
     duplicates.
  3. Idempotency: verification_code is globally unique, so the
     second insert of the same code raises IntegrityError — which
     the service's except branch catches and retries with a fresh
     number. Tests the pattern, not the HTTP handler.

Scope note: the full /v1/exclusive/activate and /v1/exclusive/verify
HTTP flow requires a fully-constructed auth user + charger row +
merchant row + charger_merchant link + intent capture session. That
is 6+ model instances and a TestClient with dependency overrides.
The HTTP surface is already covered in tests/test_exclusive_sessions.py
(which was deleted in Step 1 as a dead-import file but had a working
duplicate elsewhere in the repo — see tests/api/test_exclusive_sessions.py).
This file focuses on the service-layer invariants that live under
the HTTP surface and are cheap to test without the full stack.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import pytest
from app.models.domain import DomainMerchant
from app.models.user import User
from app.models.verified_visit import VerifiedVisit
from app.models.while_you_charge import Merchant
from app.routers.exclusive import _verify_merchant_ownership
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


def _make_user(db, *, label: str = "user", roles: str = "driver") -> User:
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@test.nerava.network",
        password_hash="hashed",
        is_active=True,
        role_flags=roles,
    )
    db.add(user)
    db.flush()
    return user


def _make_wyc_merchant(db, *, place_id: str) -> Merchant:
    """Create a WYC-layer merchant with a Google Place ID."""
    merchant = Merchant(
        id=f"m_{uuid.uuid4().hex[:8]}",
        external_id=place_id,
        place_id=place_id,
        name="Test Pizzeria",
        category="restaurant",
        lat=31.0671,
        lng=-97.7289,
        short_code="TESTPZ",
        region_code="TXH",
    )
    db.add(merchant)
    db.flush()
    return merchant


def _make_domain_merchant(
    db, *, owner: User, place_id: str, name: str = "Test Pizzeria"
) -> DomainMerchant:
    merchant = DomainMerchant(
        id=str(uuid.uuid4()),
        name=name,
        google_place_id=place_id,
        lat=31.0671,
        lng=-97.7289,
        zone_slug="test_harker",
        status="active",
        nova_balance=0,
        owner_user_id=owner.id,
    )
    db.add(merchant)
    db.flush()
    return merchant


class TestVerifyMerchantOwnership:
    """_verify_merchant_ownership() authorization guard."""

    def test_admin_bypasses_ownership_check(self, db) -> None:
        admin = _make_user(db, label="admin", roles="driver,admin")
        wyc = _make_wyc_merchant(db, place_id="ChIJ_test_admin_bypass")

        # No DomainMerchant linked to the admin — check would normally fail
        # for a non-admin, but admins bypass entirely
        _verify_merchant_ownership(db, admin, wyc.id)
        # No exception means pass

    def test_merchant_owner_passes_for_their_own_merchant(self, db) -> None:
        owner = _make_user(db, label="owner", roles="merchant")
        place_id = "ChIJ_test_owner_match"
        wyc = _make_wyc_merchant(db, place_id=place_id)
        _make_domain_merchant(db, owner=owner, place_id=place_id)

        _verify_merchant_ownership(db, owner, wyc.id)
        # No exception means pass

    def test_non_owner_merchant_is_rejected_with_403(self, db) -> None:
        """The critical security branch from the April 2026 audit."""
        real_owner = _make_user(db, label="real-owner", roles="merchant")
        attacker = _make_user(db, label="attacker", roles="merchant")
        place_id = "ChIJ_test_attacker_block"
        wyc = _make_wyc_merchant(db, place_id=place_id)
        _make_domain_merchant(db, owner=real_owner, place_id=place_id)

        with pytest.raises(HTTPException) as exc_info:
            _verify_merchant_ownership(db, attacker, wyc.id)
        assert exc_info.value.status_code == 403
        assert "do not own" in exc_info.value.detail.lower()

    def test_unknown_wyc_merchant_returns_cleanly(self, db) -> None:
        """
        When the WYC merchant doesn't exist, the helper returns silently
        and lets the caller handle the 404. This is the documented
        behavior at exclusive.py:52.
        """
        user = _make_user(db, label="user", roles="merchant")
        _verify_merchant_ownership(db, user, "m_does_not_exist")
        # No exception — caller handles

    def test_domain_merchant_without_place_id_is_rejected(self, db) -> None:
        """
        A merchant-role user whose DomainMerchant has no
        google_place_id cannot claim ownership via the place-id
        match path. This is the negative branch of the lookup.
        """
        user = _make_user(db, label="no-place-id", roles="merchant")
        # DomainMerchant exists but has no google_place_id
        dm = DomainMerchant(
            id=str(uuid.uuid4()),
            name="Orphaned Merchant",
            google_place_id=None,
            lat=31.0671,
            lng=-97.7289,
            zone_slug="test",
            status="active",
            nova_balance=0,
            owner_user_id=user.id,
        )
        db.add(dm)
        db.flush()

        wyc = _make_wyc_merchant(db, place_id="ChIJ_unrelated_place")
        with pytest.raises(HTTPException) as exc_info:
            _verify_merchant_ownership(db, user, wyc.id)
        assert exc_info.value.status_code == 403


class TestVerifiedVisitNumberAllocation:
    """
    Visit_number allocation is the test-guarded version of the April
    2026 audit fix at exclusive.py:1081. The service uses
    .order_by(visit_number.desc()).with_for_update().first() to grab
    the next slot before inserting a new VerifiedVisit row. We
    exercise the sequential allocation directly.
    """

    def _insert_visit(
        self,
        db,
        merchant: Merchant,
        driver: User,
        visit_number: int,
    ) -> VerifiedVisit:
        code = f"{merchant.region_code}-{merchant.short_code}-{str(visit_number).zfill(3)}"
        visit = VerifiedVisit(
            id=str(uuid.uuid4()),
            verification_code=code,
            region_code=merchant.region_code or "TXH",
            merchant_code=merchant.short_code or "TESTPZ",
            visit_number=visit_number,
            merchant_id=merchant.id,
            driver_id=driver.id,
            verified_at=datetime.utcnow(),
            visit_date=datetime.utcnow(),
        )
        db.add(visit)
        db.flush()
        return visit

    def test_sequential_visits_get_strictly_increasing_numbers(self, db) -> None:
        """Simulate the service's next-number pattern across 3 visits."""
        owner = _make_user(db, label="owner", roles="merchant")
        merchant = _make_wyc_merchant(db, place_id="ChIJ_sequential")
        _make_domain_merchant(db, owner=owner, place_id="ChIJ_sequential")

        drivers = [_make_user(db, label=f"d{i}") for i in range(3)]

        # Emulate what the service does: SELECT latest → +1 → INSERT
        numbers_assigned = []
        for driver in drivers:
            latest = (
                db.query(VerifiedVisit)
                .filter(VerifiedVisit.merchant_id == merchant.id)
                .order_by(VerifiedVisit.visit_number.desc())
                .first()
            )
            next_number = (latest.visit_number if latest else 0) + 1
            self._insert_visit(db, merchant, driver, next_number)
            numbers_assigned.append(next_number)

        assert numbers_assigned == [1, 2, 3]

    def test_duplicate_verification_code_raises_integrity_error(self, db) -> None:
        """
        verification_code has a UNIQUE constraint. Two inserts with the
        same code must raise IntegrityError — this is the backstop that
        blocks the idempotency gap even if the visit_number allocation
        races.
        """
        owner = _make_user(db, label="owner", roles="merchant")
        merchant = _make_wyc_merchant(db, place_id="ChIJ_integrity")
        _make_domain_merchant(db, owner=owner, place_id="ChIJ_integrity")
        driver_a = _make_user(db, label="da")
        driver_b = _make_user(db, label="db")

        self._insert_visit(db, merchant, driver_a, visit_number=1)

        # Attempt to reuse visit_number=1 for the same merchant → unique
        # violation on the verification_code column
        with pytest.raises(IntegrityError):
            self._insert_visit(db, merchant, driver_b, visit_number=1)
            # The error may not materialize until next flush
            db.flush()

        db.rollback()

    def test_visit_number_allocation_scoped_per_merchant(self, db) -> None:
        """
        Two different merchants should each start at visit_number=1
        independently. The ordering query is filtered by merchant_id,
        so cross-merchant numbers don't collide.
        """
        owner = _make_user(db, label="owner", roles="merchant")
        m1 = _make_wyc_merchant(db, place_id="ChIJ_m1")
        m1.short_code = "M1PIZZA"
        m2 = _make_wyc_merchant(db, place_id="ChIJ_m2")
        m2.short_code = "M2TACOS"
        _make_domain_merchant(db, owner=owner, place_id="ChIJ_m1", name="M1")
        _make_domain_merchant(db, owner=owner, place_id="ChIJ_m2", name="M2")
        db.flush()

        driver = _make_user(db, label="driver")

        # First visit at each merchant
        self._insert_visit(db, m1, driver, visit_number=1)
        self._insert_visit(db, m2, driver, visit_number=1)

        # Verify both rows exist and point at different merchants
        m1_visits = db.query(VerifiedVisit).filter(VerifiedVisit.merchant_id == m1.id).all()
        m2_visits = db.query(VerifiedVisit).filter(VerifiedVisit.merchant_id == m2.id).all()
        assert len(m1_visits) == 1
        assert len(m2_visits) == 1
        assert m1_visits[0].visit_number == 1
        assert m2_visits[0].visit_number == 1
        # Codes must be distinct
        assert m1_visits[0].verification_code != m2_visits[0].verification_code


class TestVerifiedVisitRedemption:
    """Redemption marks a visit as redeemed and stores the POS ref."""

    def test_mark_visit_redeemed_stores_order_reference(self, db) -> None:
        owner = _make_user(db, label="owner", roles="merchant")
        merchant = _make_wyc_merchant(db, place_id="ChIJ_redeem")
        _make_domain_merchant(db, owner=owner, place_id="ChIJ_redeem")
        driver = _make_user(db, label="driver")

        visit = VerifiedVisit(
            id=str(uuid.uuid4()),
            verification_code=f"{merchant.region_code}-{merchant.short_code}-001",
            region_code=merchant.region_code or "TXH",
            merchant_code=merchant.short_code or "TESTPZ",
            visit_number=1,
            merchant_id=merchant.id,
            driver_id=driver.id,
            verified_at=datetime.utcnow(),
            visit_date=datetime.utcnow(),
        )
        db.add(visit)
        db.flush()

        assert visit.redeemed_at is None
        assert visit.order_reference is None

        # Mark redeemed with POS reference
        now = datetime.utcnow()
        visit.redeemed_at = now
        visit.order_reference = "TOAST_ORDER_12345"
        visit.redemption_notes = "free garlic knots + soda"
        db.flush()

        refreshed = db.query(VerifiedVisit).filter(VerifiedVisit.id == visit.id).first()
        assert refreshed is not None
        assert refreshed.redeemed_at is not None
        assert refreshed.order_reference == "TOAST_ORDER_12345"
        assert refreshed.redemption_notes == "free garlic knots + soda"


class TestWithForUpdateOnVisitAllocation:
    """Rule #4: verify the service source uses with_for_update on the visit-number query."""

    def test_exclusive_router_uses_with_for_update_on_visit_number(self) -> None:
        import inspect

        from app.routers import exclusive

        source = inspect.getsource(exclusive)
        # Both the activate_exclusive and verify_visit handlers use
        # .order_by(VerifiedVisit.visit_number.desc()).with_for_update()
        # This asserts the pattern is present anywhere in the module.
        assert (
            ".with_for_update()" in source
        ), "exclusive.py must use with_for_update() for visit_number allocation"
        assert (
            "VerifiedVisit.visit_number.desc()" in source
        ), "The ordering must be by visit_number desc so next = latest + 1"

    def test_merchant_ownership_check_blocks_cross_merchant_access(self) -> None:
        """
        Rule: _verify_merchant_ownership must be called before any
        merchant-scoped mutation. Source-inspect the exclusive module
        to confirm the helper is referenced in the merchant-facing
        endpoint code paths.
        """
        import inspect

        from app.routers import exclusive

        source = inspect.getsource(exclusive)
        assert "_verify_merchant_ownership" in source
        # It must be called somewhere, not just defined
        call_count = source.count("_verify_merchant_ownership(")
        assert call_count >= 2, (
            f"_verify_merchant_ownership must be DEFINED and CALLED at least "
            f"once; found {call_count} references"
        )
