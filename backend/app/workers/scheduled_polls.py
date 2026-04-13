"""
Scheduled Poll Worker — Background verification polls for smart polling.

Checks for sessions where next_poll_at <= NOW() and session_end IS NULL.
For each due session, polls Tesla API to verify charging state, ends
sessions that are no longer charging, and reschedules if still charging.

This enables 2-poll-per-session detection:
  Poll #1: Background ping creates session on geofence entry
  Poll #2: This worker verifies session at campaign min_duration + buffer
"""

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

from app.db import SessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)


@contextmanager
def get_db_session():
    """Context manager for database sessions with rollback safety."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class ScheduledPollWorker:
    """
    Background worker that processes sessions due for verification poll.
    Runs every 120 seconds. Queries: next_poll_at <= NOW() AND session_end IS NULL.
    """

    def __init__(self, poll_interval: int = 120):
        self.poll_interval = poll_interval
        self.running = False
        self.task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the scheduled poll worker."""
        if self.running:
            logger.warning("ScheduledPollWorker is already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("ScheduledPollWorker started (interval=%ds)", self.poll_interval)

    async def stop(self):
        """Stop the scheduled poll worker."""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("ScheduledPollWorker stopped")

    async def _run(self):
        """Main worker loop."""
        while self.running:
            try:
                await self._process_due_sessions()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ScheduledPollWorker error: {e}")
                await asyncio.sleep(self.poll_interval)

    async def _process_due_sessions(self):
        """Find and process sessions that are due for a verification poll."""
        with get_db_session() as db:
            # Find sessions due for poll
            now = datetime.utcnow()
            rows = db.execute(
                text(
                    "SELECT id, driver_user_id "
                    "FROM session_events "
                    "WHERE next_poll_at IS NOT NULL "
                    "AND next_poll_at <= :now "
                    "AND session_end IS NULL "
                    "ORDER BY next_poll_at ASC "
                    "LIMIT 50"
                ),
                {"now": now},
            ).fetchall()

            if not rows:
                return

            logger.info(f"ScheduledPollWorker: {len(rows)} sessions due for verification")

            for row in rows:
                session_id = str(row[0])
                driver_id = int(row[1])
                try:
                    await self._verify_session(db, session_id, driver_id)
                except Exception as e:
                    logger.error(
                        f"Failed to verify session {session_id} for driver {driver_id}: {e}"
                    )
                    # Reschedule 3 minutes out on failure
                    db.execute(
                        text(
                            "UPDATE session_events SET next_poll_at = :next "
                            "WHERE id = :sid AND session_end IS NULL"
                        ),
                        {"next": now + timedelta(minutes=3), "sid": session_id},
                    )
                    db.commit()

    async def _verify_session(self, db, session_id: str, driver_id: int):
        """
        Poll Tesla API for a single session (poll #2).
        If still charging: update telemetry, reschedule +5 min.
        If not charging: end session, evaluate incentive, send push.
        """
        from app.models.session_event import SessionEvent
        from app.models.tesla_connection import TeslaConnection
        from app.services.incentive_engine import IncentiveEngine
        from app.services.session_event_service import SessionEventService
        from app.services.tesla_oauth import get_tesla_oauth_service, get_valid_access_token

        session = db.query(SessionEvent).filter(SessionEvent.id == session_id).first()
        if not session or session.session_end is not None:
            # Session already ended or doesn't exist
            return

        # Get Tesla connection
        tesla_conn = (
            db.query(TeslaConnection)
            .filter(
                TeslaConnection.user_id == driver_id,
                TeslaConnection.is_active == True,
                TeslaConnection.deleted_at.is_(None),
            )
            .first()
        )
        if not tesla_conn or not tesla_conn.vehicle_id:
            # No Tesla connection — reschedule or give up
            logger.warning(f"No Tesla connection for driver {driver_id}, clearing next_poll_at")
            session.next_poll_at = None
            db.commit()
            return

        oauth_service = get_tesla_oauth_service()
        access_token = await get_valid_access_token(db, tesla_conn, oauth_service)
        if not access_token:
            logger.warning(f"Token expired for driver {driver_id}, rescheduling +5min")
            session.next_poll_at = datetime.utcnow() + timedelta(minutes=5)
            db.commit()
            return

        # Poll Tesla
        import httpx

        vehicle_data = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    try:
                        await oauth_service.wake_vehicle(access_token, tesla_conn.vehicle_id)
                    except Exception:
                        pass
                    await asyncio.sleep(3)

                vehicle_data = await oauth_service.get_vehicle_data(
                    access_token, tesla_conn.vehicle_id
                )
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 408 and attempt < 2:
                    logger.info(
                        "ScheduledPoll: vehicle %s returned 408 (attempt %d/3)",
                        tesla_conn.vehicle_id,
                        attempt + 1,
                    )
                    continue
                raise

        if vehicle_data is None:
            # Vehicle unavailable — reschedule
            session.next_poll_at = datetime.utcnow() + timedelta(minutes=3)
            db.commit()
            return

        charge_state = vehicle_data.get("charge_state", {})
        drive_state = vehicle_data.get("drive_state", {})
        charging_state = charge_state.get("charging_state")
        is_charging = charging_state in {"Charging", "Starting"}

        if is_charging:
            # Still charging — update telemetry, reschedule with smart halving
            session.kwh_delivered = charge_state.get("charge_energy_added") or session.kwh_delivered
            session.battery_end_pct = charge_state.get("battery_level") or session.battery_end_pct
            session.power_kw = charge_state.get("charger_power") or session.power_kw
            session.updated_at = datetime.utcnow()

            # Backfill location if missing
            tesla_lat = drive_state.get("latitude")
            tesla_lng = drive_state.get("longitude")
            if not session.lat and tesla_lat:
                session.lat = tesla_lat
                session.lng = tesla_lng

            # Smart halving: poll at half of remaining time, floor 2 min
            # minutes_to_full_charge is in MINUTES, time_to_full_charge is in HOURS
            _mtf_min = charge_state.get("minutes_to_full_charge")
            _mtf_hr = charge_state.get("time_to_full_charge")
            if _mtf_min and isinstance(_mtf_min, (int, float)) and _mtf_min > 0:
                interval_min = max(2.0, _mtf_min / 2.0)
            elif _mtf_hr and isinstance(_mtf_hr, (int, float)) and _mtf_hr > 0:
                interval_min = max(2.0, (_mtf_hr * 60) / 2.0)
            else:
                interval_min = 5.0  # fallback when Tesla doesn't report time remaining

            session.next_poll_at = datetime.utcnow() + timedelta(minutes=interval_min)
            db.commit()
            logger.info(
                f"ScheduledPoll: session {session_id} still charging, "
                f"rescheduled +{interval_min:.0f}min (mtf_min={_mtf_min}, mtf_hr={_mtf_hr})"
            )
        else:
            # Not charging — end session and evaluate incentive
            ended = SessionEventService.end_session(
                db,
                session_id,
                ended_reason="unplugged",
                battery_end_pct=charge_state.get("battery_level"),
                kwh_delivered=charge_state.get("charge_energy_added"),
            )

            grant = None
            if ended and ended.duration_minutes and ended.duration_minutes > 0:
                grant = IncentiveEngine.evaluate_session(db, ended)

            # Award base reputation for valid sessions without incentive grants
            if ended and not grant and ended.duration_minutes and ended.duration_minutes > 0:
                quality = ended.quality_score or 0
                if quality > 30:
                    try:
                        from app.models_domain import DriverWallet as DomainWallet

                        wallet = (
                            db.query(DomainWallet).filter(DomainWallet.user_id == driver_id).first()
                        )
                        if wallet:
                            wallet.energy_reputation_score = (
                                wallet.energy_reputation_score or 0
                            ) + 5
                    except Exception:
                        pass

            # Clear next_poll_at (session ended)
            if ended:
                ended.next_poll_at = None

            db.commit()

            # Send push notification (best-effort)
            try:
                from app.services.push_service import (
                    send_incentive_earned_push,
                    send_push_notification,
                )

                if grant and grant.amount_cents > 0:
                    send_incentive_earned_push(db, driver_id, grant.amount_cents)
                else:
                    # Notify session ended even without incentive
                    duration = ended.duration_minutes if ended else 0
                    send_push_notification(
                        db,
                        driver_id,
                        title="Charging session complete",
                        body=f"Your {duration}-minute charging session has ended.",
                        data={"type": "session_ended", "session_id": str(session_id)},
                    )
            except Exception as e:
                logger.debug(f"Push notification failed (non-fatal): {e}")

            logger.info(
                f"ScheduledPoll: session {session_id} ended "
                f"(duration={ended.duration_minutes if ended else '?'}min, "
                f"incentive={f'${grant.amount_cents / 100:.2f}' if grant else 'none'})"
            )


# Singleton instance
scheduled_poll_worker = ScheduledPollWorker()
