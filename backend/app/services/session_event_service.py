"""
Session Event Service — Manages charging session lifecycle.

Creates SessionEvent records from Tesla API data (or other sources).
Triggers incentive evaluation on session END, not session start.
Polls one vehicle only, with backoff and caching.
"""
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_, desc

from app.models.session_event import SessionEvent
from app.models.tesla_connection import TeslaConnection

logger = logging.getLogger(__name__)

# Cache: driver_id -> last_poll_result to reduce redundant API calls
# Bounded to prevent unbounded memory growth in long-running processes
_CACHE_MAX_SIZE = 10000
_CACHE_EVICT_AGE_SECS = 300  # evict entries older than 5 minutes
_charging_cache: Dict[int, Dict[str, Any]] = {}


def _cache_cleanup() -> None:
    """Evict stale entries when cache exceeds max size."""
    if len(_charging_cache) <= _CACHE_MAX_SIZE:
        return
    now = datetime.utcnow()
    stale_keys = [
        k for k, v in _charging_cache.items()
        if (now - v.get("last_poll", datetime.min)).total_seconds() > _CACHE_EVICT_AGE_SECS
    ]
    for k in stale_keys:
        _charging_cache.pop(k, None)
    # If still too large, remove oldest entries
    if len(_charging_cache) > _CACHE_MAX_SIZE:
        sorted_keys = sorted(
            _charging_cache.keys(),
            key=lambda k: _charging_cache[k].get("last_poll", datetime.min)
        )
        for k in sorted_keys[:len(_charging_cache) - _CACHE_MAX_SIZE]:
            _charging_cache.pop(k, None)


class SessionEventService:
    """Manages charging session lifecycle and incentive triggering."""

    @staticmethod
    def create_from_tesla(
        db: Session,
        driver_id: int,
        charge_data: dict,
        vehicle_info: dict,
        charger_id: Optional[str] = None,
        charger_network: str = "Tesla",
    ) -> SessionEvent:
        """
        Create or update a session event from Tesla API charge_state data.

        Args:
            charge_data: Tesla charge_state response
            vehicle_info: Vehicle metadata (id, vin, display_name)
        """
        vehicle_id = str(vehicle_info.get("id", ""))
        vin = vehicle_info.get("vin")

        # Check for existing active session for this driver+vehicle
        active = SessionEventService.get_active_session(db, driver_id, vehicle_id=vehicle_id)
        if active:
            # Update telemetry on existing session
            active.kwh_delivered = charge_data.get("charge_energy_added")
            active.battery_end_pct = charge_data.get("battery_level")
            active.power_kw = charge_data.get("charger_power")
            active.updated_at = datetime.utcnow()
            db.flush()
            return active

        # Build a stable source_session_id for dedup
        # Use vehicle_id + current date to avoid duplicates within same day
        # (Tesla doesn't provide a unique charge session ID)
        now = datetime.utcnow()
        source_session_id = f"tesla_{vehicle_id}_{now.strftime('%Y%m%d_%H')}"

        # Create new session event
        session_event = SessionEvent(
            id=str(uuid.uuid4()),
            driver_user_id=driver_id,
            user_id=driver_id,
            charger_id=charger_id,
            charger_network=charger_network,
            connector_type=charge_data.get("fast_charger_type") or "Tesla",
            power_kw=charge_data.get("charger_power"),
            session_start=now,
            source="tesla_api",
            source_session_id=source_session_id,
            verified=True,
            verification_method="api_polling",
            lat=charge_data.get("lat"),
            lng=charge_data.get("lng"),
            battery_start_pct=charge_data.get("battery_level"),
            vehicle_id=vehicle_id,
            vehicle_vin=vin,
            kwh_delivered=charge_data.get("charge_energy_added"),
            # Charger cable/adapter telemetry — key for CCS adapter detection (EVject)
            conn_charge_cable=charge_data.get("conn_charge_cable"),
            fast_charger_brand=charge_data.get("fast_charger_brand"),
            charger_voltage=charge_data.get("charger_voltage"),
            charger_actual_current=charge_data.get("charger_actual_current"),
        )
        db.add(session_event)
        db.flush()
        cable_info = ""
        if charge_data.get("conn_charge_cable") or charge_data.get("fast_charger_brand"):
            cable_info = (
                f" cable={charge_data.get('conn_charge_cable')}"
                f" brand={charge_data.get('fast_charger_brand')}"
                f" voltage={charge_data.get('charger_voltage')}"
                f" current={charge_data.get('charger_actual_current')}"
            )
        logger.info(f"Created session_event {session_event.id} for driver {driver_id}{cable_info}")
        return session_event

    @staticmethod
    def end_session(
        db: Session,
        session_event_id: str,
        ended_reason: str = "unplugged",
        battery_end_pct: Optional[int] = None,
        kwh_delivered: Optional[float] = None,
    ) -> Optional[SessionEvent]:
        """
        End an active session. Computes duration.
        IncentiveEngine should be called AFTER this returns.
        """
        # Use FOR UPDATE to prevent concurrent end_session calls from racing
        try:
            session = db.query(SessionEvent).filter(
                SessionEvent.id == session_event_id
            ).with_for_update(skip_locked=True).first()
        except Exception:
            # SQLite doesn't support FOR UPDATE — fall back to regular query
            session = db.query(SessionEvent).filter(SessionEvent.id == session_event_id).first()
        if not session or session.session_end is not None:
            return session

        now = datetime.utcnow()
        session.session_end = now
        session.duration_minutes = int((now - session.session_start).total_seconds() / 60)
        session.ended_reason = ended_reason
        session.next_poll_at = None  # Clear scheduled verification poll
        if battery_end_pct is not None:
            session.battery_end_pct = battery_end_pct
        if kwh_delivered is not None:
            session.kwh_delivered = kwh_delivered

        # Compute quality score (basic heuristics)
        session.quality_score = SessionEventService._compute_quality_score(session)
        session.updated_at = now
        db.flush()

        logger.info(
            f"Ended session {session.id}: {session.duration_minutes}min, "
            f"reason={ended_reason}, quality={session.quality_score}"
        )
        return session

    @staticmethod
    def get_active_session(
        db: Session,
        driver_id: int,
        vehicle_id: Optional[str] = None,
    ) -> Optional[SessionEvent]:
        """Find the active (un-ended) session for a driver."""
        query = db.query(SessionEvent).filter(
            SessionEvent.driver_user_id == driver_id,
            SessionEvent.session_end.is_(None),
        )
        if vehicle_id:
            query = query.filter(SessionEvent.vehicle_id == vehicle_id)
        return query.order_by(desc(SessionEvent.session_start)).first()

    @staticmethod
    def get_driver_sessions(
        db: Session,
        driver_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SessionEvent]:
        """Get a driver's charging sessions, most recent first."""
        return (
            db.query(SessionEvent)
            .filter(SessionEvent.driver_user_id == driver_id)
            .order_by(desc(SessionEvent.session_start))
            .offset(offset)
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_charger_sessions(
        db: Session,
        charger_id: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[SessionEvent]:
        """Get sessions at a specific charger."""
        query = db.query(SessionEvent).filter(SessionEvent.charger_id == charger_id)
        if since:
            query = query.filter(SessionEvent.session_start >= since)
        if until:
            query = query.filter(SessionEvent.session_start <= until)
        return query.order_by(desc(SessionEvent.session_start)).limit(limit).all()

    @staticmethod
    async def poll_driver_session(
        db: Session,
        driver_id: int,
        tesla_connection: "TeslaConnection",
        tesla_oauth_service: Any,
        device_lat: Optional[float] = None,
        device_lng: Optional[float] = None,
    ) -> dict:
        """
        Poll Tesla API for a single driver's charging state.
        Creates/updates/ends session events as needed.
        Implements caching and backoff per review.

        Returns: {session_active, session_id, duration_minutes, ...}
        """
        from app.services.incentive_engine import IncentiveEngine

        # Check cache: skip if polled within 15s and still charging
        cache_key = driver_id
        cached = _charging_cache.get(cache_key)
        if cached and cached.get("still_charging"):
            last_poll = cached.get("last_poll", datetime.min)
            if (datetime.utcnow() - last_poll).total_seconds() < 15:
                active = SessionEventService.get_active_session(db, driver_id)
                if active:
                    return {
                        "session_active": True,
                        "session_id": active.id,
                        "duration_minutes": int((datetime.utcnow() - active.session_start).total_seconds() / 60),
                        "kwh_delivered": active.kwh_delivered,
                        "cached": True,
                    }

        # Poll Tesla API — ONE vehicle only (per review)
        # Includes wake-up + retry for sleeping vehicles
        try:
            vehicle_id = tesla_connection.vehicle_id
            if not vehicle_id:
                return {"session_active": False, "error": "no_vehicle_selected"}

            # Get a valid (refreshed if needed) access token
            from app.services.tesla_oauth import get_valid_access_token
            access_token = await get_valid_access_token(
                db, tesla_connection, tesla_oauth_service
            )
            if not access_token:
                return {"session_active": False, "error": "token_expired"}

            # get_vehicle_data with wake-up and retry on 408/sleeping
            # Only wake vehicle if there's an active session or geofence trigger
            # Routine polls should NOT wake sleeping cars (saves ~100 wake calls/day)
            import asyncio
            import httpx
            active_before_poll = SessionEventService.get_active_session(db, driver_id)
            should_wake = active_before_poll is not None or (device_lat is not None and device_lng is not None)
            vehicle_data = None
            max_attempts = 3 if should_wake else 1
            for attempt in range(max_attempts):
                try:
                    # Wake vehicle before data request only when justified
                    if attempt > 0 and should_wake:
                        try:
                            await tesla_oauth_service.wake_vehicle(access_token, vehicle_id)
                        except Exception:
                            pass
                        await asyncio.sleep(3)

                    vehicle_data = await tesla_oauth_service.get_vehicle_data(
                        access_token, vehicle_id
                    )
                    break  # Success
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 408:
                        if should_wake and attempt < 2:
                            logger.info(
                                "Vehicle %s returned 408 (attempt %d/%d), waking and retrying",
                                vehicle_id, attempt + 1, max_attempts
                            )
                            continue
                        # Vehicle is asleep and no reason to wake — return early
                        logger.info("Vehicle %s is asleep, skipping (no active session)", vehicle_id)
                        _charging_cache[cache_key] = {
                            "still_charging": False,
                            "last_poll": datetime.utcnow(),
                        }
                        return {"session_active": False, "vehicle_asleep": True}
                    raise  # Non-408 — propagate

            if vehicle_data is None:
                return {"session_active": False, "error": "vehicle_unavailable"}

            charge_state = vehicle_data.get("charge_state", {})
            drive_state = vehicle_data.get("drive_state", {})
            charging_state = charge_state.get("charging_state")
            is_charging = charging_state in {"Charging", "Starting"}

            # Merge location from drive_state into charge_state for downstream use
            charge_state["lat"] = drive_state.get("latitude")
            charge_state["lng"] = drive_state.get("longitude")

        except Exception as e:
            # Backoff on error — clear cache, don't crash
            logger.warning(f"Tesla poll error for driver {driver_id}: {e}")
            _charging_cache.pop(cache_key, None)

            # Even though Tesla didn't respond, still record the driver's
            # walking location if there's an active session. The phone GPS
            # is independent of the car API.
            if device_lat is not None and device_lng is not None:
                active_for_trail = SessionEventService.get_active_session(db, driver_id)
                if active_for_trail:
                    meta = dict(active_for_trail.session_metadata or {})
                    meta["device_lat"] = device_lat
                    meta["device_lng"] = device_lng
                    trail = list(meta.get("location_trail", []))
                    trail.append({
                        "lat": device_lat,
                        "lng": device_lng,
                        "ts": datetime.utcnow().isoformat(),
                    })
                    if len(trail) > 120:
                        trail = trail[-120:]
                    meta["location_trail"] = trail
                    active_for_trail.session_metadata = meta
                    flag_modified(active_for_trail, "session_metadata")
                    active_for_trail.updated_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"Trail point added for driver {driver_id} despite Tesla error (total: {len(trail)})")

            # Auto-close stale sessions on poll error (>15 min since last update)
            stale = SessionEventService._close_stale_session(db, driver_id)
            if stale:
                db.commit()
                return {
                    "session_active": False,
                    "session_id": stale.id,
                    "duration_minutes": stale.duration_minutes or 0,
                    "session_ended": True,
                    "incentive_granted": False,
                    "incentive_amount_cents": 0,
                }

            return {"session_active": False, "error": "poll_failed"}

        # Update cache (with periodic eviction)
        _cache_cleanup()
        _charging_cache[cache_key] = {
            "still_charging": is_charging,
            "last_poll": datetime.utcnow(),
        }

        # Expire only session-related objects instead of the entire identity map
        for obj in db.identity_map.values():
            if isinstance(obj, SessionEvent):
                db.expire(obj)
        active = SessionEventService.get_active_session(db, driver_id)

        # Raw SQL fallback — bypasses ORM column mapping issues
        if not active:
            from sqlalchemy import text
            row = db.execute(
                text("SELECT id FROM session_events "
                     "WHERE driver_user_id = :did AND session_end IS NULL "
                     "ORDER BY session_start DESC LIMIT 1"),
                {"did": driver_id},
            ).first()
            if row:
                active = db.query(SessionEvent).filter(
                    SessionEvent.id == str(row[0])
                ).first()
                if active:
                    logger.warning(
                        f"Raw SQL found active session {active.id} missed by ORM "
                        f"for driver {driver_id}"
                    )

        if is_charging and not active:
            # Before creating a new session, check if there's a recently ended
            # session for this vehicle that we should REOPEN instead.
            # This prevents session fragmentation when the app goes to background
            # and comes back (stale cleanup may have closed a still-active session).
            recent_cutoff = datetime.utcnow() - timedelta(minutes=30)
            recently_ended = (
                db.query(SessionEvent)
                .filter(
                    SessionEvent.driver_user_id == driver_id,
                    SessionEvent.vehicle_id == str(vehicle_id),
                    SessionEvent.session_end.is_not(None),
                    SessionEvent.session_end >= recent_cutoff,
                    SessionEvent.ended_reason.in_(["stale_cleanup", "manual"]),
                )
                .order_by(desc(SessionEvent.session_end))
                .first()
            )
            if recently_ended:
                # Reopen the session — car is still charging
                logger.info(
                    f"Reopening recently closed session {recently_ended.id} "
                    f"(ended {recently_ended.session_end}, reason={recently_ended.ended_reason}) "
                    f"— vehicle still charging"
                )
                recently_ended.session_end = None
                recently_ended.duration_minutes = None
                recently_ended.ended_reason = None
                recently_ended.quality_score = None
                recently_ended.kwh_delivered = charge_state.get("charge_energy_added")
                recently_ended.battery_end_pct = charge_state.get("battery_level")
                recently_ended.power_kw = charge_state.get("charger_power")
                recently_ended.updated_at = datetime.utcnow()
                db.commit()
                _charging_cache[cache_key] = {
                    "still_charging": True,
                    "last_poll": datetime.utcnow(),
                }
                return {
                    "session_active": True,
                    "session_id": recently_ended.id,
                    "duration_minutes": int((datetime.utcnow() - recently_ended.session_start).total_seconds() / 60),
                    "kwh_delivered": recently_ended.kwh_delivered,
                    "session_reopened": True,
                }

            # Start new session — match to nearest known charger
            vehicle_info = {"id": vehicle_id, "vin": tesla_connection.vin}
            matched_charger_id = None
            tesla_lat = charge_state.get("lat")
            tesla_lng = charge_state.get("lng")
            if tesla_lat and tesla_lng:
                try:
                    from app.services.intent_service import find_nearest_charger
                    result = find_nearest_charger(db, tesla_lat, tesla_lng, radius_m=500)
                    if result:
                        matched_charger, distance_m = result
                        matched_charger_id = matched_charger.id
                        logger.info(
                            f"Matched session to charger {matched_charger_id} "
                            f"({matched_charger.name}) at {distance_m:.0f}m"
                        )
                except Exception as e:
                    logger.warning(f"Charger matching failed: {e}")

            # Store device location in metadata (start of trail)
            metadata = {}
            if device_lat is not None and device_lng is not None:
                metadata["device_lat"] = device_lat
                metadata["device_lng"] = device_lng
                metadata["location_trail"] = [{
                    "lat": device_lat,
                    "lng": device_lng,
                    "ts": datetime.utcnow().isoformat(),
                }]
                logger.info(f"Device location: {device_lat}, {device_lng}")

            session = SessionEventService.create_from_tesla(
                db, driver_id, charge_state, vehicle_info,
                charger_id=matched_charger_id,
            )
            if metadata:
                session.session_metadata = metadata
                db.flush()

            # Extract Tesla's estimated time remaining for smart polling
            # minutes_to_full_charge is in MINUTES, time_to_full_charge is in HOURS
            _mtf_min = charge_state.get("minutes_to_full_charge")
            _mtf_hr = charge_state.get("time_to_full_charge")
            if _mtf_min and isinstance(_mtf_min, (int, float)) and _mtf_min > 0:
                minutes_remaining = int(_mtf_min)
            elif _mtf_hr and isinstance(_mtf_hr, (int, float)) and _mtf_hr > 0:
                minutes_remaining = int(_mtf_hr * 60)
            else:
                minutes_remaining = None

            # Schedule server-side verification poll (smart halving)
            try:
                session.next_poll_at = SessionEventService._calculate_next_poll_at(
                    db, session, minutes_to_full=minutes_remaining,
                )
                db.flush()
                logger.info(
                    f"Scheduled next poll for session {session.id} at {session.next_poll_at} "
                    f"(minutes_to_full={minutes_remaining})"
                )
            except Exception as e:
                logger.debug(f"next_poll_at calculation failed (non-fatal): {e}")

            db.commit()

            # Send push notification for charging detection (best-effort)
            charger_name = None
            if matched_charger_id:
                try:
                    from app.models.domain import Charger
                    charger = db.query(Charger).filter(Charger.id == matched_charger_id).first()
                    if charger:
                        charger_name = charger.name
                except Exception:
                    pass
            try:
                from app.services.push_service import send_charging_detected_push
                send_charging_detected_push(db, driver_id, session.id, charger_name)
            except Exception as e:
                logger.debug("Charging detected push failed (non-fatal): %s", e)

            # Send push for nearby Nerava merchants (best-effort)
            if matched_charger_id:
                try:
                    from app.models.while_you_charge import ChargerMerchant, Merchant
                    from app.services.push_service import send_nearby_merchant_push
                    links = db.query(ChargerMerchant).filter(
                        ChargerMerchant.charger_id == matched_charger_id,
                        ChargerMerchant.exclusive_title.isnot(None),
                        ChargerMerchant.exclusive_title != "",
                    ).order_by(ChargerMerchant.distance_m.asc()).limit(1).all()
                    for link in links:
                        merch = db.query(Merchant).filter(Merchant.id == link.merchant_id).first()
                        if merch:
                            send_nearby_merchant_push(
                                db, driver_id, merch.name,
                                exclusive_title=link.exclusive_title,
                                charger_id=matched_charger_id,
                                merchant_place_id=merch.place_id or merch.id,
                            )
                except Exception as e:
                    logger.debug("Nearby merchant push failed (non-fatal): %s", e)

            # Smart poll interval for client: halve remaining time, floor 2 min (120s)
            if minutes_remaining and minutes_remaining > 0:
                recommended = max(120, int(minutes_remaining / 2.0 * 60))
            else:
                recommended = 300  # Unknown ETA, check in 5 min

            return {
                "session_active": True,
                "session_id": session.id,
                "duration_minutes": 0,
                "kwh_delivered": session.kwh_delivered,
                "minutes_to_full": minutes_remaining,
                "battery_level": charge_state.get("battery_level"),
                "charger_power_kw": charge_state.get("charger_power"),
                "recommended_interval_s": recommended,
                "conn_charge_cable": charge_state.get("conn_charge_cable"),
                "fast_charger_brand": charge_state.get("fast_charger_brand"),
                "charger_voltage": charge_state.get("charger_voltage"),
                "charger_actual_current": charge_state.get("charger_actual_current"),
            }

        elif is_charging and active:
            # Update existing session telemetry
            active.kwh_delivered = charge_state.get("charge_energy_added")
            active.battery_end_pct = charge_state.get("battery_level")
            active.power_kw = charge_state.get("charger_power")
            active.updated_at = datetime.utcnow()

            # Update cable/adapter telemetry (may arrive after session start)
            cable = charge_state.get("conn_charge_cable")
            if cable and not active.conn_charge_cable:
                active.conn_charge_cable = cable
                logger.info(f"Session {active.id} cable detected: {cable}")
            brand = charge_state.get("fast_charger_brand")
            if brand and not active.fast_charger_brand:
                active.fast_charger_brand = brand
                logger.info(f"Session {active.id} charger brand detected: {brand}")
            active.charger_voltage = charge_state.get("charger_voltage")
            active.charger_actual_current = charge_state.get("charger_actual_current")

            # Backfill Tesla location if missing from session start
            tesla_lat = charge_state.get("lat")
            tesla_lng = charge_state.get("lng")
            if not active.lat and tesla_lat:
                active.lat = tesla_lat
                active.lng = tesla_lng
                # Also try to match charger if not already set
                if not active.charger_id and tesla_lat and tesla_lng:
                    try:
                        from app.services.intent_service import find_nearest_charger
                        result = find_nearest_charger(db, tesla_lat, tesla_lng, radius_m=500)
                        if result:
                            active.charger_id = result[0].id
                            logger.info(f"Backfilled charger_id={active.charger_id} on session {active.id}")
                    except Exception:
                        pass

            # Append device location to location trail in metadata
            if device_lat is not None and device_lng is not None:
                meta = dict(active.session_metadata or {})
                meta["device_lat"] = device_lat
                meta["device_lng"] = device_lng
                trail = list(meta.get("location_trail", []))
                trail.append({
                    "lat": device_lat,
                    "lng": device_lng,
                    "ts": datetime.utcnow().isoformat(),
                })
                # Keep last 120 points (~60 min at 30s intervals)
                if len(trail) > 120:
                    trail = trail[-120:]
                meta["location_trail"] = trail
                active.session_metadata = meta
                flag_modified(active, "session_metadata")

            db.commit()

            # Smart interval: halve remaining time, floor 2 min (120s)
            # minutes_to_full_charge is in MINUTES, time_to_full_charge is in HOURS
            _mtf_min2 = charge_state.get("minutes_to_full_charge")
            _mtf_hr2 = charge_state.get("time_to_full_charge")
            if _mtf_min2 and isinstance(_mtf_min2, (int, float)) and _mtf_min2 > 0:
                mtf = int(_mtf_min2)
            elif _mtf_hr2 and isinstance(_mtf_hr2, (int, float)) and _mtf_hr2 > 0:
                mtf = int(_mtf_hr2 * 60)
            else:
                mtf = None

            if mtf and mtf > 0:
                rec_interval = max(120, int(mtf / 2.0 * 60))
            else:
                rec_interval = 300

            return {
                "session_active": True,
                "session_id": active.id,
                "duration_minutes": int((datetime.utcnow() - active.session_start).total_seconds() / 60),
                "kwh_delivered": active.kwh_delivered,
                "minutes_to_full": mtf,
                "battery_level": charge_state.get("battery_level"),
                "charger_power_kw": charge_state.get("charger_power"),
                "recommended_interval_s": rec_interval,
                "conn_charge_cable": charge_state.get("conn_charge_cable"),
                "fast_charger_brand": charge_state.get("fast_charger_brand"),
                "charger_voltage": charge_state.get("charger_voltage"),
                "charger_actual_current": charge_state.get("charger_actual_current"),
            }

        elif not is_charging and active:
            # Guard: refresh ORM state to prevent re-ending already-ended sessions
            db.refresh(active)
            if active.session_end is not None:
                logger.info("Session %s already ended, skipping re-end", active.id)
                _charging_cache.pop(cache_key, None)
                return {
                    "session_active": False,
                    "session_id": active.id,
                    "duration_minutes": active.duration_minutes or 0,
                    "kwh_delivered": active.kwh_delivered,
                    "session_ended": True,
                    "incentive_earned": None,
                    "incentive_amount_cents": None,
                    "incentive_campaign_name": None,
                }

            # Session ended — evaluate incentives (per review: pay on END)
            session = SessionEventService.end_session(
                db, active.id,
                ended_reason="unplugged",
                battery_end_pct=charge_state.get("battery_level"),
                kwh_delivered=charge_state.get("charge_energy_added"),
            )
            # Evaluate incentives now that session is complete
            grant = None
            if session and session.duration_minutes and session.duration_minutes > 0:
                grant = IncentiveEngine.evaluate_session(db, session)

            # Award base reputation for valid sessions without incentive grants
            if session and not grant and session.duration_minutes and session.duration_minutes > 0:
                quality = session.quality_score or 0
                if quality > 30:
                    try:
                        from app.models_domain import DriverWallet as DomainWallet
                        wallet = db.query(DomainWallet).filter(
                            DomainWallet.user_id == driver_id
                        ).first()
                        if wallet:
                            wallet.energy_reputation_score = (wallet.energy_reputation_score or 0) + 5
                            logger.info(
                                "Awarded 5 base reputation points to driver %s "
                                "(session %s, no incentive grant)", driver_id, session.id
                            )
                    except Exception as e:
                        logger.debug("Base reputation award failed (non-fatal): %s", e)

            # Grant referral rewards on first completed session (idempotent)
            if session and session.duration_minutes and session.duration_minutes > 0:
                try:
                    from app.services.referral_service import grant_referral_rewards
                    if grant_referral_rewards(db, driver_id):
                        logger.info("Referral rewards granted for driver %s on session %s", driver_id, session.id)
                except Exception as e:
                    logger.debug("Referral reward grant failed (non-fatal): %s", e)

            db.commit()
            _charging_cache.pop(cache_key, None)

            # Send push notification for incentive earned (best-effort)
            if grant and grant.amount_cents > 0:
                try:
                    from app.services.push_service import send_incentive_earned_push
                    send_incentive_earned_push(db, driver_id, grant.amount_cents)
                except Exception as push_err:
                    logger.debug("Push notification failed (non-fatal): %s", push_err)

            return {
                "session_active": False,
                "session_id": session.id if session else None,
                "duration_minutes": session.duration_minutes if session else 0,
                "kwh_delivered": session.kwh_delivered if session else None,
                "session_ended": True,
                "incentive_granted": grant is not None,
                "incentive_amount_cents": grant.amount_cents if grant else 0,
            }

        else:
            # Not charging and no active session — also check for stale sessions
            stale = SessionEventService._close_stale_session(db, driver_id)
            if stale:
                db.commit()
                return {
                    "session_active": False,
                    "session_id": stale.id,
                    "duration_minutes": stale.duration_minutes or 0,
                    "session_ended": True,
                    "incentive_granted": False,
                    "incentive_amount_cents": 0,
                }
            return {"session_active": False, "recommended_interval_s": 300}

    @staticmethod
    def _compute_quality_score(session: SessionEvent) -> int:
        """
        Basic anti-fraud quality score (0-100).
        Higher is better. Can be expanded with more heuristics later.
        """
        score = 50  # baseline

        # Duration scoring: reward normal sessions, penalize anomalies
        if session.duration_minutes:
            if session.duration_minutes > 1440:  # > 24 hours — certainly invalid
                return 0
            elif session.duration_minutes > 240:  # > 4 hours — likely stale/zombie
                score -= 50
            elif session.duration_minutes >= 15:
                score += 20
            elif session.duration_minutes >= 5:
                score += 10
            elif session.duration_minutes < 2:
                score -= 30  # suspiciously short

        # Energy delivered bonus
        if session.kwh_delivered and session.kwh_delivered > 1.0:
            score += 15
        elif session.kwh_delivered and session.kwh_delivered > 0:
            score += 5

        # Verified bonus
        if session.verified:
            score += 10

        # Battery change bonus (battery_end > battery_start = real charging)
        if session.battery_start_pct and session.battery_end_pct:
            if session.battery_end_pct > session.battery_start_pct:
                score += 5

        return max(0, min(100, score))

    @staticmethod
    def _close_stale_session(
        db: Session,
        driver_id: int,
        stale_minutes: int = 15,
    ) -> Optional[SessionEvent]:
        """
        Find and close ALL active sessions that haven't been updated in
        `stale_minutes`. Evaluates incentives for each closed session.
        Returns the most recent closed session or None.
        """
        from app.services.incentive_engine import IncentiveEngine

        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=stale_minutes)
        stale_sessions = (
            db.query(SessionEvent)
            .filter(
                SessionEvent.driver_user_id == driver_id,
                SessionEvent.session_end.is_(None),
                SessionEvent.updated_at < cutoff,
                # Respect smart polling: don't close sessions with future scheduled polls
                (SessionEvent.next_poll_at.is_(None)) | (SessionEvent.next_poll_at <= now),
            )
            .order_by(desc(SessionEvent.session_start))
            .all()
        )
        if not stale_sessions:
            return None

        for stale in stale_sessions:
            logger.info(
                f"Auto-closing stale session {stale.id} for driver {driver_id} "
                f"(last updated {stale.updated_at})"
            )
            ended = SessionEventService.end_session(
                db, stale.id,
                ended_reason="stale_cleanup",
                battery_end_pct=stale.battery_end_pct,
                kwh_delivered=stale.kwh_delivered,
            )
            # Evaluate incentives for stale-closed sessions too
            if ended and ended.duration_minutes and ended.duration_minutes > 0:
                grant = IncentiveEngine.evaluate_session(db, ended)
                if grant:
                    logger.info(
                        f"Granted {grant.amount_cents}c for stale-closed session "
                        f"{ended.id} ({ended.duration_minutes}min)"
                    )
                # Grant referral rewards on first completed session (idempotent)
                try:
                    from app.services.referral_service import grant_referral_rewards
                    if grant_referral_rewards(db, stale.driver_user_id):
                        logger.info(f"Referral rewards granted for driver {stale.driver_user_id} on stale session {ended.id}")
                except Exception as e:
                    logger.debug(f"Referral reward grant failed (non-fatal): {e}")

        return stale_sessions[0]  # Most recent for backward compat

    @staticmethod
    def end_session_manual(
        db: Session,
        session_event_id: str,
        driver_id: int,
    ) -> Optional[SessionEvent]:
        """
        Manually end a session (user-initiated). Verifies ownership.
        Evaluates incentives after ending.
        Returns the ended session or None if not found / not owned / already ended.
        """
        from app.services.incentive_engine import IncentiveEngine

        session = db.query(SessionEvent).filter(
            SessionEvent.id == session_event_id,
            SessionEvent.driver_user_id == driver_id,
            SessionEvent.session_end.is_(None),
        ).first()
        if not session:
            return None

        logger.info(f"Manual session end for {session.id} by driver {driver_id}")
        ended = SessionEventService.end_session(
            db, session.id,
            ended_reason="manual",
        )

        # Evaluate incentives for manually ended sessions
        if ended and ended.duration_minutes and ended.duration_minutes > 0:
            grant = IncentiveEngine.evaluate_session(db, ended)
            if grant:
                logger.info(
                    f"Granted {grant.amount_cents}c for manually ended session "
                    f"{ended.id} ({ended.duration_minutes}min)"
                )
            # Grant referral rewards on first completed session (idempotent)
            try:
                from app.services.referral_service import grant_referral_rewards
                if grant_referral_rewards(db, driver_id):
                    logger.info(f"Referral rewards granted for driver {driver_id} on manual session {ended.id}")
            except Exception as e:
                logger.debug(f"Referral reward grant failed (non-fatal): {e}")
            db.commit()

        return ended

    @staticmethod
    def _calculate_next_poll_at(
        db: Session,
        session: SessionEvent,
        minutes_to_full: Optional[int] = None,
    ) -> datetime:
        """
        Smart polling: use Tesla's `minutes_to_full_charge` to schedule
        the next server-side poll using a halving strategy.

        Strategy: check at half the remaining time, with a 2-minute floor.
        Example: 30 min remaining → poll in 15 min → 7 min → 3 min → 2 min → 2 min...

        If Tesla doesn't provide time remaining, fall back to campaign-based
        scheduling (poll at session_start + min_campaign_duration + 2 min).
        """
        now = datetime.utcnow()

        if minutes_to_full and minutes_to_full > 0:
            # Smart halving: poll at half the remaining time, floor 2 min
            half = minutes_to_full / 2.0
            interval_minutes = max(2.0, half)
            return now + timedelta(minutes=interval_minutes)

        # Fallback: campaign-based scheduling
        from app.services.campaign_service import CampaignService

        campaigns = CampaignService.get_active_campaigns(db)
        min_duration = 15  # default fallback (minutes)

        for campaign in campaigns:
            if SessionEventService._session_could_match_campaign(session, campaign):
                if campaign.rule_min_duration_minutes < min_duration:
                    min_duration = campaign.rule_min_duration_minutes

        buffer_minutes = 2
        return session.session_start + timedelta(minutes=min_duration + buffer_minutes)

    @staticmethod
    def _session_could_match_campaign(session: SessionEvent, campaign) -> bool:
        """
        Lightweight check if a session COULD match a campaign, ignoring
        duration (since the session just started). Used to find the minimum
        duration we need to wait before the verification poll.

        Only checks rules that are known at session start time:
        charger, network, zone, geo, time, day of week.
        """
        # Charger IDs
        if campaign.rule_charger_ids:
            if session.charger_id not in campaign.rule_charger_ids:
                return False

        # Charger networks
        if campaign.rule_charger_networks:
            if session.charger_network not in campaign.rule_charger_networks:
                return False

        # Zone IDs
        if campaign.rule_zone_ids:
            if session.zone_id not in campaign.rule_zone_ids:
                return False

        # Geo radius
        if campaign.rule_geo_center_lat is not None and campaign.rule_geo_center_lng is not None and campaign.rule_geo_radius_m:
            if session.lat is None or session.lng is None:
                return False
            from app.services.incentive_engine import IncentiveEngine
            dist = IncentiveEngine._haversine_m(
                campaign.rule_geo_center_lat, campaign.rule_geo_center_lng,
                session.lat, session.lng,
            )
            if dist > campaign.rule_geo_radius_m:
                return False

        # Time of day
        if campaign.rule_time_start and campaign.rule_time_end:
            from app.services.incentive_engine import IncentiveEngine
            session_hour_min = session.session_start.strftime("%H:%M")
            if not IncentiveEngine._time_in_window(
                session_hour_min, campaign.rule_time_start, campaign.rule_time_end
            ):
                return False

        # Day of week
        if campaign.rule_days_of_week:
            session_dow = session.session_start.isoweekday()
            if session_dow not in campaign.rule_days_of_week:
                return False

        return True

    @staticmethod
    def count_driver_sessions(
        db: Session,
        driver_id: int,
        charger_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Count completed sessions for a driver, optionally filtered."""
        query = db.query(SessionEvent).filter(
            SessionEvent.driver_user_id == driver_id,
            SessionEvent.session_end.is_not(None),
        )
        if charger_id:
            query = query.filter(SessionEvent.charger_id == charger_id)
        if since:
            query = query.filter(SessionEvent.session_start >= since)
        return query.count()
