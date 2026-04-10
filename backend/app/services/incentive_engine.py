"""
Incentive Engine — Rules evaluation for session-to-campaign matching.

Key design decisions per review:
- Evaluate on session END only (called after SessionEventService.end_session)
- One session = one grant max (highest priority campaign wins, no stacking)
- Budget decrement is atomic (prevents overruns)
- Driver caps enforced before grant
- Minimum duration is mandatory for every campaign
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.session_event import IncentiveGrant, SessionEvent
from app.services.campaign_service import CampaignService
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)


class IncentiveEngine:
    """Evaluates completed sessions against active campaigns."""

    @staticmethod
    def evaluate_session(db: Session, session: SessionEvent) -> Optional[IncentiveGrant]:
        """
        Evaluate a completed session against all active campaigns.
        Returns the grant if one was created, else None.

        One session = one grant. Highest priority campaign wins.
        Campaigns are pre-sorted by priority (ascending = higher priority).
        """
        if session.session_end is None:
            logger.debug(f"Session {session.id} not ended yet, skipping evaluation")
            return None

        if not session.duration_minutes or session.duration_minutes < 1:
            logger.debug(f"Session {session.id} too short ({session.duration_minutes}min)")
            return None

        # Check if session already has a grant (idempotency)
        existing = (
            db.query(IncentiveGrant).filter(IncentiveGrant.session_event_id == session.id).first()
        )
        if existing:
            logger.debug(f"Session {session.id} already has grant {existing.id}")
            return existing

        # Get active campaigns sorted by priority
        campaigns = CampaignService.get_active_campaigns(db)
        if not campaigns:
            logger.info(f"No active campaigns found for session {session.id}")
            return None

        logger.info(
            f"Evaluating session {session.id} ({session.duration_minutes}min, "
            f"charger={session.charger_id}, network={session.charger_network}) "
            f"against {len(campaigns)} active campaigns"
        )

        for campaign in campaigns:
            matched = IncentiveEngine._session_matches_campaign(db, session, campaign)
            if not matched:
                logger.info(
                    f"Session {session.id} did NOT match campaign '{campaign.name}' "
                    f"(id={campaign.id}, min_dur={campaign.rule_min_duration_minutes}min)"
                )
            else:
                grant = IncentiveEngine._create_grant(db, session, campaign)
                if grant:
                    return grant

        return None

    @staticmethod
    def _session_matches_campaign(
        db: Session,
        session: SessionEvent,
        campaign: Campaign,
    ) -> bool:
        """
        Check if a session matches ALL rules of a campaign.
        All non-null rules are AND-ed.
        """

        def _reject(rule_name):
            logger.info(
                f"Campaign '{campaign.name}' rejected session {session.id}: "
                f"failed rule '{rule_name}'"
            )
            return False

        # --- Mandatory: minimum duration ---
        if session.duration_minutes < campaign.rule_min_duration_minutes:
            return _reject(
                f"min_duration ({session.duration_minutes}min < {campaign.rule_min_duration_minutes}min)"
            )

        # --- Optional max duration ---
        if (
            campaign.rule_max_duration_minutes
            and session.duration_minutes > campaign.rule_max_duration_minutes
        ):
            return _reject(
                f"max_duration ({session.duration_minutes}min > {campaign.rule_max_duration_minutes}min)"
            )

        # --- Charger IDs ---
        if campaign.rule_charger_ids:
            if session.charger_id not in campaign.rule_charger_ids:
                return _reject(
                    f"charger_ids ({session.charger_id} not in {campaign.rule_charger_ids})"
                )

        # --- Charger networks ---
        if campaign.rule_charger_networks:
            if session.charger_network not in campaign.rule_charger_networks:
                return _reject(
                    f"charger_networks ({session.charger_network} not in {campaign.rule_charger_networks})"
                )

        # --- Zone IDs ---
        if campaign.rule_zone_ids:
            if session.zone_id not in campaign.rule_zone_ids:
                return _reject("zone_ids")

        # --- Geo radius ---
        if (
            campaign.rule_geo_center_lat is not None
            and campaign.rule_geo_center_lng is not None
            and campaign.rule_geo_radius_m
        ):
            if session.lat is None or session.lng is None:
                return _reject("geo_radius (no session lat/lng)")
            dist = haversine_m(
                campaign.rule_geo_center_lat,
                campaign.rule_geo_center_lng,
                session.lat,
                session.lng,
            )
            if dist > campaign.rule_geo_radius_m:
                return _reject(f"geo_radius ({dist:.0f}m > {campaign.rule_geo_radius_m}m)")

        # --- Time of day ---
        if campaign.rule_time_start and campaign.rule_time_end:
            session_hour_min = session.session_start.strftime("%H:%M")
            if not IncentiveEngine._time_in_window(
                session_hour_min, campaign.rule_time_start, campaign.rule_time_end
            ):
                return _reject(
                    f"time_of_day ({session_hour_min} not in {campaign.rule_time_start}-{campaign.rule_time_end})"
                )

        # --- Day of week ---
        if campaign.rule_days_of_week:
            session_dow = session.session_start.isoweekday()  # 1=Mon, 7=Sun
            if session_dow not in campaign.rule_days_of_week:
                return _reject(f"day_of_week ({session_dow} not in {campaign.rule_days_of_week})")

        # --- Min power (DC fast only) ---
        if campaign.rule_min_power_kw:
            if session.power_kw is None or session.power_kw < campaign.rule_min_power_kw:
                return _reject(f"min_power ({session.power_kw}kW < {campaign.rule_min_power_kw}kW)")

        # --- Connector types ---
        if campaign.rule_connector_types:
            if session.connector_type not in campaign.rule_connector_types:
                return _reject(
                    f"connector_types ({session.connector_type} not in {campaign.rule_connector_types})"
                )

        # --- Driver session count (for new/repeat driver rules) ---
        if (
            campaign.rule_driver_session_count_min is not None
            or campaign.rule_driver_session_count_max is not None
        ):
            from app.services.session_event_service import SessionEventService

            count = SessionEventService.count_driver_sessions(db, session.driver_user_id)
            if campaign.rule_driver_session_count_min is not None:
                if count < campaign.rule_driver_session_count_min:
                    return _reject(
                        f"driver_session_count_min ({count} < {campaign.rule_driver_session_count_min})"
                    )
            if campaign.rule_driver_session_count_max is not None:
                if count > campaign.rule_driver_session_count_max:
                    return _reject(
                        f"driver_session_count_max ({count} > {campaign.rule_driver_session_count_max})"
                    )

        # --- Driver allowlist ---
        if campaign.rule_driver_allowlist:
            from app.models.user import User

            driver = db.query(User).filter(User.id == session.driver_user_id).first()
            if not driver:
                return False
            # Check email or user_id in allowlist
            driver_email = driver.email or ""
            driver_id_str = str(driver.id)
            if (
                driver_email not in campaign.rule_driver_allowlist
                and driver_id_str not in campaign.rule_driver_allowlist
            ):
                return False

        # --- Partner session controls ---
        if session.partner_id:
            if not getattr(campaign, "allow_partner_sessions", True):
                return False
            if campaign.rule_partner_ids:
                if session.partner_id not in campaign.rule_partner_ids:
                    return False
            if campaign.rule_min_trust_tier:
                from app.models.partner import Partner

                partner = db.query(Partner).filter(Partner.id == session.partner_id).first()
                if partner and partner.trust_tier > campaign.rule_min_trust_tier:
                    return False

        # --- Driver caps ---
        if not CampaignService.check_driver_caps(
            db, campaign, session.driver_user_id, session.charger_id
        ):
            return _reject("driver_caps")

        return True

    @staticmethod
    def _create_grant(
        db: Session,
        session: SessionEvent,
        campaign: Campaign,
    ) -> Optional[IncentiveGrant]:
        """
        Create an incentive grant and atomically decrement campaign budget.
        Also creates a Nova transaction for the driver.
        """
        amount = campaign.cost_per_session_cents
        idempotency_key = f"campaign_{campaign.id}_session_{session.id}"

        # Atomic budget decrement — returns False if insufficient
        if not CampaignService.decrement_budget_atomic(db, campaign.id, amount):
            logger.info(f"Campaign {campaign.id} budget insufficient for {amount}c")
            return None

        # Determine reward destination for partner sessions
        reward_dest = "nerava_wallet"
        if session.partner_id:
            from app.models.user import User

            driver = db.query(User).filter(User.id == session.driver_user_id).first()
            if driver and driver.auth_provider == "partner":
                reward_dest = "partner_managed"

        # Wrap all post-decrement operations in try/except so we can
        # restore the campaign budget if anything downstream fails.
        try:
            nova_tx = None
            if reward_dest == "nerava_wallet":
                # Create Nova transaction (atomic with grant) — only for Nerava wallet users
                from app.services.nova_service import NovaService

                nova_tx = NovaService.grant_to_driver(
                    db,
                    driver_id=session.driver_user_id,
                    amount=amount,
                    type="campaign_grant",
                    session_id=None,  # Don't pass session_events ID — FK points to legacy domain_charging_sessions table
                    metadata={
                        "source": "incentive_engine",
                        "campaign_id": str(campaign.id),
                        "campaign_name": campaign.name,
                        "charger_id": session.charger_id,
                        "duration_minutes": session.duration_minutes,
                    },
                    idempotency_key=idempotency_key,
                    auto_commit=False,
                )

            # Create incentive grant record
            grant = IncentiveGrant(
                id=str(uuid.uuid4()),
                session_event_id=session.id,
                campaign_id=campaign.id,
                driver_user_id=session.driver_user_id,
                amount_cents=amount,
                status="granted",
                reward_destination=reward_dest,
                nova_transaction_id=nova_tx.id if nova_tx else None,
                idempotency_key=idempotency_key,
                granted_at=datetime.utcnow(),
            )
            db.add(grant)
            db.flush()

            # Credit real USD to driver wallet — only for Nerava wallet users
            if reward_dest == "nerava_wallet":
                from app.models.driver_wallet import DriverWallet, WalletLedger

                wallet = (
                    db.query(DriverWallet)
                    .filter(DriverWallet.driver_id == session.driver_user_id)
                    .with_for_update()
                    .first()
                )
                if not wallet:
                    wallet = DriverWallet(
                        id=str(uuid.uuid4()),
                        driver_id=session.driver_user_id,
                        balance_cents=0,
                        pending_balance_cents=0,
                    )
                    db.add(wallet)
                    db.flush()

                wallet.balance_cents += amount
                wallet.total_earned_cents += amount
                wallet.updated_at = datetime.utcnow()

                ledger_entry = WalletLedger(
                    id=str(uuid.uuid4()),
                    wallet_id=wallet.id,
                    driver_id=session.driver_user_id,
                    amount_cents=amount,
                    balance_after_cents=wallet.balance_cents,
                    transaction_type="credit",
                    reference_type="campaign_grant",
                    reference_id=grant.id,
                    description=f"Earned from {campaign.name}",
                )
                db.add(ledger_entry)
                db.flush()

        except Exception as e:
            # Rollback the entire unit of work (grant + wallet + nova)
            # and restore the campaign budget that was already decremented.
            logger.error(
                f"Grant creation failed for campaign {campaign.id}, "
                f"session {session.id}, restoring budget: {e}"
            )
            db.rollback()
            # Restore budget: re-fetch campaign inside a new flush context
            cam = db.query(Campaign).filter(Campaign.id == campaign.id).with_for_update().first()
            if cam:
                cam.spent_cents = max(0, cam.spent_cents - amount)
                cam.sessions_granted = max(0, cam.sessions_granted - 1)
                cam.updated_at = datetime.utcnow()
                db.flush()
                try:
                    db.commit()
                except Exception as commit_err:
                    logger.error(
                        f"Failed to commit budget restore for campaign {campaign.id}: {commit_err}"
                    )
                    db.rollback()
            return None

        logger.info(
            f"Granted {amount}c from campaign '{campaign.name}' to driver {session.driver_user_id} "
            f"for session {session.id} ({session.duration_minutes}min)"
        )
        return grant

    @staticmethod
    def _time_in_window(time_str: str, start: str, end: str) -> bool:
        """
        Check if time_str (HH:MM) is within start-end window.
        Handles overnight windows (e.g., 22:00 → 06:00).
        """
        if start <= end:
            return start <= time_str <= end
        else:
            # Overnight window
            return time_str >= start or time_str <= end
# Audit: round 6 review trigger
