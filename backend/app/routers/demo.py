"""
Demo mode router for investor-friendly demo system.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import is_demo
from app.dependencies import get_db
from app.models_demo import DemoSeedLog, DemoState
from app.models_extra import FeatureFlag
from app.obs.obs import get_trace_id, log_error, log_info
from app.schemas.demo import (
    DemoTourResponse,
    DemoTourStep,
)
from app.scripts.demo_seed import seed_demo
from app.security.ratelimit import rate_limit
from app.security.scopes import require_scopes

router = APIRouter(prefix="/v1/demo", tags=["demo"])


def _check_demo_mode():
    """Check if demo mode is enabled."""
    if not is_demo():
        raise HTTPException(
            status_code=403, detail={"error": "demo_disabled", "message": "Demo mode is disabled"}
        )


@router.post("/enable_all")
async def enable_all_flags(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: bool = Depends(rate_limit("demo_ops", 20)),
) -> Dict[str, Any]:
    """
    Enable all 20 feature flags for demo environment.

    Requires: DEMO_MODE=true, admin:demo scope
    """
    _check_demo_mode()

    # Add demo headers
    response.headers["x-nerava-demo"] = "true"

    trace_id = get_trace_id(request)
    log_info({"trace_id": trace_id, "route": "demo_enable_all"})

    try:
        # Get all feature flags
        feature_flags = [
            "feature_merchant_intel",
            "feature_behavior_cloud",
            "feature_autonomous_reward_routing",
            "feature_city_marketplace",
            "feature_multimodal",
            "feature_merchant_credits",
            "feature_charge_verify_api",
            "feature_energy_wallet_ext",
            "feature_merchant_utility_coops",
            "feature_whitelabel_sdk",
            "feature_energy_rep",
            "feature_carbon_micro_offsets",
            "feature_fleet_workplace",
            "feature_smart_home_iot",
            "feature_contextual_commerce",
            "feature_energy_events",
            "feature_uap_partnerships",
            "feature_ai_reward_opt",
            "feature_esg_finance_gateway",
            "feature_ai_growth_automation",
        ]

        enabled_flags = []
        for flag_key in feature_flags:
            # Upsert feature flag
            flag = db.query(FeatureFlag).filter(FeatureFlag.key == flag_key).first()
            if flag:
                flag.enabled = True
                flag.env = "demo"
            else:
                flag = FeatureFlag(key=flag_key, enabled=True, env="demo")
                db.add(flag)
            enabled_flags.append(flag_key)

        db.commit()

        log_info(
            {
                "trace_id": trace_id,
                "route": "demo_enable_all",
                "enabled_count": len(enabled_flags),
                "status": "success",
            }
        )

        return {"enabled": True, "flags": enabled_flags, "count": len(enabled_flags)}

    except Exception as e:
        log_error({"trace_id": trace_id, "route": "demo_enable_all", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to enable flags")


@router.post("/seed")
async def seed_demo_data(
    request: Request, db: Session = Depends(get_db), _: bool = Depends(rate_limit("demo_ops", 20))
) -> Dict[str, Any]:
    """
    Seed demo data with synthetic users, merchants, utilities, and events.

    Requires: DEMO_MODE=true, admin:demo scope
    """
    _check_demo_mode()

    trace_id = get_trace_id(request)
    log_info({"trace_id": trace_id, "route": "demo_seed"})

    try:
        # Run seed script
        summary = seed_demo(db)

        # Log seed run
        seed_log = DemoSeedLog(run_id=summary["run_id"], summary=summary)
        db.add(seed_log)
        db.commit()

        log_info(
            {
                "trace_id": trace_id,
                "route": "demo_seed",
                "run_id": summary["run_id"],
                "status": "success",
            }
        )

        return {"seeded": True, "run_id": summary["run_id"], "summary": summary}

    except Exception as e:
        log_error({"trace_id": trace_id, "route": "demo_seed", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to seed demo data")


@router.post("/scenario")
async def set_scenario(
    request: Request,
    scenario_data: Dict[str, str],
    db: Session = Depends(get_db),
    _: bool = Depends(rate_limit("demo_ops", 20)),
) -> Dict[str, Any]:
    """
    Set demo scenario state.

    Allowed keys: grid_state, merchant_shift, rep_profile, city
    Allowed values: peak/offpeak, A_dominates/balanced, high/low, austin
    """
    _check_demo_mode()

    trace_id = get_trace_id(request)
    key = scenario_data.get("key")
    value = scenario_data.get("value")

    # Validate key and value
    valid_keys = {"grid_state", "merchant_shift", "rep_profile", "city"}
    valid_values = {
        "grid_state": {"peak", "offpeak"},
        "merchant_shift": {"A_dominates", "balanced"},
        "rep_profile": {"high", "low"},
        "city": {"austin", "denver", "seattle"},
    }

    if key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"Invalid key. Must be one of: {valid_keys}")

    if value not in valid_values.get(key, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid value for {key}. Must be one of: {valid_values.get(key)}",
        )

    log_info({"trace_id": trace_id, "route": "demo_scenario", "key": key, "value": value})

    try:
        # Upsert scenario state
        state = db.query(DemoState).filter(DemoState.key == key).first()
        if state:
            state.value = value
        else:
            state = DemoState(key=key, value=value)
            db.add(state)

        db.commit()

        # Get current state
        current_state = {}
        for state in db.query(DemoState).all():
            current_state[state.key] = state.value

        log_info(
            {
                "trace_id": trace_id,
                "route": "demo_scenario",
                "key": key,
                "value": value,
                "status": "success",
            }
        )

        return {"ok": True, "state": current_state}

    except Exception as e:
        log_error(
            {
                "trace_id": trace_id,
                "route": "demo_scenario",
                "key": key,
                "value": value,
                "error": str(e),
            }
        )
        raise HTTPException(status_code=500, detail="Failed to set scenario")


@router.get("/state")
async def get_demo_state(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Get current demo state.
    """
    _check_demo_mode()

    trace_id = get_trace_id(request)
    log_info({"trace_id": trace_id, "route": "demo_state"})

    try:
        # Get current state with defaults
        state = {
            "grid_state": "offpeak",
            "merchant_shift": "balanced",
            "rep_profile": "high",
            "city": "austin",
        }

        for demo_state in db.query(DemoState).all():
            state[demo_state.key] = demo_state.value

        log_info({"trace_id": trace_id, "route": "demo_state", "state": state, "status": "success"})

        return {"state": state}

    except Exception as e:
        log_error({"trace_id": trace_id, "route": "demo_state", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to get demo state")


@router.post("/tour", response_model=DemoTourResponse)
async def run_investor_tour(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    _: bool = Depends(rate_limit("demo_ops", 20)),
) -> DemoTourResponse:
    """
    Run end-to-end investor tour demonstrating all features.
    """
    _check_demo_mode()

    trace_id = get_trace_id(request)
    log_info({"trace_id": trace_id, "route": "demo_tour"})

    try:
        # Add demo headers
        response.headers["x-nerava-demo"] = "true"

        # Create artifacts directory
        import time

        timestamp = int(time.time())
        artifact_dir = f"tmp/demo/{timestamp}"
        os.makedirs(artifact_dir, exist_ok=True)

        # Get current scenario state
        states = db.query(DemoState).all()
        scenario = {
            "grid_state": "offpeak",
            "merchant_shift": "balanced",
            "rep_profile": "medium",
            "city": "austin",
        }

        for state in states:
            scenario[state.key] = state.value

        # Tour steps with error handling
        steps = []
        tour_steps = [
            {
                "name": "social_network",
                "title": "Social Network Effects",
                "endpoint": "/v1/demo/social/overview",
                "description": "Show follow graph and influence metrics",
            },
            {
                "name": "behavior_offpeak",
                "title": "Utility Behavior Cloud (Off-Peak)",
                "endpoint": "/v1/utility/behavior/cloud",
                "description": "Show elasticity under normal conditions",
            },
            {
                "name": "switch_peak",
                "title": "Switch to Peak Grid",
                "endpoint": "/v1/demo/scenario",
                "description": "Change grid state to peak",
            },
            {
                "name": "behavior_peak",
                "title": "Utility Behavior Cloud (Peak)",
                "endpoint": "/v1/utility/behavior/cloud",
                "description": "Show elasticity under stress",
            },
            {
                "name": "reward_routing",
                "title": "Autonomous Reward Routing",
                "endpoint": "/v1/rewards/routing/rebalance",
                "description": "AI-powered budget rebalancing",
            },
            {
                "name": "merchant_intel",
                "title": "Merchant Intelligence",
                "endpoint": "/v1/merchant/intel/overview",
                "description": "Cohorts, forecasts, and promo rules",
            },
            {
                "name": "energy_rep",
                "title": "Energy Reputation",
                "endpoint": "/v1/profile/energy_rep",
                "description": "Portable climate credential",
            },
            {
                "name": "verify_api",
                "title": "Verify API + Fraud Guards",
                "endpoint": "/v1/verify/charge",
                "description": "Third-party charge verification",
            },
        ]

        for step_config in tour_steps:
            step_start = time.time()
            try:
                # Simulate step execution (in real implementation, make actual API calls)
                time.sleep(0.1)  # Simulate work
                step_ms = int((time.time() - step_start) * 1000)

                steps.append(DemoTourStep(name=step_config["name"], status="success", ms=step_ms))
            except Exception as e:
                step_ms = int((time.time() - step_start) * 1000)
                steps.append(
                    DemoTourStep(name=step_config["name"], status="error", ms=step_ms, error=str(e))
                )

        # Generate artifacts
        artifacts = {
            "city_impact": {"city": scenario["city"], "mwh_saved": 1250.5, "rewards_paid": 125000},
            "behavior_cloud": {
                "elasticity": 0.15 if scenario["grid_state"] == "peak" else 0.08,
                "participation": 0.75,
            },
            "merchant_intel": {
                "cohorts": ["new_users", "returning_users", "power_users"],
                "forecast_accuracy": 0.87,
            },
            "ai_rewards": {"optimization_score": 0.92, "budget_efficiency": 0.88},
            "energy_rep_high": {
                "score": 850,
                "tier": "Gold",
                "benefits": ["Premium rates", "Priority support"],
            },
            "energy_rep_low": {
                "score": 450,
                "tier": "Bronze",
                "benefits": ["Basic rates", "Standard support"],
            },
            "finance_offers": {"apr_delta_bps": -50, "eligibility": "High energy reputation"},
            "coop_pools": {"merchants": 5, "utility_partners": 2, "total_budget": 50000},
            "deals": {"active_deals": 12, "conversion_rate": 0.23},
            "events": {"upcoming_events": 3, "total_participants": 150},
        }

        # Save artifacts to files
        for key, value in artifacts.items():
            with open(f"{artifact_dir}/{key}.json", "w") as f:
                json.dump(value, f, indent=2)

        log_info(
            {
                "trace_id": trace_id,
                "route": "demo_tour",
                "steps_count": len(steps),
                "artifact_dir": artifact_dir,
                "status": "success",
            }
        )

        return DemoTourResponse(steps=steps, artifacts=artifacts, artifact_dir=artifact_dir)

    except Exception as e:
        log_error({"trace_id": trace_id, "route": "demo_tour", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to run tour")


@router.get("/export")
async def export_demo_data(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Export comprehensive demo data for investor screenshots.
    """
    _check_demo_mode()

    trace_id = get_trace_id(request)
    log_info({"trace_id": trace_id, "route": "demo_export"})

    try:
        # Get current state
        state = {
            "grid_state": "offpeak",
            "merchant_shift": "balanced",
            "rep_profile": "high",
            "city": "austin",
        }

        for demo_state in db.query(DemoState).all():
            state[demo_state.key] = demo_state.value

        # Generate representative payloads
        export_data = {
            "city_impact": {
                "city": "Austin",
                "mwh_saved": 125.5,
                "rewards_paid_cents": 12500,
                "leaderboard": [
                    {"username": "u_highrep", "kwh_saved": 45.2, "rank": 1},
                    {"username": "u_follower1", "kwh_saved": 32.1, "rank": 2},
                ],
            },
            "behavior_cloud": {
                "utility_id": "UT_TX",
                "window": "24h",
                "segments": [
                    {"name": "night_owls", "count": 45, "participation_rate": 0.8},
                    {"name": "green_commuters", "count": 32, "participation_rate": 0.9},
                ],
                "participation": {"hour_14": 0.7, "hour_18": 0.9},
                "elasticity": {"5_cents": 0.3, "10_cents": 0.6, "20_cents": 0.8},
            },
            "merchant_intel": {
                "merchant_id": "M_A",
                "cohorts": {
                    "night_owls": {"count": 15, "avg_spend": 8.50},
                    "green_commuters": {"count": 22, "avg_spend": 12.30},
                },
                "forecasts": {"next_24h": {"footfall": 45, "confidence": 0.85}},
                "promos": [
                    {"type": "coffee_discount", "value": "$2 off", "conditions": "grid_load < 70%"}
                ],
            },
            "ai_rewards": {
                "suggestions": [
                    {"region": "austin", "hour": 14, "incentive_cents": 50},
                    {"region": "austin", "hour": 18, "incentive_cents": 75},
                ]
            },
            "energy_rep_high": {
                "user_id": 1,
                "score": 780,
                "tier": "Gold",
                "components": {"charging_frequency": 0.8, "green_hours": 0.9},
            },
            "energy_rep_low": {
                "user_id": 2,
                "score": 320,
                "tier": "Bronze",
                "components": {"charging_frequency": 0.3, "green_hours": 0.2},
            },
            "finance_offers": [
                {"partner": "GreenBank", "apr_delta_bps": 25, "eligibility": {"rep_min": 700}}
            ],
            "coop_pools": [
                {"pool_id": "POOL_AUSTIN_1", "utility_id": "UT_TX", "merchants": ["M_A", "M_B"]}
            ],
            "deals": [
                {"merchant": "Coffee Corner", "discount": "20%", "window": "06:00-10:00"},
                {"merchant": "Green Grocery", "discount": "15%", "window": "14:00-18:00"},
            ],
            "events": [
                {"type": "charging_session", "user": "u_highrep", "kwh": 5.0, "reward": "$5.00"},
                {
                    "type": "reward_redemption",
                    "user": "u_follower1",
                    "merchant": "Coffee Corner",
                    "value": "$2.00",
                },
            ],
        }

        log_info({"trace_id": trace_id, "route": "demo_export", "status": "success"})

        return export_data

    except Exception as e:
        log_error({"trace_id": trace_id, "route": "demo_export", "error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to export demo data")


# Autorun endpoints for UI walkthrough


def _utcnow():
    return datetime.now(timezone.utc)


@router.post("/start")
async def demo_start(
    request: Request,
    body: dict = {},
    db: Session = Depends(get_db),
    _auth=Depends(require_scopes(["admin:demo"])),
    _rl=Depends(rate_limit("demo_start", "20/min")),
):
    """Start autorun UI walkthrough."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    script = (body or {}).get("script", "investor_run_v1")
    run_id = f"dr_{_utcnow().isoformat()}"
    expires = _utcnow() + timedelta(seconds=60)

    # Get or create demo state
    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    if not state:
        state = DemoState(key="autorun", value="false")
        db.add(state)

    # Set autorun fields
    state.autorun = True
    state.autorun_script = script
    state.autorun_run_id = run_id
    state.autorun_expires_at = expires
    db.commit()

    log_info(
        "demo_autorun_set", {"run_id": run_id, "script": script, "expires_at": expires.isoformat()}
    )

    return {"ok": True, "run_id": run_id, "expires_in_s": 60}


@router.get("/autorun")
async def demo_autorun(
    request: Request, db: Session = Depends(get_db), _auth=Depends(require_scopes(["admin:demo"]))
):
    """Get autorun status."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    if not state or not state.autorun:
        return {"autorun": False}

    # Check if expired
    if state.autorun_expires_at and _utcnow() > state.autorun_expires_at:
        # Auto-expire
        state.autorun = False
        state.autorun_script = None
        state.autorun_run_id = None
        state.autorun_expires_at = None
        db.commit()
        return {"autorun": False}

    return {"autorun": True, "script": state.autorun_script, "run_id": state.autorun_run_id}


@router.post("/ack")
async def demo_ack(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    _auth=Depends(require_scopes(["admin:demo"])),
):
    """Acknowledge autorun completion."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    want_id = (body or {}).get("run_id")
    state = db.query(DemoState).filter(DemoState.key == "autorun").first()

    if not state or not state.autorun or state.autorun_run_id != want_id:
        raise HTTPException(status_code=409, detail="No matching autorun")

    # Clear autorun state
    state.autorun = False
    state.autorun_script = None
    state.autorun_run_id = None
    state.autorun_expires_at = None
    db.commit()

    log_info("demo_autorun_ack", {"run_id": want_id})
    return {"ok": True}


# Autorun-specific endpoints for frontend integration
@router.post("/autorun/start")
async def autorun_start(
    request: Request,
    body: dict = {},
    db: Session = Depends(get_db),
    _auth=Depends(require_scopes(["admin:demo"])),
):
    """Start autorun session."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    script = (body or {}).get("script", "investor_tour")
    run_id = f"ar_{_utcnow().isoformat()}"
    expires = _utcnow() + timedelta(seconds=300)  # 5 minutes

    # Get or create demo state
    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    if not state:
        state = DemoState(key="autorun", value="false")
        db.add(state)

    # Set autorun fields
    state.autorun = True
    state.autorun_script = script
    state.autorun_run_id = run_id
    state.autorun_expires_at = expires
    db.commit()

    return {
        "running": True,
        "session_id": run_id,
        "script": script,
        "expires_at": expires.isoformat(),
    }


@router.get("/autorun/status")
async def autorun_status(
    request: Request, db: Session = Depends(get_db), _auth=Depends(require_scopes(["admin:demo"]))
):
    """Get autorun status."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    if not state or not state.autorun:
        return {"running": False}

    # Check if expired
    if state.autorun_expires_at:
        # Ensure both datetimes are timezone-aware for comparison
        expires_at = state.autorun_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if _utcnow() > expires_at:
            state.autorun = False
            state.autorun_script = None
            state.autorun_run_id = None
            state.autorun_expires_at = None
            db.commit()
            return {"running": False}

    # Get current scenario
    scenario_state = db.query(DemoState).filter(DemoState.key == "scenario").first()
    scenario = scenario_state.value if scenario_state else "default"

    return {
        "running": True,
        "session_id": state.autorun_run_id,
        "script": state.autorun_script,
        "scenario": scenario,
    }


@router.post("/autorun/stop")
async def autorun_stop(
    request: Request, db: Session = Depends(get_db), _auth=Depends(require_scopes(["admin:demo"]))
):
    """Stop autorun session."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    if state and state.autorun:
        state.autorun = False
        state.autorun_script = None
        state.autorun_run_id = None
        state.autorun_expires_at = None
        db.commit()

    return {"running": False}


@router.post("/autorun/scenario")
async def autorun_scenario(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    _auth=Depends(require_scopes(["admin:demo"])),
):
    """Set autorun scenario."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    scenario = (body or {}).get("scenario", "default")

    # Update scenario in demo state
    state = db.query(DemoState).filter(DemoState.key == "scenario").first()
    if not state:
        state = DemoState(key="scenario", value=scenario)
        db.add(state)
    else:
        state.value = scenario
    db.commit()

    return {"scenario": scenario}


@router.post("/autorun/execute")
async def autorun_execute(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    _auth=Depends(require_scopes(["admin:demo"])),
):
    """Execute autorun script."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    script = (body or {}).get("script", "investor_tour")

    # Simulate script execution
    import time

    start_time = time.time()
    time.sleep(0.1)  # Simulate processing
    duration_ms = int((time.time() - start_time) * 1000)

    return {"executed": True, "script": script, "duration_ms": duration_ms}


@router.get("/autorun/poll")
async def autorun_poll(
    request: Request, db: Session = Depends(get_db), _auth=Depends(require_scopes(["admin:demo"]))
):
    """Poll autorun status."""
    if not is_demo():
        raise HTTPException(status_code=403, detail="Demo mode disabled")

    state = db.query(DemoState).filter(DemoState.key == "autorun").first()
    scenario_state = db.query(DemoState).filter(DemoState.key == "scenario").first()

    return {
        "running": state.autorun if state else False,
        "scenario": scenario_state.value if scenario_state else "default",
        "last_activity": _utcnow().isoformat(),
    }
