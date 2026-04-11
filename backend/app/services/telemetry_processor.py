"""
Telemetry Processor — Maps Tesla Fleet Telemetry events to session lifecycle.

Receives telemetry data from the Fleet Telemetry webhook and translates
vehicle charge-state changes into SessionEvent create/update/end operations.
Reuses existing SessionEventService methods for session management.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.session_event import SessionEvent
from app.models.tesla_connection import TeslaConnection
from app.services.session_event_service import SessionEventService

logger = logging.getLogger(__name__)


class TelemetryProcessor:
    """Processes Fleet Telemetry events into charging session lifecycle."""

    # States indicating active charging
    CHARGING_STATES = {"Charging", "Starting"}
    # States indicating charging has ended
    ENDED_STATES = {"Disconnected", "Complete", "Stopped", "NoPower"}

    @staticmethod
    def process_telemetry(
        db: Session,
        vin: str,
        telemetry_data: list,
        created_at: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Process a Fleet Telemetry event for a single VIN.

        Args:
            db: Database session
            vin: Vehicle Identification Number
            telemetry_data: List of TelemetryValue dicts with key/value pairs
            created_at: ISO timestamp from telemetry event

        Returns:
            Result dict with action taken, or None if no action needed
        """
        # 1. Lookup driver by VIN via TeslaConnection
        tesla_conn = (
            db.query(TeslaConnection)
            .filter(
                TeslaConnection.vin == vin,
                TeslaConnection.is_active == True,
            )
            .first()
        )
        if not tesla_conn:
            logger.debug("No active TeslaConnection for VIN %s", vin)
            return None

        driver_id = tesla_conn.user_id

        # 2. Extract telemetry fields into a flat dict
        fields = {}
        for item in telemetry_data:
            key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
            value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
            # Unwrap Tesla's nested value format: {"stringValue": "X"} → "X"
            if isinstance(value, dict):
                if "stringValue" in value:
                    value = value["stringValue"]
                elif "value" in value:
                    value = value["value"]
            if key:
                fields[key] = value

        # Unpack Location dict into Latitude/Longitude for downstream code
        location = fields.get("Location")
        if isinstance(location, dict):
            if "latitude" in location:
                fields["Latitude"] = location["latitude"]
            if "longitude" in location:
                fields["Longitude"] = location["longitude"]

        logger.info(
            "Telemetry fields for VIN %s: keys=%s, DetailedChargeState=%s",
            vin, list(fields.keys()), fields.get("DetailedChargeState"),
        )

        # 3. Determine charge state
        charge_state = fields.get("DetailedChargeState")
        if charge_state is None:
            # No charge state in this telemetry batch — might be location-only update
            # Try to update existing session with other fields if active
            active = SessionEventService.get_active_session(db, driver_id)
            if active:
                updated = TelemetryProcessor._update_session_telemetry(active, fields)
                if updated:
                    active.updated_at = datetime.utcnow()
                    db.commit()
                    return {"action": "updated", "session_id": active.id}
            return None

        is_charging = charge_state in TelemetryProcessor.CHARGING_STATES
        is_ended = charge_state in TelemetryProcessor.ENDED_STATES

        # 4. Get active session
        active = SessionEventService.get_active_session(db, driver_id)

        # 5. State transitions
        if is_charging and not active:
            # New charging session detected via telemetry
            return TelemetryProcessor._start_session(
                db, driver_id, tesla_conn, fields
            )

        elif is_charging and active:
            # Update telemetry on existing session
            TelemetryProcessor._update_session_telemetry(active, fields)
            active.updated_at = datetime.utcnow()
            db.commit()
            return {"action": "updated", "session_id": active.id}

        elif (is_ended or not is_charging) and active:
            # Session ended
            return TelemetryProcessor._end_session(db, driver_id, active, fields)

        # Not charging and no active session — nothing to do
        return None

    @staticmethod
    def _start_session(
        db: Session,
        driver_id: int,
        tesla_conn: TeslaConnection,
        fields: Dict[str, Any],
    ) -> dict:
        """Create a new session from telemetry data."""
        # Build charge_data in the format create_from_tesla() expects
        charge_data = TelemetryProcessor._build_charge_data(fields)
        vehicle_info = {
            "id": tesla_conn.vehicle_id or "",
            "vin": tesla_conn.vin,
        }

        # Match to nearest charger if we have location
        charger_id = None
        lat = fields.get("Latitude")
        lng = fields.get("Longitude")
        if lat is not None and lng is not None:
            try:
                from app.services.intent_service import find_nearest_charger
                result = find_nearest_charger(db, float(lat), float(lng), radius_m=500)
                if result:
                    charger_id = result[0].id
                    logger.info(
                        "Telemetry: matched to charger %s at %.0fm",
                        charger_id, result[1],
                    )
            except Exception as e:
                logger.warning("Telemetry: charger matching failed: %s", e)

        session = SessionEventService.create_from_tesla(
            db, driver_id, charge_data, vehicle_info,
            charger_id=charger_id,
        )
        # Mark as telemetry-sourced
        session.source = "fleet_telemetry"
        session.verification_method = "telemetry"
        db.commit()

        # Send push notification (best-effort)
        charger_name = None
        if charger_id:
            try:
                from app.models.domain import Charger
                charger = db.query(Charger).filter(Charger.id == charger_id).first()
                if charger:
                    charger_name = charger.name
            except Exception:
                pass

        try:
            from app.services.push_service import send_charging_detected_push
            send_charging_detected_push(db, driver_id, session.id, charger_name)
        except Exception as e:
            logger.debug("Charging detected push failed (non-fatal): %s", e)

        logger.info(
            "Telemetry: created session %s for driver %s (VIN %s)",
            session.id, driver_id, tesla_conn.vin,
        )
        return {"action": "created", "session_id": session.id}

    @staticmethod
    def _end_session(
        db: Session,
        driver_id: int,
        active: SessionEvent,
        fields: Dict[str, Any],
    ) -> dict:
        """End an active session and evaluate incentives."""
        from app.services.incentive_engine import IncentiveEngine

        battery_end = fields.get("BatteryLevel")
        kwh = fields.get("ACChargingEnergyIn") or fields.get("DCChargingEnergyIn")

        session = SessionEventService.end_session(
            db, active.id,
            ended_reason="telemetry_disconnected",
            battery_end_pct=int(battery_end) if battery_end is not None else None,
            kwh_delivered=float(kwh) if kwh is not None else None,
        )

        # Evaluate incentives
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
                except Exception as e:
                    logger.debug("Base reputation award failed (non-fatal): %s", e)

        db.commit()

        # Send push for incentive earned (best-effort)
        if grant and grant.amount_cents > 0:
            try:
                from app.services.push_service import send_incentive_earned_push
                send_incentive_earned_push(db, driver_id, grant.amount_cents)
            except Exception as e:
                logger.debug("Incentive push failed (non-fatal): %s", e)

        logger.info(
            "Telemetry: ended session %s for driver %s (%d min)",
            active.id, driver_id, session.duration_minutes or 0,
        )
        return {
            "action": "ended",
            "session_id": active.id,
            "duration_minutes": session.duration_minutes if session else 0,
            "incentive_granted": grant is not None,
            "incentive_amount_cents": grant.amount_cents if grant else 0,
        }

    @staticmethod
    def _build_charge_data(fields: Dict[str, Any]) -> dict:
        """Convert telemetry fields to the charge_data format used by create_from_tesla()."""
        power = fields.get("ACChargingPower") or fields.get("DCChargingPower")
        kwh = fields.get("ACChargingEnergyIn") or fields.get("DCChargingEnergyIn")

        fast_charger_type = None
        if fields.get("FastChargerPresent"):
            fast_charger_type = fields.get("FastChargerType")

        return {
            "battery_level": fields.get("BatteryLevel"),
            "charger_power": float(power) if power is not None else None,
            "charge_energy_added": float(kwh) if kwh is not None else None,
            "lat": fields.get("Latitude"),
            "lng": fields.get("Longitude"),
            "fast_charger_type": fast_charger_type,
        }

    @staticmethod
    def _update_session_telemetry(
        session: SessionEvent,
        fields: Dict[str, Any],
    ) -> bool:
        """Update session with latest telemetry values. Returns True if anything changed."""
        changed = False

        battery = fields.get("BatteryLevel")
        if battery is not None:
            session.battery_end_pct = int(battery)
            changed = True

        power = fields.get("ACChargingPower") or fields.get("DCChargingPower")
        if power is not None:
            session.power_kw = float(power)
            changed = True

        kwh = fields.get("ACChargingEnergyIn") or fields.get("DCChargingEnergyIn")
        if kwh is not None:
            session.kwh_delivered = float(kwh)
            changed = True

        lat = fields.get("Latitude")
        lng = fields.get("Longitude")
        if lat is not None and lng is not None and not session.lat:
            session.lat = float(lat)
            session.lng = float(lng)
            changed = True
            # Try to match charger if not set
            if not session.charger_id:
                try:
                    from app.db import SessionLocal
                    from app.services.intent_service import find_nearest_charger
                    db = SessionLocal()
                    try:
                        result = find_nearest_charger(db, float(lat), float(lng), radius_m=500)
                        if result:
                            session.charger_id = result[0].id
                    finally:
                        db.close()
                except Exception:
                    pass

        return changed
