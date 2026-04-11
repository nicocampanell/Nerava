from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.services.geo import haversine_m
from app.utils.log import get_logger

logger = get_logger(__name__)


def _has_table(db: Session, name: str) -> bool:
    try:
        res = db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}
        ).first()
        if res:
            return True
    except Exception:
        pass
    # Fallback via inspector (may fail on sqlite URLs outside context)
    try:
        from sqlalchemy import inspect

        return name in inspect(db.bind).get_table_names()
    except Exception:
        return False


def _load_event(db: Session, event_id: int) -> Optional[Dict[str, Any]]:
    try:
        row = (
            db.execute(
                text("SELECT id, title, lat, lng, radius_m FROM events2 WHERE id=:id"),
                {"id": event_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
    except Exception as e:
        logger.warning({"at": "verify", "step": "load_event", "err": str(e)})
        return None


def _nearest_charger(db: Session, lat: float, lng: float) -> Optional[Dict[str, Any]]:
    try:
        if not _has_table(db, "chargers_openmap"):
            return None
        r = (
            db.execute(
                text(
                    "SELECT id, name, lat, lng FROM chargers_openmap WHERE ABS(lat-:lat)<0.1 AND ABS(lng-:lng)<0.1 ORDER BY ((lat-:lat)*(lat-:lat) + (lng-:lng)*(lng-:lng)) ASC LIMIT 1"
                ),
                {"lat": lat, "lng": lng},
            )
            .mappings()
            .first()
        )
        return dict(r) if r else None
    except Exception as e:
        logger.info({"at": "verify", "step": "nearest_charger", "err": str(e)})
        return None


def _nearest_merchant(db: Session, lat: float, lng: float) -> Optional[Dict[str, Any]]:
    try:
        if not _has_table(db, "merchants"):
            return None
        r = (
            db.execute(
                text(
                    "SELECT id, name, lat, lng FROM merchants WHERE ABS(lat-:lat)<0.1 AND ABS(lng-:lng)<0.1 ORDER BY ((lat-:lat)*(lat-:lat) + (lng-:lng)*(lng-:lng)) ASC LIMIT 1"
                ),
                {"lat": lat, "lng": lng},
            )
            .mappings()
            .first()
        )
        return dict(r) if r else None
    except Exception as e:
        logger.info({"at": "verify", "step": "nearest_merchant", "err": str(e)})
        return None


def _get_session_hub_id(db: Session, session_id: str) -> Optional[str]:
    """Get hub_id from session (either from hub_id column, meta JSON, or detect from target_id)."""
    try:
        # Try hub_id column first (if it exists)
        try:
            result = db.execute(
                text("SELECT hub_id FROM sessions WHERE id=:sid"), {"sid": session_id}
            ).first()
            if result and result[0]:
                return str(result[0])
        except Exception:
            pass

        # Try meta JSON (if column exists)
        try:
            result = db.execute(
                text("SELECT meta FROM sessions WHERE id=:sid"), {"sid": session_id}
            ).first()
            if result and result[0]:
                import json

                if isinstance(result[0], str):
                    meta = json.loads(result[0])
                else:
                    meta = result[0]
                if isinstance(meta, dict) and "hub_id" in meta:
                    return str(meta["hub_id"])
        except Exception:
            pass

        # Fallback: detect Domain hub from target_id (Domain chargers/merchants)
        result = db.execute(
            text("SELECT target_id FROM sessions WHERE id=:sid"), {"sid": session_id}
        ).first()
        if result and result[0]:
            target_id = str(result[0])
            try:
                from app.domains.domain_hub import DOMAIN_CHARGERS

                charger_ids = [ch["id"] for ch in DOMAIN_CHARGERS]
                if target_id in charger_ids:
                    return "domain"
            except Exception:
                pass
    except Exception:
        pass
    return None


def _get_domain_radius(target_type: str, target_id: str, default_radius: int) -> int:
    """Get domain-specific radius for target, falling back to default."""
    try:
        from app.domains.domain_verification import get_charger_radius, get_merchant_radius

        if target_type == "charger":
            return get_charger_radius(target_id)
        elif target_type == "merchant":
            return get_merchant_radius(target_id)
    except Exception:
        pass
    return default_radius


def _choose_target(
    db: Session, lat: float, lng: float, event_id: Optional[int], hub_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if event_id:
        ev = _load_event(db, event_id)
        if ev:
            ev["target_type"] = "event"
            ev["target_id"] = str(ev["id"])
            ev["target_name"] = ev.get("title") or "Event"
            ev["radius_m"] = int(ev.get("radius_m") or settings.verify_default_radius_m)
            return ev
    ch = _nearest_charger(db, lat, lng)
    if ch:
        ch["target_type"] = "charger"
        ch["target_id"] = str(ch["id"])
        ch["target_name"] = ch.get("name") or "Charger"
        default_radius = settings.verify_default_radius_m
        if hub_id == "domain":
            ch["radius_m"] = _get_domain_radius("charger", ch["target_id"], default_radius)
        else:
            ch["radius_m"] = default_radius
        # verify distance within 150m
        if haversine_m(lat, lng, ch["lat"], ch["lng"]) <= 150:
            return ch
    m = _nearest_merchant(db, lat, lng)
    if m:
        m["target_type"] = "merchant"
        m["target_id"] = str(m["id"])
        m["target_name"] = m.get("name") or "Merchant"
        default_radius = settings.verify_default_radius_m
        if hub_id == "domain":
            m["radius_m"] = _get_domain_radius("merchant", m["target_id"], default_radius)
        else:
            m["radius_m"] = default_radius
        if haversine_m(lat, lng, m["lat"], m["lng"]) <= 150:
            return m
    return None


def _record_ping_history(db: Session, session_id: str, lat: float, lng: float, ts: datetime):
    """Record ping in session meta for drift calculation."""
    try:
        # Try to get/create meta column - if it doesn't exist, skip
        try:
            # Get existing meta
            result = db.execute(
                text("SELECT meta FROM sessions WHERE id=:sid"), {"sid": session_id}
            ).first()
            meta = {}
            if result and result[0]:
                import json

                if isinstance(result[0], str):
                    meta = json.loads(result[0])
                else:
                    meta = result[0] if isinstance(result[0], dict) else {}

            # Initialize ping_history if not exists
            if "ping_history" not in meta:
                meta["ping_history"] = []

            # Add new ping (keep last 10 for drift calculation)
            meta["ping_history"].append({"lat": lat, "lng": lng, "ts": ts.isoformat()})
            if len(meta["ping_history"]) > 10:
                meta["ping_history"] = meta["ping_history"][-10:]

            # Update meta
            import json

            db.execute(
                text(
                    """
                UPDATE sessions SET meta = :meta
                WHERE id = :sid
            """
                ),
                {
                    "meta": json.dumps(meta) if isinstance(meta, dict) else str(meta),
                    "sid": session_id,
                },
            )
        except Exception:
            # Meta column doesn't exist, skip ping history recording
            pass
    except Exception as e:
        logger.debug(f"Could not record ping history: {str(e)}")


def _calculate_drift_penalty(
    db: Session, session_id: str, lat: float, lng: float, ts: datetime
) -> Dict[str, Any]:
    """Calculate drift penalty based on recent ping history."""
    try:
        from app.domains.domain_verification import (
            DOMAIN_DRIFT_TOLERANCE_M,
            DOMAIN_DRIFT_WINDOW_S,
            MAX_DRIFT_PENALTY,
            SCORE_DRIFT_PENALTY_PER_M,
        )

        # Get ping history from meta (if column exists)
        try:
            result = db.execute(
                text("SELECT meta FROM sessions WHERE id=:sid"), {"sid": session_id}
            ).first()
            if not result or not result[0]:
                return {"penalty": 0, "drift_m": 0}

            import json

            if isinstance(result[0], str):
                meta = json.loads(result[0])
            else:
                meta = result[0] if isinstance(result[0], dict) else {}

            ping_history = meta.get("ping_history", [])
        except Exception:
            # Fallback: use last_lat/last_lng from session if meta doesn't exist
            result = db.execute(
                text("SELECT last_lat, last_lng, updated_at FROM sessions WHERE id=:sid"),
                {"sid": session_id},
            ).first()
            if not result or not result[0] or not result[1]:
                return {"penalty": 0, "drift_m": 0}

            last_lat = result[0]
            last_lng = result[1]
            last_ts = result[2]

            if not last_lat or not last_lng:
                return {"penalty": 0, "drift_m": 0}

            # Calculate time delta
            # Try to parse last_ts if it's a string
            if isinstance(last_ts, str):
                try:
                    # Try ISO format
                    if "T" in last_ts:
                        last_ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                    else:
                        # SQLite format
                        last_ts = datetime.strptime(last_ts[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    # If parsing fails, use current time as fallback (won't match window, so no drift)
                    last_ts = ts

            if not isinstance(last_ts, datetime):
                return {"penalty": 0, "drift_m": 0}

            delta = (ts - last_ts).total_seconds()
            if delta <= 0 or delta > DOMAIN_DRIFT_WINDOW_S:
                return {"penalty": 0, "drift_m": 0}

            drift_m = haversine_m(lat, lng, last_lat, last_lng)
        else:
            # Use ping_history from meta
            if len(ping_history) < 2:
                return {"penalty": 0, "drift_m": 0}

            # Find most recent ping within drift window
            recent_pings = []
            for ping in ping_history:
                ping_ts = (
                    datetime.fromisoformat(ping["ts"])
                    if isinstance(ping["ts"], str)
                    else ping["ts"]
                )
                delta = (ts - ping_ts).total_seconds()
                if 0 < delta <= DOMAIN_DRIFT_WINDOW_S:
                    recent_pings.append(ping)

            if not recent_pings:
                return {"penalty": 0, "drift_m": 0}

            # Calculate drift from most recent ping
            latest = recent_pings[-1]
            drift_m = haversine_m(lat, lng, latest["lat"], latest["lng"])

        if drift_m > DOMAIN_DRIFT_TOLERANCE_M:
            excess_drift = drift_m - DOMAIN_DRIFT_TOLERANCE_M
            penalty = min(MAX_DRIFT_PENALTY, int(excess_drift * SCORE_DRIFT_PENALTY_PER_M))
            return {"penalty": penalty, "drift_m": drift_m}

        return {"penalty": 0, "drift_m": drift_m}
    except Exception as e:
        logger.debug(f"Could not calculate drift: {str(e)}")
        return {"penalty": 0, "drift_m": 0}


def _calculate_verification_score(
    distance_m: float,
    radius_m: float,
    dwell_seconds: int,
    dwell_required_s: int,
    drift_penalty: int,
    accuracy_m: float,
    min_accuracy_m: int,
    hub_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate verification score with penalties."""
    try:
        from app.domains.domain_verification import (
            DOMAIN_DWELL_OPTIMAL_S,
            MAX_ACCURACY_PENALTY,
            MAX_DISTANCE_PENALTY,
            MAX_DWELL_PENALTY,
            SCORE_ACCURACY_PENALTY_PER_M,
            SCORE_DISTANCE_PENALTY_PER_M,
            SCORE_DWELL_PENALTY_PER_S,
            VERIFICATION_BASE_SCORE,
        )
    except Exception:
        # Fallback to defaults if domain verification not available
        VERIFICATION_BASE_SCORE = 100
        SCORE_DISTANCE_PENALTY_PER_M = 2
        MAX_DISTANCE_PENALTY = 50
        SCORE_DWELL_PENALTY_PER_S = 1
        MAX_DWELL_PENALTY = 30
        DOMAIN_DWELL_OPTIMAL_S = 120
        MAX_ACCURACY_PENALTY = 20
        SCORE_ACCURACY_PENALTY_PER_M = 1

    score = VERIFICATION_BASE_SCORE
    components = {}

    # Distance penalty
    if distance_m > radius_m:
        excess_distance = distance_m - radius_m
        distance_penalty = min(
            MAX_DISTANCE_PENALTY, int(excess_distance * SCORE_DISTANCE_PENALTY_PER_M)
        )
        score -= distance_penalty
        components["distance_penalty"] = distance_penalty
    else:
        components["distance_penalty"] = 0

    # Dwell penalty (only for Domain hub)
    if hub_id == "domain":
        if dwell_seconds < DOMAIN_DWELL_OPTIMAL_S:
            dwell_deficit = DOMAIN_DWELL_OPTIMAL_S - dwell_seconds
            dwell_penalty = min(MAX_DWELL_PENALTY, int(dwell_deficit * SCORE_DWELL_PENALTY_PER_S))
            score -= dwell_penalty
            components["dwell_penalty"] = dwell_penalty
        else:
            components["dwell_penalty"] = 0
    else:
        components["dwell_penalty"] = 0

    # Drift penalty (already calculated)
    score -= drift_penalty
    components["drift_penalty"] = drift_penalty

    # Accuracy penalty
    if accuracy_m > min_accuracy_m:
        excess_accuracy = accuracy_m - min_accuracy_m
        accuracy_penalty = min(
            MAX_ACCURACY_PENALTY, int(excess_accuracy * SCORE_ACCURACY_PENALTY_PER_M)
        )
        score -= accuracy_penalty
        components["accuracy_penalty"] = accuracy_penalty
    else:
        components["accuracy_penalty"] = 0

    # Ensure score is between 0 and 100
    score = max(0, min(100, score))

    return {"verification_score": score, "score_components": components}


def start_session(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    lat: float,
    lng: float,
    accuracy_m: float,
    ua: str,
    event_id: Optional[int] = None,
) -> Dict[str, Any]:
    try:
        has_events2 = _has_table(db, "events2")
        has_chargers = _has_table(db, "chargers_openmap")
        has_merchants = _has_table(db, "merchants")
        reason = None

        # Get hub_id for session
        hub_id = _get_session_hub_id(db, session_id)

        try:
            target = _choose_target(db, lat, lng, event_id, hub_id)
        except Exception as e:
            logger.error(
                {
                    "at": "verify",
                    "step": "start_choose",
                    "uid": user_id,
                    "sid": session_id,
                    "err": str(e),
                }
            )
            target = None
            reason = "select_error"

        min_acc = settings.verify_min_accuracy_m
        dwell_req = settings.verify_dwell_required_s

        # Use domain-specific dwell requirement if Domain hub
        if hub_id == "domain":
            try:
                from app.domains.domain_verification import get_dwell_required

                dwell_req = get_dwell_required()
            except Exception:
                pass

        radius_m = int(target.get("radius_m") if target else settings.verify_default_radius_m)

        # Idempotent baseline init (even without target)
        try:
            db.execute(
                text(
                    """
                UPDATE sessions SET
                    target_type = COALESCE(target_type, :tt),
                    target_id = COALESCE(target_id, :ti),
                    target_name = COALESCE(target_name, :tn),
                    radius_m = COALESCE(radius_m, :rm),
                    started_lat = COALESCE(started_lat, :slat),
                    started_lng = COALESCE(started_lng, :slng),
                    last_lat = :llat,
                    last_lng = :llng,
                    last_accuracy_m = :acc,
                    min_accuracy_m = COALESCE(min_accuracy_m, :minacc),
                    dwell_required_s = COALESCE(dwell_required_s, :dreq),
                    ping_count = COALESCE(ping_count, 0),
                    dwell_seconds = COALESCE(dwell_seconds, 0),
                    status = CASE WHEN status IN ('pending','started') THEN 'active' ELSE status END,
                    ua = COALESCE(ua, :ua)
                WHERE id = :sid
            """
                ),
                {
                    "tt": (
                        target["target_type"]
                        if target
                        else ("unknown" if settings.verify_allow_start_without_target else None)
                    ),
                    "ti": target["target_id"] if target else None,
                    "tn": target["target_name"] if target else None,
                    "rm": radius_m,
                    "slat": lat,
                    "slng": lng,
                    "llat": lat,
                    "llng": lng,
                    "acc": accuracy_m,
                    "minacc": min_acc,
                    "dreq": dwell_req,
                    "ua": ua,
                    "sid": session_id,
                },
            )
            db.commit()
        except Exception as e:
            logger.error(
                {
                    "at": "verify",
                    "step": "start_update",
                    "uid": user_id,
                    "sid": session_id,
                    "err": str(e),
                }
            )

        payload_base = {
            "at": "verify",
            "step": "start",
            "uid": user_id,
            "sid": session_id,
            "has_events2": has_events2,
            "has_chargers": has_chargers,
            "has_merchants": has_merchants,
        }

        if not target:
            logger.info(
                {
                    **payload_base,
                    "ok": True if settings.verify_allow_start_without_target else False,
                    "reason": reason or "no_target",
                }
            )
            if settings.verify_allow_start_without_target:
                return {
                    "ok": True,
                    "session_id": session_id,
                    "reason": "no_target",
                    "hint": "Stay put; target will be acquired on first ping.",
                    "status": "started",
                    "dwell_required_s": dwell_req,
                    "min_accuracy_m": min_acc,
                }
            return {
                "ok": False,
                "reason": reason or "no_target",
                "hint": "Try moving 150m closer or widen radius.",
                "status": "start_failed",
                "session_id": session_id,
                "dwell_required_s": dwell_req,
                "min_accuracy_m": min_acc,
            }

        logger.info({**payload_base, "ok": True, "target_type": target.get("target_type")})
        return {
            "ok": True,
            "session_id": session_id,
            "target": {
                "type": target["target_type"],
                "id": target["target_id"],
                "name": target["target_name"],
                "lat": target["lat"],
                "lng": target["lng"],
                "radius_m": radius_m,
            },
            "status": "started",
            "dwell_required_s": dwell_req,
            "min_accuracy_m": min_acc,
        }
    except Exception as e:
        logger.error(
            {"at": "verify", "step": "start", "ok": False, "sid": session_id, "exc": repr(e)}
        )
        return {
            "ok": False,
            "reason": "internal_error",
            "hint": "Use ping to continue; start will self-heal.",
            "status": "start_failed",
        }


def _load_target_coords(db: Session, row, hub_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load target coordinates with hub-specific radius."""
    ttype = row["target_type"]
    tid = row["target_id"]
    if not ttype or not tid:
        return None

    base_radius = int(row["radius_m"] or settings.verify_default_radius_m)

    # Override with domain-specific radius if Domain hub
    if hub_id == "domain":
        base_radius = _get_domain_radius(ttype, tid, base_radius)

    if ttype == "event":
        ev = _load_event(db, int(tid))
        if not ev:
            return None
        return {"lat": ev["lat"], "lng": ev["lng"], "radius_m": base_radius}
    if ttype == "charger":
        r = db.execute(
            text("SELECT lat, lng FROM chargers_openmap WHERE id=:id"), {"id": tid}
        ).first()
        if not r:
            # Try chargers table as fallback
            r = db.execute(text("SELECT lat, lng FROM chargers WHERE id=:id"), {"id": tid}).first()
            if not r:
                return None
        return {"lat": float(r[0]), "lng": float(r[1]), "radius_m": base_radius}
    if ttype == "merchant":
        r = db.execute(text("SELECT lat, lng FROM merchants WHERE id=:id"), {"id": tid}).first()
        if not r:
            return None
        return {"lat": float(r[0]), "lng": float(r[1]), "radius_m": base_radius}
    return None


def ping(
    db: Session,
    *,
    session_id: str,
    lat: float,
    lng: float,
    accuracy_m: float,
    ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = ts or datetime.utcnow()
    row = (
        db.execute(text("SELECT * FROM sessions WHERE id=:sid"), {"sid": session_id})
        .mappings()
        .first()
    )
    if not row:
        return {"ok": False, "reason": "not_found"}
    if row["status"] == "verified":
        return {"ok": True, "verified": True, "idempotent": True}

    # Get hub_id for domain-specific logic
    hub_id = _get_session_hub_id(db, session_id)

    # Accuracy gate
    min_acc = int(row.get("min_accuracy_m") or settings.verify_min_accuracy_m)
    if accuracy_m > min_acc:
        # Update last seen but do not accrue
        db.execute(
            text(
                """
            UPDATE sessions SET last_lat=:llat, last_lng=:llng, last_accuracy_m=:acc, ping_count=COALESCE(ping_count,0)+1
            WHERE id=:sid
        """
            ),
            {"llat": lat, "llng": lng, "acc": accuracy_m, "sid": session_id},
        )
        db.commit()

        # Calculate score even if accuracy is poor
        score_result = _calculate_verification_score(
            distance_m=999,  # Unknown distance
            radius_m=0,
            dwell_seconds=row.get("dwell_seconds", 0),
            dwell_required_s=row.get("dwell_required_s", 300),
            drift_penalty=0,
            accuracy_m=accuracy_m,
            min_accuracy_m=min_acc,
            hub_id=hub_id,
        )

        return {
            "ok": True,
            "verified": False,
            "reason": "accuracy",
            "accuracy_m": accuracy_m,
            "min_accuracy_m": min_acc,
            "ping_count": int((row.get("ping_count") or 0) + 1),
            **score_result,
        }

    # Record ping for drift calculation
    _record_ping_history(db, session_id, lat, lng, now)

    # Calculate drift penalty
    drift_result = (
        _calculate_drift_penalty(db, session_id, lat, lng, now)
        if hub_id == "domain"
        else {"penalty": 0, "drift_m": 0}
    )

    target = _load_target_coords(db, row, hub_id)
    # Self-heal: if no target yet, try to select once
    if not target:
        try:
            sel = _choose_target(db, lat, lng, None, hub_id)
        except Exception as e:
            sel = None
            logger.info({"at": "verify", "step": "ping_choose", "sid": session_id, "err": str(e)})
        if sel:
            try:
                radius_m = sel.get("radius_m") or settings.verify_default_radius_m
                db.execute(
                    text(
                        """
                    UPDATE sessions SET target_type=:tt, target_id=:ti, target_name=:tn, radius_m=COALESCE(radius_m,:rm)
                    WHERE id=:sid AND (target_type IS NULL OR target_id IS NULL)
                """
                    ),
                    {
                        "tt": sel["target_type"],
                        "ti": sel["target_id"],
                        "tn": sel["target_name"],
                        "rm": int(radius_m),
                        "sid": session_id,
                    },
                )
                db.commit()
                target = {"lat": sel["lat"], "lng": sel["lng"], "radius_m": int(radius_m)}
                acquired = True
            except Exception as e:
                logger.info(
                    {"at": "verify", "step": "ping_update_target", "sid": session_id, "err": str(e)}
                )
                acquired = False
        else:
            acquired = False
    if not target:
        # No target yet; do not accrue
        db.execute(
            text(
                """
            UPDATE sessions SET last_lat=:llat, last_lng=:llng, last_accuracy_m=:acc, ping_count=COALESCE(ping_count,0)+1
            WHERE id=:sid
        """
            ),
            {"llat": lat, "llng": lng, "acc": accuracy_m, "sid": session_id},
        )
        db.commit()
        return {
            "ok": True,
            "verified": False,
            "reason": "no_target",
            "ping_count": int((row.get("ping_count") or 0) + 1),
        }

    distance_m = haversine_m(lat, lng, float(target["lat"]), float(target["lng"]))
    accrue = 0
    if distance_m <= float(target["radius_m"]):
        # Use server-time step with cap
        last_ts = row.get("updated_at")  # may not exist; fallback to 5s step
        step = settings.verify_ping_max_step_s
        accrue = step
    new_dwell = int(row.get("dwell_seconds") or 0) + int(accrue)

    dwell_required_s = row.get("dwell_required_s") or settings.verify_dwell_required_s
    if hub_id == "domain":
        try:
            from app.domains.domain_verification import get_dwell_required

            dwell_required_s = get_dwell_required()
        except Exception:
            pass

    # Calculate verification score
    score_result = _calculate_verification_score(
        distance_m=distance_m,
        radius_m=target["radius_m"],
        dwell_seconds=new_dwell,
        dwell_required_s=dwell_required_s,
        drift_penalty=drift_result["penalty"],
        accuracy_m=accuracy_m,
        min_accuracy_m=min_acc,
        hub_id=hub_id,
    )

    db.execute(
        text(
            """
        UPDATE sessions SET
            last_lat=:llat, last_lng=:llng, last_accuracy_m=:acc,
            ping_count=COALESCE(ping_count,0)+1,
            dwell_seconds=:dwell,
            status=CASE WHEN :dwell >= :req THEN 'verified' ELSE status END
        WHERE id=:sid
    """
        ),
        {
            "llat": lat,
            "llng": lng,
            "acc": accuracy_m,
            "sid": session_id,
            "dwell": new_dwell,
            "req": dwell_required_s,
        },
    )
    db.commit()

    if new_dwell >= dwell_required_s:
        # Call reward logic (idempotent)
        rewarded = False
        wallet_delta = 0
        pool_delta = 0
        try:
            from app.services.rewards import award_verify_bonus

            ar = award_verify_bonus(
                db,
                user_id=int(row.get("user_id") or 0),
                session_id=session_id,
                amount=int(getattr(settings, "verify_reward_cents", 200)),
                now=now,
            )
            rewarded = bool(ar.get("awarded"))
            wallet_delta = int(ar.get("user_delta") or 0)
            pool_delta = int(ar.get("pool_delta") or 0)
            logger.info(
                {
                    "at": "verify",
                    "step": "reward",
                    "sid": session_id,
                    "uid": int(row.get("user_id") or 0),
                    "gross": int(getattr(settings, "verify_reward_cents", 200)),
                    "net": wallet_delta,
                    "pool": pool_delta,
                    "ok": True,
                }
            )
        except Exception as e:
            logger.info({"at": "verify", "step": "reward", "sid": session_id, "err": str(e)})

        response = {
            "ok": True,
            "verified": True,
            "rewarded": rewarded,
            "reward_cents": int(getattr(settings, "verify_reward_cents", 200)),
            "wallet_delta_cents": wallet_delta,
            "pool_delta_cents": pool_delta,
            "dwell_seconds": new_dwell,
            "ping_count": int((row.get("ping_count") or 0) + 1),
            "distance_m": round(distance_m, 1),
            "radius_m": target["radius_m"],
        }
        response.update(score_result)
        if drift_result.get("drift_m"):
            response["drift_m"] = round(drift_result["drift_m"], 1)
        return response

    resp = {
        "ok": True,
        "verified": False,
        "dwell_seconds": new_dwell,
        "distance_m": round(distance_m, 1),
        "radius_m": target["radius_m"],
        "needed_seconds": dwell_required_s - new_dwell,
        "accuracy_m": accuracy_m,
        "ping_count": int((row.get("ping_count") or 0) + 1),
    }
    resp.update(score_result)
    if drift_result.get("drift_m"):
        resp["drift_m"] = round(drift_result["drift_m"], 1)
    if locals().get("acquired"):
        resp["target_acquired"] = True
    return resp
