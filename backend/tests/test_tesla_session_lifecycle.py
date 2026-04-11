"""
Step 2: Tesla session lifecycle end-to-end test.

Exercises the complete charging flow as a single sequence:
  1. MockTeslaFleetAPIClient places a vehicle at a charger geofence
  2. Session detected and created via SessionEventService.create_from_tesla()
  3. Session telemetry updates mid-flow (kWh, battery, power)
  4. Session ends via SessionEventService.end_session()
  5. IncentiveEngine.evaluate_session() matches against an active campaign
  6. Wallet credited via the real code path (with_for_update branch)
  7. Final wallet balance matches the grant amount

This flow is tested in fragments across test_session_event_service.py,
test_incentive_engine.py, test_charge_context.py, and
test_financial_flows.py but no existing test exercises the full loop
without mocking the downstream services. This file is self-contained
and does NOT depend on any pre-existing broken fixtures.

NOTE: row-lock contention tests require PostgreSQL. These tests use
SQLite and cannot verify with_for_update() blocking behavior — the
lock is a no-op on SQLite. A follow-up PR will add a Postgres test DB
fixture (testcontainers-python or docker-compose) so the row-lock
branches can be exercised end-to-end. The tests below still verify
the full non-concurrent path and the correctness of the service
contract.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest

# Enforce mock modes BEFORE any app imports so no real Tesla / Stripe
# client is ever constructed.
os.environ.setdefault("TESLA_MOCK_MODE", "true")
os.environ.setdefault("STRIPE_SECRET_KEY", "")

from app.models.campaign import Campaign  # noqa: E402
from app.models.driver_wallet import DriverWallet, WalletLedger  # noqa: E402
from app.models.session_event import IncentiveGrant, SessionEvent  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.incentive_engine import IncentiveEngine  # noqa: E402
from app.services.mock_tesla_fleet_api import (  # noqa: E402
    MockTeslaFleetAPIClient,
    reset_mock_state,
)
from app.services.session_event_service import SessionEventService  # noqa: E402

logger = logging.getLogger(__name__)

# Harker Heights Supercharger (real site — matches the Tesla report data)
HARKER_HEIGHTS_LAT = 31.0671
HARKER_HEIGHTS_LNG = -97.7289


@pytest.fixture
def driver(db) -> User:
    """Create a driver user with no existing wallet."""
    user = User(
        email=f"tesla-lifecycle-{uuid.uuid4().hex[:8]}@test.nerava.network",
        password_hash="hashed",
        is_active=True,
        role_flags="driver",
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def active_campaign(db, driver) -> Campaign:
    """
    Active Tesla campaign that accepts any Tesla session over 15 min.
    Budget is $50, reward is $5/session → exactly 10 sessions before
    the budget exhausts. We only use one session per test so that is
    plenty of headroom.
    """
    campaign = Campaign(
        id=str(uuid.uuid4()),
        sponsor_name="Test Tesla Lifecycle Sponsor",
        sponsor_type="charging_network",
        name="Tesla Lifecycle Test Campaign",
        campaign_type="custom",
        status="active",
        priority=10,
        budget_cents=5000,
        spent_cents=0,
        cost_per_session_cents=500,
        sessions_granted=0,
        start_date=datetime.utcnow() - timedelta(days=1),
        end_date=datetime.utcnow() + timedelta(days=30),
        rule_min_duration_minutes=15,
        rule_charger_networks=["Tesla"],
        created_by_user_id=driver.id,
        funding_status="funded",
    )
    db.add(campaign)
    db.flush()
    return campaign


@pytest.fixture
def mock_tesla_client() -> MockTeslaFleetAPIClient:
    """
    Fresh MockTeslaFleetAPIClient with a clean singleton state per test.
    Resetting the module-level state ensures tests don't leak state
    between runs — important because the mock uses a global cache.
    """
    reset_mock_state()
    client = MockTeslaFleetAPIClient()
    return client


def _vehicle_info_from_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the vehicle_info dict expected by create_from_tesla()."""
    response = data["response"]
    return {
        "id": response["id"],
        "vin": response["vin"],
        "display_name": response["display_name"],
    }


def _charge_data_from_data(data: Dict[str, Any], lat: float, lng: float) -> Dict[str, Any]:
    """
    Build the charge_data dict in the shape create_from_tesla() expects.

    create_from_tesla() reads battery_level, charging_state, charger_power,
    charge_energy_added, fast_charger_type, lat, lng from a flat dict. The
    MockTeslaFleetAPIClient returns a nested Tesla-shaped response, so we
    flatten it here.
    """
    charge_state = data["response"]["charge_state"]
    return {
        "battery_level": charge_state["battery_level"],
        "charging_state": charge_state["charging_state"],
        "charger_power": 150.0,
        "charge_energy_added": 12.5,
        "fast_charger_type": "Tesla",
        "lat": lat,
        "lng": lng,
    }


class TestTeslaSessionLifecycle:
    """End-to-end Tesla charging session → grant → wallet credit."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_creates_session_grant_and_credits_wallet(
        self,
        db,
        driver: User,
        active_campaign: Campaign,
        mock_tesla_client: MockTeslaFleetAPIClient,
    ) -> None:
        """
        Drives the full lifecycle from "vehicle arrives at charger" to
        "driver wallet balance reflects the grant" with zero mocks
        between the Tesla layer and the wallet layer.
        """
        # ── 1. Vehicle arrives at Harker Heights Supercharger ────────
        vehicle_id = "MOCK_VEHICLE_001"
        mock_tesla_client.set_vehicle_location(vehicle_id, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG)
        mock_tesla_client.set_vehicle_battery(
            vehicle_id, battery_level=45, charging_state="Charging"
        )
        await mock_tesla_client.simulate_vehicle_arrival(
            vehicle_id,
            HARKER_HEIGHTS_LAT,
            HARKER_HEIGHTS_LNG,
            callback_url="http://localhost:8000/mock-tesla/callback",
        )

        # ── 2. Session detected and created ──────────────────────────
        vehicle_data = await mock_tesla_client.get_vehicle_data(vehicle_id, "mock_token")
        vehicle_info = _vehicle_info_from_data(vehicle_data)
        charge_data = _charge_data_from_data(vehicle_data, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG)

        session = SessionEventService.create_from_tesla(
            db=db,
            driver_id=driver.id,
            charge_data=charge_data,
            vehicle_info=vehicle_info,
            charger_id="tesla_sc_harker_heights_tx",
            charger_network="Tesla",
        )
        db.flush()

        assert session.id is not None
        assert session.driver_user_id == driver.id
        assert session.charger_id == "tesla_sc_harker_heights_tx"
        assert session.charger_network == "Tesla"
        assert session.session_end is None, "Session should be open mid-flow"
        assert session.battery_start_pct == 45

        # ── 3. Telemetry mid-session: battery climbs, kWh accumulates ─
        mock_tesla_client.set_vehicle_battery(
            vehicle_id, battery_level=72, charging_state="Charging"
        )
        updated_vehicle_data = await mock_tesla_client.get_vehicle_data(vehicle_id, "mock_token")
        updated_charge_data = _charge_data_from_data(
            updated_vehicle_data, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG
        )
        updated_charge_data["charge_energy_added"] = 28.4

        _refreshed = SessionEventService.create_from_tesla(
            db=db,
            driver_id=driver.id,
            charge_data=updated_charge_data,
            vehicle_info=vehicle_info,
            charger_id="tesla_sc_harker_heights_tx",
            charger_network="Tesla",
        )
        db.flush()

        # create_from_tesla() should have found the existing active
        # session and updated its telemetry in-place rather than
        # creating a second row.
        assert _refreshed.id == session.id
        assert _refreshed.battery_end_pct == 72
        assert _refreshed.kwh_delivered == 28.4

        # Sanity: only one session row for this driver/vehicle
        session_count = (
            db.query(SessionEvent).filter(SessionEvent.driver_user_id == driver.id).count()
        )
        assert session_count == 1, "Telemetry update must not create duplicate sessions"

        # ── 4. Backdate session_start so duration is over the 15-min floor ─
        # In real production Tesla polls span tens of minutes of wall
        # time. The test runs in milliseconds, so we reach back and
        # pretend the session started 45 minutes ago before ending it.
        # This is the minimum amount of test magic required to exercise
        # the incentive-matching path — the service code is unchanged.
        session.session_start = datetime.utcnow() - timedelta(minutes=45)
        db.flush()

        # ── 5. Session ends ──────────────────────────────────────────
        mock_tesla_client.set_vehicle_battery(
            vehicle_id, battery_level=80, charging_state="Disconnected"
        )
        ended = SessionEventService.end_session(
            db=db,
            session_event_id=session.id,
            ended_reason="unplugged",
            battery_end_pct=80,
            kwh_delivered=32.7,
        )
        db.flush()

        assert ended is not None
        assert ended.session_end is not None
        assert ended.duration_minutes is not None
        assert ended.duration_minutes >= 15, (
            f"Session duration {ended.duration_minutes}min must be over "
            f"the 15-min incentive floor for the match to fire"
        )
        assert ended.ended_reason == "unplugged"

        # ── 6. IncentiveEngine matches and creates grant ─────────────
        grant = IncentiveEngine.evaluate_session(db, ended)
        db.flush()

        assert grant is not None, (
            "Incentive engine should have created a grant for a matching Tesla "
            "session against an active Tesla campaign"
        )
        assert grant.campaign_id == active_campaign.id
        assert grant.session_event_id == session.id
        assert grant.driver_user_id == driver.id
        assert grant.amount_cents == active_campaign.cost_per_session_cents
        assert grant.status == "granted"
        assert grant.reward_destination == "nerava_wallet"

        # ── 7. Wallet is credited with the correct amount ────────────
        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver.id).first()
        assert wallet is not None, "Wallet should have been auto-created by grant path"
        assert wallet.balance_cents == active_campaign.cost_per_session_cents, (
            f"Wallet balance {wallet.balance_cents}c must equal the grant "
            f"amount {active_campaign.cost_per_session_cents}c"
        )
        assert wallet.total_earned_cents == active_campaign.cost_per_session_cents

        # ── 8. Ledger entry exists and matches the grant ─────────────
        ledger_entries = (
            db.query(WalletLedger)
            .filter(WalletLedger.driver_id == driver.id)
            .filter(WalletLedger.reference_id == grant.id)
            .all()
        )
        assert (
            len(ledger_entries) == 1
        ), "Exactly one ledger entry should be created per grant (double-entry discipline)"
        ledger = ledger_entries[0]
        assert ledger.amount_cents == active_campaign.cost_per_session_cents
        assert ledger.transaction_type == "credit"
        assert ledger.reference_type == "campaign_grant"
        assert ledger.balance_after_cents == wallet.balance_cents

        # ── 9. Campaign spent_cents reflects the grant ───────────────
        refreshed_campaign = db.query(Campaign).filter(Campaign.id == active_campaign.id).first()
        assert refreshed_campaign is not None
        assert refreshed_campaign.spent_cents == active_campaign.cost_per_session_cents
        assert refreshed_campaign.sessions_granted == 1

    @pytest.mark.asyncio
    async def test_session_under_minimum_duration_gets_no_grant(
        self,
        db,
        driver: User,
        active_campaign: Campaign,
        mock_tesla_client: MockTeslaFleetAPIClient,
    ) -> None:
        """A 5-minute session must NOT match the 15-minute campaign."""
        vehicle_id = "MOCK_VEHICLE_001"
        mock_tesla_client.set_vehicle_location(vehicle_id, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG)
        vehicle_data = await mock_tesla_client.get_vehicle_data(vehicle_id, "mock_token")
        session = SessionEventService.create_from_tesla(
            db=db,
            driver_id=driver.id,
            charge_data=_charge_data_from_data(
                vehicle_data, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG
            ),
            vehicle_info=_vehicle_info_from_data(vehicle_data),
            charger_id="tesla_sc_harker_heights_tx",
            charger_network="Tesla",
        )
        db.flush()

        # Only 5 minutes in the past — below the 15-min campaign floor
        session.session_start = datetime.utcnow() - timedelta(minutes=5)
        db.flush()

        ended = SessionEventService.end_session(
            db=db,
            session_event_id=session.id,
            ended_reason="unplugged",
        )
        db.flush()

        assert ended is not None
        assert ended.duration_minutes is not None
        assert ended.duration_minutes < 15

        grant = IncentiveEngine.evaluate_session(db, ended)
        assert grant is None, "Short session must not produce a grant"

        # Campaign budget must NOT have been touched
        refreshed = db.query(Campaign).filter(Campaign.id == active_campaign.id).first()
        assert refreshed is not None
        assert refreshed.spent_cents == 0
        assert refreshed.sessions_granted == 0

    @pytest.mark.asyncio
    async def test_non_tesla_session_does_not_match_tesla_campaign(
        self,
        db,
        driver: User,
        active_campaign: Campaign,
        mock_tesla_client: MockTeslaFleetAPIClient,
    ) -> None:
        """A ChargePoint session must not match the Tesla-only campaign."""
        session = SessionEvent(
            id=str(uuid.uuid4()),
            driver_user_id=driver.id,
            user_id=driver.id,
            charger_id="cp_some_station",
            charger_network="ChargePoint",
            connector_type="CCS",
            power_kw=50.0,
            session_start=datetime.utcnow() - timedelta(minutes=45),
            session_end=datetime.utcnow(),
            duration_minutes=45,
            source="partner_cp",
            source_session_id=f"cp_{uuid.uuid4()}",
            verified=True,
            lat=HARKER_HEIGHTS_LAT,
            lng=HARKER_HEIGHTS_LNG,
        )
        db.add(session)
        db.flush()

        grant = IncentiveEngine.evaluate_session(db, session)
        assert grant is None, (
            "ChargePoint session must not match campaign with " "rule_charger_networks=['Tesla']"
        )

    @pytest.mark.asyncio
    async def test_second_evaluation_is_idempotent(
        self,
        db,
        driver: User,
        active_campaign: Campaign,
        mock_tesla_client: MockTeslaFleetAPIClient,
    ) -> None:
        """
        Calling evaluate_session() twice on the same session must NOT
        create a second grant or credit the wallet twice. The
        IncentiveEngine enforces one-grant-per-session via the
        (session_event_id) uniqueness branch.
        """
        vehicle_id = "MOCK_VEHICLE_001"
        vehicle_data = await mock_tesla_client.get_vehicle_data(vehicle_id, "mock_token")
        session = SessionEventService.create_from_tesla(
            db=db,
            driver_id=driver.id,
            charge_data=_charge_data_from_data(
                vehicle_data, HARKER_HEIGHTS_LAT, HARKER_HEIGHTS_LNG
            ),
            vehicle_info=_vehicle_info_from_data(vehicle_data),
            charger_id="tesla_sc_harker_heights_tx",
            charger_network="Tesla",
        )
        db.flush()

        session.session_start = datetime.utcnow() - timedelta(minutes=45)
        db.flush()
        SessionEventService.end_session(
            db=db,
            session_event_id=session.id,
            ended_reason="unplugged",
        )
        db.flush()

        first = IncentiveEngine.evaluate_session(db, session)
        db.flush()
        assert first is not None

        second = IncentiveEngine.evaluate_session(db, session)
        db.flush()
        assert second is not None
        assert (
            second.id == first.id
        ), "Second evaluation must return the existing grant, not create a new one"

        # Only one grant in the DB for this session
        grant_count = (
            db.query(IncentiveGrant).filter(IncentiveGrant.session_event_id == session.id).count()
        )
        assert grant_count == 1

        # Wallet must only have been credited once
        wallet = db.query(DriverWallet).filter(DriverWallet.driver_id == driver.id).first()
        assert wallet is not None
        assert (
            wallet.balance_cents == active_campaign.cost_per_session_cents
        ), "Wallet credited exactly once despite two evaluate_session() calls"


class TestWithForUpdateIsInExecutionPath:
    """
    Rule #4: Any wallet mutation test must verify that with_for_update()
    is in the execution path. On SQLite FOR UPDATE is a no-op, so we
    verify the CODE PATH by reading the service source and asserting
    that the critical mutation functions use the locked-select pattern.
    """

    def test_incentive_engine_uses_with_for_update_for_wallet(self) -> None:
        """Grant creation must row-lock the wallet before crediting."""
        import inspect

        from app.services import incentive_engine

        source = inspect.getsource(incentive_engine)
        assert ".with_for_update()" in source, (
            "IncentiveEngine source must use with_for_update() to prevent "
            "concurrent grant races from lost wallet updates"
        )
        # Locate the wallet-credit block specifically
        assert "DriverWallet.driver_id == session.driver_user_id" in source
        # And confirm the lock is applied on the wallet filter, not somewhere irrelevant
        wallet_lock_region = source[
            source.index("DriverWallet.driver_id == session.driver_user_id") :
        ]
        wallet_lock_region = wallet_lock_region[
            : wallet_lock_region.index("wallet.balance_cents +=")
        ]
        assert (
            ".with_for_update()" in wallet_lock_region
        ), "The lock must sit between the wallet SELECT and the balance mutation"

    def test_campaign_service_decrement_uses_with_for_update(self) -> None:
        """Budget decrement must row-lock the campaign before mutating."""
        import inspect

        from app.services import campaign_service

        source = inspect.getsource(campaign_service.CampaignService.decrement_budget_atomic)
        assert ".with_for_update()" in source
        # The lock is applied before mutating spent_cents
        lock_idx = source.index(".with_for_update()")
        mutate_idx = source.index("campaign.spent_cents +=")
        assert lock_idx < mutate_idx, (
            "with_for_update() must come before the spent_cents mutation, "
            "otherwise the lock is pointless"
        )
