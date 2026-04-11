"""
Nerava demo tests (pytest)

Covers:
- Health
- User register + prefs
- Wallet credit/debit
- Local merchant + perk creation
- Unified merchants (local + google) listing (works even without Google key)
- Idempotent perk claim (first claim credits, duplicate does not)
- Hubs nearby + soft reservation

Run:
  pytest -q nerava-backend-v9/app/tests/test_demo.py
"""

import json
import os
import time
from datetime import datetime, timedelta

import pytest
from app.main_simple import app
from fastapi.testclient import TestClient

USER = os.getenv("NERAVA_USER", "demo@nerava.app")
LAT = float(os.getenv("NERAVA_LAT", "30.4021"))
LNG = float(os.getenv("NERAVA_LNG", "-97.7265"))

@pytest.fixture(scope="module")
def client():
    """Provide TestClient for API tests."""
    return TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def health_check(client):
    """Health check that runs once per test module."""
    r = client.get("/v1/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("ok", False) is True
    return True

def test_user_register(client):
    r = client.post("/v1/users/register", json={"email": USER, "name": "Demo User"})
    assert r.status_code in (200, 201), r.text
    assert "email" in r.json()

def test_user_prefs(client):
    prefs_payload = {"pref_coffee": True, "pref_food": True}
    r = client.post(f"/v1/users/{USER}/prefs", json=prefs_payload)
    assert r.status_code == 200, r.text
    r2 = client.get(f"/v1/users/{USER}/prefs")
    assert r2.status_code == 200, r2.text
    j = r2.json()
    assert j.get("pref_coffee") is True

def test_wallet_credit_debit(client):
    r_before = client.get("/v1/wallet", params={"user_id": USER})
    assert r_before.status_code == 200, r_before.text
    before = r_before.json().get("balance_cents", 0)

    r_credit = client.post("/v1/wallet/credit_qs", params={"user_id": USER, "cents": 500})
    assert r_credit.status_code == 200, r_credit.text

    r_after = client.get("/v1/wallet", params={"user_id": USER})
    after = r_after.json().get("balance_cents", 0)
    assert after >= before + 500

@pytest.fixture(scope="module")
def local_merchant_and_perk(client):
    """Create a unique local merchant + perk for this test module."""
    unique = str(int(time.time()))
    m_payload = {
        "name": f"Domain Coffee {unique}",
        "lat": LAT,
        "lng": LNG,
        "category": "coffee_bakery",
        "logo_url": "",
    }
    r_m = client.post("/v1/local/merchant", json=m_payload)
    assert r_m.status_code == 200, r_m.text
    merchant = r_m.json()
    mid = merchant["id"]

    p_payload = {
        "merchant_id": mid,
        "title": f"Latte perk {unique}",
        "description": "$0.75 off",
        "reward_cents": 75,
    }
    r_p = client.post("/v1/local/perk", json=p_payload)
    assert r_p.status_code == 200, r_p.text
    perk = r_p.json()
    pid = perk["id"]

    return {"merchant_id": mid, "perk_id": pid, "merchant_name": m_payload["name"]}

def test_unified_merchants_includes_local(client, local_merchant_and_perk):
    # ask unified endpoint for nearby merchants; expect our local perk at/near the top
    params = {
        "lat": LAT,
        "lng": LNG,
        "radius_m": 600,
        "max_results": 12,
        "prefs": "coffee_bakery,quick_bite",
        "hub_id": "hub_domain",
    }
    r = client.get("/v1/merchants/nearby", params=params)
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)

    # Confirm at least one local-sourced item
    local_items = [x for x in items if x.get("source") == "local" and "perk" in x]
    assert len(local_items) >= 1, f"No local items found in unified list: {json.dumps(items[:3], indent=2)}"

def test_perk_claim_idempotent_and_wallet(client, local_merchant_and_perk):
    pid = local_merchant_and_perk["perk_id"]

    # balance before
    before = client.get("/v1/wallet", params={"user_id": USER}).json().get("balance_cents", 0)

    # first claim -> should credit (newly_claimed true)
    r1 = client.post("/v1/local/perk/claim", json={"perk_id": pid, "user_id": USER})
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1.get("idempotent") in (False, None)  # first time shouldn't be idempotent
    # wallet may return new balance; to be robust, recheck balance
    mid = client.get("/v1/wallet", params={"user_id": USER}).json().get("balance_cents", 0)
    assert mid >= before + 75  # awarded 75¢

    # second claim -> should be idempotent (no extra credit)
    r2 = client.post("/v1/local/perk/claim", json={"perk_id": pid, "user_id": USER})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2.get("idempotent") is True
    after = client.get("/v1/wallet", params={"user_id": USER}).json().get("balance_cents", 0)
    assert after == mid  # unchanged on duplicate claim

def test_reservation_and_hubs(client):
    hubs = client.get("/v1/hubs/nearby", params={"lat": LAT, "lng": LNG, "radius_km": 2}).json()
    assert isinstance(hubs, list)
    if hubs:
        hub_id = hubs[0].get("id", "hub_domain_A")
        start = (datetime.utcnow() + timedelta(minutes=5)).replace(microsecond=0).isoformat() + "Z"
        r = client.post("/v1/reservations/soft", json={"hub_id": hub_id, "user_id": USER, "start_iso": start, "minutes": 30})
        assert r.status_code == 200, r.text
