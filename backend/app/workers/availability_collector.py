"""
Background worker that collects charger availability from TomTom every 15 minutes.

Runs as an asyncio task inside the FastAPI process. Lightweight: ~25 TomTom calls
per cycle plus one Tesla Fleet API call (for Harker Heights Supercharger data
via James's driver account).

Stores snapshots in charger_availability_snapshots for historical pattern analysis.
"""

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY", "")
POLL_INTERVAL_SECONDS = 900  # 15 minutes (25 stations × 96 polls/day = 2,400 < 2,500 free tier)
FIELD_ALERT_THRESHOLD_PCT = 60  # Alert when occupancy exceeds this %
BUSINESS_HOURS = (7, 21)  # 7am-9pm local time for alerts

# Tesla Fleet API collector: uses James's driver account (phone auth) to poll
# nearby_charging_sites every cycle. Tesla Superchargers don't expose availability
# via TomTom or any other public API — nearby_charging_sites is the only way,
# and it's scoped to the vehicle's current GPS location. As long as this Tesla
# stays in Bell County (Killeen/Harker Heights/Temple), Market Heights will be
# in the response. The target phone is stored here (not env var) because this
# is tactical data collection for the Harker Heights pizzeria merchant report
# (Fri 2026-04-10 → Tue 2026-04-14). To disable, set to empty string.
TESLA_COLLECTOR_PHONE = "+17133056318"

# Monitored stations across all regions
MONITORED_STATIONS: List[Dict[str, str]] = [
    # ── Austin Domain area ──
    {
        "charger_id": "tomtom_domain_1",
        "name": "ChargePoint @ 11505 Domain Dr",
        "avail_id": "a70292d8-fde5-41eb-b9c0-61829cda02c0",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_2",
        "name": "ChargePoint @ 11811 Domain Dr",
        "avail_id": "3b359414-28af-48c6-9554-29bd34aee61c",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_3",
        "name": "ChargePoint @ 11600 Alterra Pkwy",
        "avail_id": "b0c3386d-89ba-4b46-95a8-58b0f6a44b44",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_4",
        "name": "ChargePoint @ 3004 Palm Way (A)",
        "avail_id": "623d43b7-7768-4739-add0-4f9f859fbff7",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_5",
        "name": "ChargePoint @ 3004 Palm Way (B)",
        "avail_id": "1283fbc0-6385-4b8b-8432-7ba103a7e5cf",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_6",
        "name": "ChargePoint @ 3000 Kramer Ln",
        "avail_id": "b1e3f371-8538-4839-b940-d50673105ba4",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_7",
        "name": "ChargePoint @ 11500 N MoPac",
        "avail_id": "edfce375-5a14-4943-ac46-32c582963b3b",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_8",
        "name": "ChargePoint @ 11800 Alterra Pkwy",
        "avail_id": "3c75c68f-934b-40fa-926b-98d699cd7c47",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_9",
        "name": "ChargePoint @ 11920 Domain Dr",
        "avail_id": "91e497ef-bbb5-476b-885f-2d220ee9e4de",
        "region": "austin",
    },
    {
        "charger_id": "tomtom_domain_10",
        "name": "ChargePoint @ Domain Dr",
        "avail_id": "3f909562-f9b1-4377-890d-92265c55442c",
        "region": "austin",
    },
    # ── Katy TX area (field activation target) ──
    {
        "charger_id": "tomtom_katy_1",
        "name": "ChargePoint @ 23005 Katy Fwy",
        "avail_id": "2995cd98-7c1d-4076-a6ff-2a67408aac4c",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_2",
        "name": "ChargePoint @ 23414 W Fernhurst Dr",
        "avail_id": "2fc1a596-24a1-4150-8aa4-c8ba12ac6fd9",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_3",
        "name": "Premier at Katy @ Bella Dolce Ln",
        "avail_id": "dd0e0b0d-644e-4c7f-a4d1-d68534ef75b0",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_4",
        "name": "Memorial Hermann Katy Hospital",
        "avail_id": "d0c82ee8-8f00-4bc4-af80-591eda5cfa41",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_5",
        "name": "ChargePoint @ 107 New Hope Ln",
        "avail_id": "3d0ab5c6-a827-8878-98f2-a6bddc62d1e8",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_6",
        "name": "ChargePoint @ 1330 Park West Green Dr",
        "avail_id": "40733c29-cf08-4251-8e10-efdd127b74c9",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_7",
        "name": "ChargePoint @ 21001 Katy Fwy",
        "avail_id": "6a500a27-638f-4f91-8e4c-cf1758700f70",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_8",
        "name": "Vineyard Apts REVS @ Provincial Blvd",
        "avail_id": "97e8ad5a-7e2a-41a1-9be9-d959d316383a",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_9",
        "name": "ChargePoint @ 24932 Katy Ranch Rd",
        "avail_id": "1f3eb99d-899c-8d17-aaa8-e15541749770",
        "region": "katy",
    },
    {
        "charger_id": "tomtom_katy_10",
        "name": "Seacrest Apts @ Provincial Blvd",
        "avail_id": "14f75fda-0d64-816d-bb9b-74f24ac036a9",
        "region": "katy",
    },
    # ── Austin Downtown (diverse networks) ──
    {
        "charger_id": "tomtom_atx_downtown_1",
        "name": "301 E 8th St (non-CP)",
        "avail_id": "2fea4e5e-0755-8f50-811b-eb1db2e893fa",
        "region": "austin_downtown",
    },
    {
        "charger_id": "tomtom_atx_downtown_2",
        "name": "ChargePoint @ 701 Brazos St",
        "avail_id": "0d9640e0-9698-47ca-84f3-bb91f4fd1271",
        "region": "austin_downtown",
    },
    {
        "charger_id": "tomtom_atx_downtown_3",
        "name": "ChargePoint @ 710 Trinity St",
        "avail_id": "593c807e-a046-4221-8935-86993c46a4ae",
        "region": "austin_downtown",
    },
    {
        "charger_id": "tomtom_atx_downtown_4",
        "name": "ChargePoint @ 205 E 7th St",
        "avail_id": "8d059a15-9e93-445a-82a4-32bf3fc07cf0",
        "region": "austin_downtown",
    },
    {
        "charger_id": "tomtom_atx_downtown_5",
        "name": "ChargePoint @ 515 Congress Ave",
        "avail_id": "69a49afb-8aa5-4a7b-bb88-7fca6f762dbe",
        "region": "austin_downtown",
    },
]


async def _fetch_availability(avail_id: str) -> Optional[Dict[str, Any]]:
    """Fetch availability from TomTom for a single station."""
    import httpx

    url = f"https://api.tomtom.com/search/2/chargingAvailability.json?chargingAvailability={avail_id}&key={TOMTOM_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"[AvailCollector] TomTom returned {resp.status_code} for {avail_id}")
            return None
    except Exception as e:
        logger.error(f"[AvailCollector] Failed to fetch {avail_id}: {e}")
        return None


def _parse_availability(data: Dict[str, Any]) -> Dict[str, Any]:
    """Parse TomTom availability response into summary."""
    connectors = data.get("connectors", [])
    total = 0
    available = 0
    occupied = 0
    out_of_service = 0
    details = []

    for c in connectors:
        count = c.get("total", 0)
        current = c.get("availability", {}).get("current", {})
        a = current.get("available", 0)
        o = current.get("occupied", 0)
        oos = current.get("outOfService", 0)
        power_levels = c.get("availability", {}).get("perPowerLevel", [])

        total += count
        available += a
        occupied += o
        out_of_service += oos

        details.append(
            {
                "type": c.get("type"),
                "total": count,
                "available": a,
                "occupied": o,
                "out_of_service": oos,
                "power_kw": power_levels[0].get("powerKW") if power_levels else None,
            }
        )

    return {
        "total_ports": total,
        "available_ports": available,
        "occupied_ports": occupied,
        "out_of_service_ports": out_of_service,
        "connector_details": details,
    }


async def _collect_once():
    """Run one collection cycle for all monitored stations."""
    from app.db import SessionLocal
    from app.models.charger_availability import ChargerAvailabilitySnapshot

    if not TOMTOM_API_KEY:
        logger.warning("[AvailCollector] TOMTOM_API_KEY not set, skipping collection")
        return

    db = SessionLocal()
    collected = 0
    try:
        for station in MONITORED_STATIONS:
            data = await _fetch_availability(station["avail_id"])
            if not data:
                continue

            parsed = _parse_availability(data)
            snapshot = ChargerAvailabilitySnapshot(
                id=str(uuid.uuid4()),
                charger_id=station["charger_id"],
                tomtom_availability_id=station["avail_id"],
                source="tomtom",
                total_ports=parsed["total_ports"],
                available_ports=parsed["available_ports"],
                occupied_ports=parsed["occupied_ports"],
                out_of_service_ports=parsed["out_of_service_ports"],
                connector_details=parsed["connector_details"],
                recorded_at=datetime.utcnow(),
            )
            db.add(snapshot)
            collected += 1

            # Task 2-4: Field alert threshold check
            total = parsed["total_ports"]
            occupied = parsed["occupied_ports"]
            if total > 0:
                occupancy_pct = (occupied / total) * 100
                current_hour = datetime.utcnow().hour - 5  # Rough CT offset (UTC-5)
                if current_hour < 0:
                    current_hour += 24
                is_business = BUSINESS_HOURS[0] <= current_hour < BUSINESS_HOURS[1]

                if occupancy_pct >= FIELD_ALERT_THRESHOLD_PCT and is_business:
                    _log_field_alert(db, station, occupancy_pct, total, occupied)

        db.commit()
        logger.info(f"[AvailCollector] Collected {collected}/{len(MONITORED_STATIONS)} stations")
    except Exception as e:
        db.rollback()
        logger.error(f"[AvailCollector] Collection failed: {e}")
    finally:
        db.close()


def _log_field_alert(db, station: Dict[str, str], occupancy_pct: float, total: int, occupied: int):
    """Log a field alert when charger occupancy exceeds threshold during business hours."""
    from sqlalchemy import text

    charger_id = station["charger_id"]
    name = station["name"]
    region = station.get("region", "unknown")

    # Check if we already alerted for this charger in the last 30 minutes
    try:
        last_alert = db.execute(
            text(
                "SELECT recorded_at FROM charger_availability_snapshots "
                "WHERE charger_id = :cid AND occupied_ports * 100.0 / NULLIF(total_ports, 0) >= :threshold "
                "ORDER BY recorded_at DESC LIMIT 1"
            ),
            {"cid": charger_id, "threshold": FIELD_ALERT_THRESHOLD_PCT},
        ).fetchone()

        # Only log if this is a new spike (not already logged in last 30 min)
        if not last_alert or (datetime.utcnow() - last_alert[0]).total_seconds() > 1800:
            logger.info(
                f"[FieldAlert] {name} ({region}): {occupancy_pct:.0f}% occupied "
                f"({occupied}/{total} stalls in use)"
            )
    except Exception:
        pass  # Don't let alert logic crash the collector


def _slugify_site_name(name: str) -> str:
    """Convert 'Harker Heights, TX' → 'harker_heights_tx' for a stable charger_id."""
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


async def _collect_tesla_once():
    """
    Fetch Supercharger availability via Tesla Fleet API nearby_charging_sites.

    Uses the TeslaConnection belonging to TESLA_COLLECTOR_PHONE. Writes one
    ChargerAvailabilitySnapshot per supercharger in the response with
    source="tesla_fleet" and charger_id="tesla_sc_{slug}".

    Silently returns (without raising) if:
      - TESLA_COLLECTOR_PHONE is empty
      - No matching user
      - No active Tesla connection
      - Token refresh fails
      - Fleet API call fails
      - Response has no superchargers (vehicle out of Bell County)

    Designed to be safe to run alongside the TomTom collection — it catches
    its own errors so failure here never affects TomTom snapshots.
    """
    if not TESLA_COLLECTOR_PHONE:
        return

    from app.db import SessionLocal
    from app.models.charger_availability import ChargerAvailabilitySnapshot
    from app.models.tesla_connection import TeslaConnection
    from app.models.user import User
    from app.services.tesla_oauth import get_tesla_oauth_service, get_valid_access_token

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == TESLA_COLLECTOR_PHONE).first()
        if not user:
            logger.debug(f"[TeslaCollector] No user for phone {TESLA_COLLECTOR_PHONE}")
            return

        conn = (
            db.query(TeslaConnection)
            .filter(
                TeslaConnection.user_id == user.id,
                TeslaConnection.is_active == True,  # noqa: E712
                TeslaConnection.deleted_at.is_(None),
            )
            .first()
        )
        if not conn or not conn.vehicle_id:
            logger.debug(f"[TeslaCollector] No active Tesla connection for user {user.id}")
            return

        oauth = get_tesla_oauth_service()
        try:
            access_token = await get_valid_access_token(db, conn, oauth)
        except Exception as token_err:
            logger.warning(f"[TeslaCollector] Token refresh failed: {token_err}")
            return
        if not access_token:
            logger.warning("[TeslaCollector] No access token (connection revoked?)")
            return

        # Robust "fresh stall data" strategy:
        # Tesla's nearby_charging_sites endpoint sometimes returns the site
        # list with null available_stalls/total_stalls when the vehicle is
        # parked and idle (cached path). To force fresh data we need the
        # vehicle fully ONLINE, not just waking. Strategy:
        #   1. Call wake_vehicle (returns immediately with current state).
        #   2. Call vehicle_data which forces full online transition and
        #      blocks until the car responds with live telemetry.
        #   3. Immediately call nearby_charging_sites — Tesla will now return
        #      fresh stall counts.
        #   4. If the first response still has null stalls on ALL sites,
        #      wait 5s and retry once more.
        response = None
        raw_first_attempt = None
        for attempt in range(2):
            try:
                await oauth.wake_vehicle(access_token, conn.vehicle_id)
            except Exception as wake_err:
                logger.info(
                    f"[TeslaCollector] wake_vehicle non-fatal (attempt {attempt + 1}): {wake_err}"
                )

            # vehicle_data forces the car fully online. This is what the
            # driver-app polling does and it reliably primes nearby_charging_sites
            # to return live stall counts. Cheap relative to the dev credit
            # ($0.002/call → ~$0.19/day at 15-min cadence).
            try:
                await oauth.get_vehicle_data(access_token, conn.vehicle_id)
            except Exception as vd_err:
                logger.info(
                    f"[TeslaCollector] get_vehicle_data non-fatal (attempt {attempt + 1}): {vd_err}"
                )

            try:
                response = await oauth.get_nearby_charging_sites(access_token, conn.vehicle_id)
            except Exception as call_err:
                logger.warning(
                    f"[TeslaCollector] get_nearby_charging_sites failed "
                    f"(attempt {attempt + 1}): {call_err}"
                )
                response = None
                await asyncio.sleep(5)
                continue

            # Check whether the response contains ANY site with live stall data.
            # If yes → we're good. If no → retry once after a short delay.
            scs = response.get("superchargers") or []
            has_live = any(
                sc.get("total_stalls") is not None and sc.get("available_stalls") is not None
                for sc in scs
            )
            if attempt == 0:
                raw_first_attempt = {
                    "site_count": len(scs),
                    "has_live": has_live,
                    "sample": scs[0] if scs else None,
                }
            if has_live:
                if attempt == 1:
                    logger.info(
                        "[TeslaCollector] Recovered live stall data on retry "
                        f"(first attempt had {len(scs)} sites but no stall data)"
                    )
                break
            # No live data — retry with a wait for the car to fully come online
            if attempt == 0:
                logger.info(
                    f"[TeslaCollector] First attempt returned {len(scs)} site(s) "
                    f"with no stall data; retrying in 5s"
                )
                await asyncio.sleep(5)

        if response is None:
            logger.warning("[TeslaCollector] All attempts failed — no response")
            return

        superchargers = response.get("superchargers") or []
        if not superchargers:
            logger.info(
                "[TeslaCollector] Empty superchargers array — "
                "vehicle may be outside Tesla's nearby radius"
            )
            return

        stored = 0
        skipped_no_stalls = 0
        for sc in superchargers:
            name = sc.get("name") or ""
            total = sc.get("total_stalls")
            available = sc.get("available_stalls")
            # Skip entries without live stall data (Tesla returns null when
            # the vehicle has been parked too long and the cached path kicks in).
            if total is None or available is None:
                skipped_no_stalls += 1
                continue
            occupied = max(0, int(total) - int(available))
            slug = _slugify_site_name(name)
            if not slug:
                loc = sc.get("location") or {}
                slug = f"{loc.get('lat','?')}_{loc.get('long','?')}"
            charger_id = f"tesla_sc_{slug}"

            snapshot = ChargerAvailabilitySnapshot(
                id=str(uuid.uuid4()),
                charger_id=charger_id,
                tomtom_availability_id=None,
                source="tesla_fleet",
                total_ports=int(total),
                available_ports=int(available),
                occupied_ports=occupied,
                out_of_service_ports=0,  # Tesla does not report OOS stalls
                connector_details=sc,  # Full raw supercharger dict
                recorded_at=datetime.utcnow(),
            )
            db.add(snapshot)
            stored += 1

        db.commit()
        logger.info(
            f"[TeslaCollector] Stored {stored} supercharger snapshot(s), "
            f"skipped {skipped_no_stalls} with null stalls "
            f"(vehicle_id={conn.vehicle_id})"
        )
        # On cycles that stored zero after retry, dump the first attempt for
        # diagnosis so we can see exactly what Tesla returned.
        if stored == 0 and raw_first_attempt is not None:
            logger.warning(f"[TeslaCollector] Cycle stored 0 — first_attempt={raw_first_attempt}")
    except Exception as e:
        db.rollback()
        logger.error(f"[TeslaCollector] Collection failed: {e}")
    finally:
        db.close()


async def run_collector():
    """Main loop: collect availability every 15 minutes."""
    logger.info(
        f"[AvailCollector] Starting — monitoring {len(MONITORED_STATIONS)} TomTom "
        f"stations + Tesla Fleet (phone={TESLA_COLLECTOR_PHONE or 'disabled'}) "
        f"every {POLL_INTERVAL_SECONDS}s"
    )
    while True:
        # TomTom path
        try:
            await _collect_once()
        except Exception as e:
            logger.error(f"[AvailCollector] TomTom path error: {e}")
        # Tesla Fleet path — independent, isolated from TomTom errors
        try:
            await _collect_tesla_once()
        except Exception as e:
            logger.error(f"[AvailCollector] Tesla path error: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
