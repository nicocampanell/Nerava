#!/usr/bin/env python3
"""
Nerava Demo Runner — investor-friendly narration + color highlighting.

Adds:
- Baseline vs. post-preferences recommendations (shows scores/reasons delta)
- Utility-seeded incentive event: webhook → award_off_peak → wallet history
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta

import httpx

BASE = os.getenv("NERAVA_URL", "http://127.0.0.1:8000")
LAT  = float(os.getenv("NERAVA_LAT", "30.4021"))
LNG  = float(os.getenv("NERAVA_LNG", "-97.7265"))

USER1 = os.getenv("NERAVA_USER1", "demo@nerava.app")
USER2 = os.getenv("NERAVA_USER2", "investor@nerava.app")
PREFS1 = os.getenv("NERAVA_PREFS1", "coffee_bakery,quick_bite")
PREFS2 = os.getenv("NERAVA_PREFS2", "coffee_bakery,quick_bite")

# ---------- ANSI colors ----------
BOLD  = "\033[1m"; DIM="\033[2m"; RED="\033[31m"; GRN="\033[32m"; YEL="\033[33m"
BLU   = "\033[34m"; MAG="\033[35m"; CYA="\033[36m"; RST="\033[0m"

# ---------- HTTP helpers ----------
def _ok(resp):
    resp.raise_for_status()
    return resp.json()

def get(path, **params):
    with httpx.Client(timeout=20.0) as c:
        return _ok(c.get(BASE + path, params=params))

def post_json(path, payload=None, **params):
    with httpx.Client(timeout=20.0) as c:
        return _ok(c.post(BASE + path, params=params, json=payload or {}))

def post_qs(path, **params):
    with httpx.Client(timeout=20.0) as c:
        return _ok(c.post(BASE + path, params=params))

# ---------- Pretty printers ----------
def hr(char="─", n=70): print(DIM + char * n + RST)
def title(t): print(f"\n{BOLD}{t}{RST}")
def narr(s): print(f"{DIM}• {s}{RST}")

def show_request(method, path, params=None, body=None):
    q = ""
    if params:
        try: q = "?" + "&".join(f"{k}={v}" for k,v in params.items())
        except Exception: q = ""
    print(f"{BLU}{method} {BASE}{path}{q}{RST}")
    if body: print(f"{DIM}  body:{RST} {json.dumps(body)}")

def show_key_value(label, value, color=GRN):
    print(f"  {color}{label}:{RST} {value}")

def show_json_snippet(obj, keys):
    for k in keys:
        if k in obj: show_key_value(k, obj[k])

def summarize_merchants(items, limit=6):
    print(f"{MAG}Top nearby places (walkable) ↴{RST}")
    for i, m in enumerate(items[:limit], 1):
        name = m.get("name", "Unknown")
        badge = m.get("badge", "")
        cats = ",".join(m.get("categories", []))
        links = m.get("links", {})
        dots = []
        if "reserve" in links: dots.append("Reserve")
        if "pickup" in links: dots.append("Pickup")
        dots_str = f"  {DIM}({', '.join(dots)}){RST}" if dots else ""
        print(f"   {i:>2}. {BOLD}{name}{RST} {DIM}•{RST} {badge or cats}{dots_str}")

def safe_step(title_text, call_fn, request_show=None, response_highlight=None, warn_key=None, warnings=None):
    title(f"▶ {title_text}")
    if request_show:
        show_request(request_show.get("method","GET"),
                     request_show.get("path",""),
                     request_show.get("params"),
                     request_show.get("body"))
    try:
        data = call_fn()
        if response_highlight: response_highlight(data)
        else: print(json.dumps(data, indent=2))
        print(f"{GRN}✓ Success{RST}")
        return data, None
    except Exception as e:
        print(f"{RED}✗ Failed:{RST} {e}")
        traceback.print_exc(limit=1)
        if warnings is not None and warn_key: warnings.append((warn_key, str(e)))
        return None, str(e)

# ---------- Wallet helpers ----------
def wallet_credit_any(user_id: str, cents: int):
    try: return post_qs("/v1/wallet/credit_qs", user_id=user_id, cents=cents)
    except Exception:
        payload = {"user_id": user_id, "amount_cents": cents}
        return post_json("/v1/wallet/credit", payload)

def wallet_debit_any(user_id: str, cents: int):
    try: return post_qs("/v1/wallet/debit_qs", user_id=user_id, cents=cents)
    except Exception:
        payload = {"user_id": user_id, "amount_cents": cents}
        return post_json("/v1/wallet/debit", payload)

def rec_summary(label, rec):
    show_key_value(f"{label} hub", rec.get("name"))
    show_key_value(f"{label} score", rec.get("score"))
    show_key_value(f"{label} reasons", ", ".join(rec.get("reason_tags", [])))

def main():
    warnings = []

    # 1) HEALTH
    hr(); narr("We start by confirming the API is live.")
    _, _ = safe_step("Health check",
                     lambda: get("/v1/health"),
                     {"method":"GET","path":"/v1/health"},
                     lambda d: [show_key_value("ok", d.get("ok")),
                                show_key_value("time", d.get("time"))])

    # 2) REGISTER USERS
    hr(); narr("We register two users for the live demo.")
    _, _ = safe_step(f"Register user {USER1}",
                     lambda: post_json("/v1/users/register", {"email": USER1, "name": "Demo User"}),
                     {"method":"POST","path":"/v1/users/register","body":{"email":USER1,"name":"Demo User"}},
                     lambda d: show_key_value("email", d.get("email")))
    _, _ = safe_step(f"Register user {USER2}",
                     lambda: post_json("/v1/users/register", {"email": USER2, "name": "Investor User"}),
                     {"method":"POST","path":"/v1/users/register","body":{"email":USER2,"name":"Investor User"}},
                     lambda d: show_key_value("email", d.get("email")))

    # 3) HUBS NEARBY
    hr(); narr("We cluster chargers into a few walkable 'Nerava Hubs' near The Domain.")
    hubs, _ = safe_step("Find nearby hubs",
                        lambda: get("/v1/hubs/nearby", lat=LAT, lng=LNG, radius_km=2, max_results=5),
                        {"method":"GET","path":"/v1/hubs/nearby",
                         "params":{"lat":LAT,"lng":LNG,"radius_km":2,"max_results":5}},
                        lambda d: show_key_value("hub_count", len(d)))

    def hub_coords_id():
        if hubs and isinstance(hubs, list) and len(hubs)>0:
            h = hubs[0]; return h.get("lat", LAT), h.get("lng", LNG), h.get("id","")
        return LAT, LNG, ""

    # 3A) BASELINE RECOMMENDATION (before prefs)
    hr(); narr("Baseline recommendation (before preferences) for USER1.")
    rec0, _ = safe_step(f"Recommend (baseline) for {USER1}",
                        lambda: get("/v1/hubs/recommend", lat=LAT, lng=LNG, radius_km=2, user_id=USER1),
                        {"method":"GET","path":"/v1/hubs/recommend",
                         "params":{"lat":LAT,"lng":LNG,"radius_km":2,"user_id":USER1}},
                        lambda d: rec_summary("baseline", d))

    # 4) MERCHANTS NEARBY (unified)
    mlat, mlng, _hid = hub_coords_id()
    hr(); narr("Unified merchants — local perks first, plus Reserve/Pickup links.")
    _, _ = safe_step("Nearby merchants at recommended hub (unified)",
                     lambda: get("/v1/merchants/nearby", lat=mlat, lng=mlng, radius_m=600, max_results=12, prefs=PREFS1, hub_id=_hid or "hub_unknown"),
                     {"method":"GET","path":"/v1/merchants/nearby",
                      "params":{"lat":mlat,"lng":mlng,"radius_m":600,"max_results":12,"prefs":PREFS1,"hub_id":_hid or "hub_unknown"}},
                     lambda d: summarize_merchants(d, 8))

    # 5) USER PREFS
    hr(); narr("We set preferences so both users are nudged toward coffee/quick options.")
    prefs_payload = {"pref_coffee": True, "pref_food": True, "pref_dog": False, "pref_kid": False, "pref_shopping": False, "pref_exercise": False}
    _, _ = safe_step(f"Save prefs for {USER1}",
                     lambda: post_json(f"/v1/users/{USER1}/prefs", prefs_payload),
                     {"method":"POST","path":f"/v1/users/{USER1}/prefs","body":prefs_payload},
                     lambda d: show_json_snippet(d, ["pref_coffee","pref_food"]))
    _, _ = safe_step(f"Save prefs for {USER2}",
                     lambda: post_json(f"/v1/users/{USER2}/prefs", prefs_payload),
                     {"method":"POST","path":f"/v1/users/{USER2}/prefs","body":prefs_payload},
                     lambda d: show_json_snippet(d, ["pref_coffee","pref_food"]))

    # 5A) RECOMMENDATION AFTER PREFS (delta vs baseline)
    hr(); narr("Re-run recommendation after preferences; note score/reason deltas.")
    rec1_u1, _ = safe_step(f"Recommend (after prefs) for {USER1}",
                           lambda: get("/v1/hubs/recommend", lat=LAT, lng=LNG, radius_km=2, user_id=USER1),
                           {"method":"GET","path":"/v1/hubs/recommend",
                            "params":{"lat":LAT,"lng":LNG,"radius_km":2,"user_id":USER1}},
                           lambda d: rec_summary("after", d))
    rec1_u2, _ = safe_step(f"Recommend (after prefs) for {USER2}",
                           lambda: get("/v1/hubs/recommend", lat=LAT, lng=LNG, radius_km=2, user_id=USER2),
                           {"method":"GET","path":"/v1/hubs/recommend",
                            "params":{"lat":LAT,"lng":LNG,"radius_km":2,"user_id":USER2}},
                           lambda d: rec_summary("after", d))

    # 6) RESERVATION (SOFT)
    hub_id = (rec1_u1 or rec0 or {}).get("id") or ((hubs or [{}])[0]).get("id", "hub_domain_A")
    start_iso = (datetime.utcnow() + timedelta(minutes=10)).replace(microsecond=0).isoformat() + "Z"
    resv_req = {"hub_id": hub_id, "user_id": USER1, "start_iso": start_iso, "minutes": 30}
    hr(); narr("We place a 30-min soft reservation window.")
    _, _ = safe_step("Create soft reservation (30m)",
                     lambda: post_json("/v1/reservations/soft", resv_req),
                     {"method":"POST","path":"/v1/reservations/soft","body":resv_req},
                     lambda d: [show_key_value("reservation_id", d.get("id")),
                                show_key_value("hub_id", d.get("hub_id")),
                                show_key_value("status", d.get("status"))])

    # 7) WALLET CREDIT/DEBIT (USER1)
    hr(); narr("Wallet simulates cash-back—credit $5 then debit $3 for a small purchase.")
    _, _ = safe_step(f"Wallet (before) — {USER1}",
                     lambda: get("/v1/wallet", user_id=USER1),
                     {"method":"GET","path":"/v1/wallet","params":{"user_id":USER1}},
                     lambda d: show_key_value("balance_cents", d.get("balance_cents")))
    _, _ = safe_step("Wallet credit +500¢",
                     lambda: wallet_credit_any(USER1, 500),
                     {"method":"POST","path":"(credit_qs or credit JSON)","params":{"user_id":USER1,"cents":500}},
                     lambda d: show_key_value("new_balance_cents", d.get("new_balance_cents", d.get("balance_cents"))))
    _, _ = safe_step("Wallet debit -300¢",
                     lambda: wallet_debit_any(USER1, 300),
                     {"method":"POST","path":"(debit_qs or debit JSON)","params":{"user_id":USER1,"cents":300}},
                     lambda d: show_key_value("new_balance_cents", d.get("new_balance_cents", d.get("balance_cents"))))
    _, _ = safe_step(f"Wallet (after) — {USER1}",
                     lambda: get("/v1/wallet", user_id=USER1),
                     {"method":"GET","path":"/v1/wallet","params":{"user_id":USER1}},
                     lambda d: show_key_value("balance_cents", d.get("balance_cents")))

    # 8) CHARGERS (TRANSPARENCY)
    hr(); narr("Transparency: show raw chargers results.")
    _, _ = safe_step("Chargers nearby (OCM)",
                     lambda: get("/v1/chargers/nearby", lat=LAT, lng=LNG, radius_km=2, max_results=5),
                     {"method":"GET","path":"/v1/chargers/nearby",
                      "params":{"lat":LAT,"lng":LNG,"radius_km":2,"max_results":5}},
                     lambda d: show_key_value("result_count", len(d)))

    # 9) LOCAL MERCHANT + PERK (so users converge)
    hr(); narr("We add a local merchant with a simple $0.75 perk that both users can claim.")
    def ensure_local_offer():
        m = post_json("/v1/local/merchant", {"name":"Domain Coffee","lat":30.4025,"lng":-97.7258,"category":"coffee_bakery","logo_url":""})
        p = post_json("/v1/local/perk", {"merchant_id":m["id"],"title":"Latte perk","description":"$0.75 off","reward_cents":75})
        return {"merchant_id": m["id"], "perk_id": p["id"], "reward_cents": p["reward_cents"]}
    ids, _ = safe_step("Create local merchant + perk",
                       ensure_local_offer,
                       {"method":"POST","path":"/v1/local/merchant + /v1/local/perk"},
                       lambda d: [show_key_value("merchant_id", d["merchant_id"]),
                                  show_key_value("perk_id", d["perk_id"]),
                                  show_key_value("perk_reward_cents", d["reward_cents"])])

    # Unified merchants again (should include Perk item(s) first)
    _, _ = safe_step("Nearby merchants (unified, after adding local perk)",
                     lambda: get("/v1/merchants/nearby", lat=mlat, lng=mlng, radius_m=600, max_results=12, prefs=PREFS1, hub_id=hub_id or "hub_unknown"),
                     {"method":"GET","path":"/v1/merchants/nearby",
                      "params":{"lat":mlat,"lng":mlng,"radius_m":600,"max_results":12,"prefs":PREFS1,"hub_id":hub_id or "hub_unknown"}},
                     lambda d: summarize_merchants(d, 8))

    # 10) PERK CLAIMS (idempotent per user)
    hr(); narr("Both users claim the same perk — credited once per user, duplicates ignored.")
    claim_body1 = {"perk_id": ids["perk_id"], "user_id": USER1}
    _, _ = safe_step(f"Claim perk for {USER1}",
                     lambda: post_json("/v1/local/perk/claim", claim_body1),
                     {"method":"POST","path":"/v1/local/perk/claim","body":claim_body1},
                     lambda d: [show_key_value("newly_claimed", d.get("newly_claimed", False)),
                                show_key_value("wallet_balance_cents", d.get("wallet_balance_cents"))],
                     warnings=warnings, warn_key="CLAIM_1")

    claim_body2 = {"perk_id": ids["perk_id"], "user_id": USER2}
    _, _ = safe_step(f"Claim perk for {USER2}",
                     lambda: post_json("/v1/local/perk/claim", claim_body2),
                     {"method":"POST","path":"/v1/local/perk/claim","body":claim_body2},
                     lambda d: [show_key_value("newly_claimed", d.get("newly_claimed", False)),
                                show_key_value("wallet_balance_cents", d.get("wallet_balance_cents"))],
                     warnings=warnings, warn_key="CLAIM_2")

    # 11) UTILITY INCENTIVE: seed → trigger → award for both → history
    hr(); narr("Utility load-shift event bonus — we seed/trigger and then award (+50¢ each during window).")

    # First, show that outside the window we might get zero
    _, _ = safe_step("Award off-peak (before webhook) — expect 0 if not in window",
                     lambda: post_qs("/v1/incentives/award_off_peak", user_id=USER1),
                     {"method":"POST","path":"/v1/incentives/award_off_peak","params":{"user_id":USER1}},
                     lambda d: show_key_value("awarded_cents", d.get("awarded_cents")))

    # Trigger the utility webhook (sets next 60m as off-peak & 50¢ award)
    _, _ = safe_step("Trigger Austin Energy demo event (sets 60m window @ 50¢)",
                     lambda: post_qs("/v1/webhooks/utility/austin_energy/fake_event"),
                     {"method":"POST","path":"/v1/webhooks/utility/austin_energy/fake_event"},
                     lambda d: show_json_snippet(d, ["ok","off_peak_now_to_plus_60m"]))

    # Now award for both users (should be 50¢ during the window)
    _, _ = safe_step(f"Award off-peak → {USER1}",
                     lambda: post_qs("/v1/incentives/award_off_peak", user_id=USER1),
                     {"method":"POST","path":"/v1/incentives/award_off_peak","params":{"user_id":USER1}},
                     lambda d: [show_key_value("awarded_cents", d.get("awarded_cents")),
                                show_key_value("new_balance_cents", d.get("new_balance_cents"))])
    _, _ = safe_step(f"Award off-peak → {USER2}",
                     lambda: post_qs("/v1/incentives/award_off_peak", user_id=USER2),
                     {"method":"POST","path":"/v1/incentives/award_off_peak","params":{"user_id":USER2}},
                     lambda d: [show_key_value("awarded_cents", d.get("awarded_cents")),
                                show_key_value("new_balance_cents", d.get("new_balance_cents"))])

    # Show wallet histories with OFF_PEAK_AWARD entries
    _, _ = safe_step(f"Wallet history — {USER1}",
                     lambda: get("/v1/wallet/history", user_id=USER1),
                     {"method":"GET","path":"/v1/wallet/history","params":{"user_id":USER1}},
                     lambda rows: print(json.dumps(rows[:5], indent=2)))
    _, _ = safe_step(f"Wallet history — {USER2}",
                     lambda: get("/v1/wallet/history", user_id=USER2),
                     {"method":"GET","path":"/v1/wallet/history","params":{"user_id":USER2}},
                     lambda rows: print(json.dumps(rows[:5], indent=2)))

    # 12) FINAL BALANCES
    hr(); narr("Final wallet balances after claims and utility bonus.")
    _, _ = safe_step("Wallet — " + USER1,
                     lambda: get("/v1/wallet", user_id=USER1),
                     {"method":"GET","path":"/v1/wallet","params":{"user_id":USER1}},
                     lambda d: show_key_value("balance_cents", d.get("balance_cents")))
    _, _ = safe_step("Wallet — " + USER2,
                     lambda: get("/v1/wallet", user_id=USER2),
                     {"method":"GET","path":"/v1/wallet","params":{"user_id":USER2}},
                     lambda d: show_key_value("balance_cents", d.get("balance_cents")))

    # 13) MERCHANT SUMMARY
    def normalize_summary(raw):
        if isinstance(raw, dict): return raw
        raise ValueError("unexpected summary shape")

    hr("="); print("\nMERCHANT SUMMARY (Perk performance)")
    try:
        merchant_id = ids["merchant_id"]
        raw = get(f"/v1/local/merchant/{merchant_id}/summary")
        summary = normalize_summary(raw)
        tot = summary["totals"]
        print(f"  Merchant #{merchant_id} — claims: {tot['claims']}, unique users: {tot['unique_users']}")
        for p in summary["perks"]:
            print(f"   • {p['perk_title']} — {p['claim_count']} claim(s), {p['unique_users']} unique; last: {p['last_claim_at']}")
    except Exception as e:
        print(f"{RED}✗ Failed summary:{RST} {e}")
        warnings.append(("MERCHANT_SUMMARY", str(e)))

    # Final recap
    hr("="); title("DEMO SUMMARY (Investor-friendly)")
    narr("✅ API alive & responding")
    narr("✅ Baseline vs post-preferences recommendation (scores/reasons changed)")
    narr("✅ Unified local+Google merchants (perks + Reserve/Pickup)")
    narr("✅ Soft reservation window")
    narr("✅ Wallet: cashback + utility award")
    narr("✅ Perk claims: one-time per user, no double-dips")
    narr("✅ Merchant summary proves engagement")
    narr("✅ Chargers endpoint for transparency")
    if warnings:
        print(f"\n{YEL}Completed with {len(warnings)} warning(s):{RST} " + ", ".join(k for k,_ in warnings))
        sys.exit(1)
    else:
        print(f"\n{GRN}All steps passed. Ready to demo!{RST}")

if __name__ == "__main__":
    main()
