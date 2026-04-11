# Nerava × HubSpot Integration Plan

**Prepared for:** Daniel Aggarwal, Head of Growth Systems
**Date:** April 9, 2026
**Status:** Backend plumbing built — needs event wiring + HubSpot workflow setup

---

## Executive Summary

The HubSpot integration infrastructure is already built and running in production. An async outbox pattern processes events reliably with retry logic and rate limiting. 5 event types are live today. To power all 8 lifecycle flows from Daniel's strategy doc, we need to add ~15 more event types and sync 25 contact properties. Engineering estimate: **10-12 days** for the backend wiring. Daniel owns the HubSpot workflow configuration, email templates, and campaign activation.

---

## What's Already Built

| Component | Status | Details |
|-----------|--------|---------|
| Outbox pattern | **Live** | Events queued to `hubspot_outbox` table, processed async |
| Background worker | **Live** | Polls every 10 seconds, 50 events/batch |
| Retry logic | **Live** | 3 retries with exponential backoff |
| Rate limiting | **Live** | 8 requests/second to HubSpot API |
| Dry-run mode | **Live** | `HUBSPOT_SEND_LIVE=false` logs without sending |
| Event adapter | **Live** | Maps Nerava events → HubSpot contact properties |

### 5 Events Already Flowing

| Event | Trigger | Properties Synced |
|-------|---------|-------------------|
| `driver_signed_up` | OTP verification | email, phone, signup_date, auth_provider |
| `wallet_pass_installed` | Apple/Google wallet add | wallet_pass_installed = true |
| `nova_earned` | Incentive grant | nova_balance |
| `nova_redeemed` | Nova redemption | nova_balance |
| `first_redemption_completed` | First merchant visit | first_redemption_date |

---

## Contact Properties — Full Specification (25 Total)

### Driver Properties (16)

These sync to HubSpot contacts identified by email or phone.

| # | Property Name | Type | Used In Flows | Source Event | Update Frequency |
|---|--------------|------|---------------|--------------|-----------------|
| 1 | `signup_date` | date | Welcome, Nudge | `driver_signed_up` | Once |
| 2 | `vehicle_connected` | boolean | Welcome, Nudge | `vehicle_connected` | Once |
| 3 | `vehicle_type` | string | Welcome, Nudge, Post-charge | `vehicle_connected` | Once |
| 4 | `vehicle_make_model` | string | Post-charge, Re-engagement | `vehicle_connected` | Once |
| 5 | `has_tesla_connected` | boolean | All (content gating) | `vehicle_connected` | Once |
| 6 | `first_verified_charge_date` | date | Welcome, Nudge, Re-engagement | `session_completed` (first) | Once |
| 7 | `last_session_date` | date | Post-charge, Re-engagement | `session_completed` | Every session |
| 8 | `last_session_earnings` | number (cents) | Post-charge | `session_completed` | Every session |
| 9 | `total_sessions` | number | Post-charge, Re-engagement | `session_completed` | Every session |
| 10 | `total_rewards_earned` | number (cents) | Post-charge, Re-engagement | `session_completed` | Every session |
| 11 | `total_kwh_delivered` | number | Re-engagement | `session_completed` | Every session |
| 12 | `avg_session_duration_min` | number | Segmentation | `session_completed` | Every session |
| 13 | `wallet_balance` | number (cents) | Post-charge, Re-engagement | `session_completed` / `withdrawal` | On change |
| 14 | `tier_status` | string | Post-charge | `session_completed` | On change |
| 15 | `referral_code` | string | All (email footer) | `driver_signed_up` | Once |
| 16 | `referrals_made` | number | Re-engagement | `referral_redeemed` | On referral |

**Computed in HubSpot (no backend sync needed):**

| Property | Type | How to Compute |
|----------|------|---------------|
| `days_since_last_session` | number | HubSpot calculated property: today - `last_session_date` |
| `lifecycle_stage` | string | Workflow-managed: prospect → activated → engaged → churned |
| `preferred_charging_time` | string | Can be sent from backend or computed from session timestamps |

### Merchant Properties (9)

These sync to a separate HubSpot contact object or a custom object for merchants.

| # | Property Name | Type | Used In Flows | Source Event | Update Frequency |
|---|--------------|------|---------------|--------------|-----------------|
| 17 | `business_claimed_date` | date | Welcome, Nudge | `merchant_claimed` | Once |
| 18 | `first_deal_live_date` | date | Welcome, Nudge | `merchant_deal_published` | Once |
| 19 | `active_deal` | boolean | All merchant flows | `merchant_deal_published` / `merchant_deal_paused` | On change |
| 20 | `business_type` | string | Welcome (deal templates) | `merchant_claimed` | Once |
| 21 | `last_deal_active_date` | date | Re-engagement, Digest | `merchant_deal_published` | On change |
| 22 | `total_driver_visits` | number | Digest, Re-engagement | `exclusive_session_completed` | On visit |
| 23 | `weekly_driver_visits` | number | Digest | Weekly rollup job | Weekly (Monday) |
| 24 | `weekly_deal_claims` | number | Digest | Weekly rollup job | Weekly (Monday) |
| 25 | `weekly_spend` | number (cents) | Digest | Weekly rollup job | Weekly (Monday) |

**Merchant enrichment (bonus — for better activation emails):**

| Property | Type | Purpose |
|----------|------|---------|
| `nearest_charger_network` | string | "Tesla drivers are charging 50m from you" |
| `nearby_charger_count` | number | "12 chargers within walking distance of your business" |
| `merchant_category` | string | Suggest deal templates by type (cafe, restaurant, retail) |

---

## Event Types — What Needs to Be Added

### New Backend Events (15)

| # | Event Name | Trigger Point | Properties Updated |
|---|-----------|---------------|-------------------|
| 1 | `vehicle_connected` | Tesla OAuth complete / Smartcar link | vehicle_connected, vehicle_type, vehicle_make_model, has_tesla_connected |
| 2 | `session_completed` | Session end (incentive evaluated) | last_session_date, last_session_earnings, total_sessions, total_rewards_earned, total_kwh_delivered, avg_session_duration_min, wallet_balance, tier_status |
| 3 | `first_session_completed` | Session end (session count = 1) | first_verified_charge_date |
| 4 | `withdrawal_completed` | Payout processed | wallet_balance |
| 5 | `referral_redeemed` | New user redeems referral code | referrals_made (on referrer's contact) |
| 6 | `merchant_claimed` | Business claimed in portal | business_claimed_date, business_type, nearest_charger_network, nearby_charger_count |
| 7 | `merchant_deal_published` | Exclusive created/activated | first_deal_live_date, active_deal, last_deal_active_date |
| 8 | `merchant_deal_paused` | Exclusive deactivated | active_deal |
| 9 | `exclusive_session_completed` | Driver completes merchant visit | total_driver_visits |
| 10 | `merchant_weekly_rollup` | Monday 7am CT cron job | weekly_driver_visits, weekly_deal_claims, weekly_spend |
| 11 | `tier_changed` | Reputation crosses threshold | tier_status |
| 12 | `wallet_credited` | Any wallet credit (grant, referral, bonus) | wallet_balance, total_rewards_earned |
| 13 | `driver_signed_up` | Already exists | (add referral_code to payload) |
| 14 | `nova_earned` | Already exists | (add tier_status to payload) |
| 15 | `nova_redeemed` | Already exists | (add wallet_balance to payload) |

---

## The 8 Lifecycle Flows — HubSpot Configuration

### Flow 1: Driver Welcome Series (P1)

**Enrollment trigger:** `signup_date` is known (contact created)
**Exit condition:** `first_verified_charge_date` is known OR Day 7 reached

| Email | Timing | Subject | Send Condition |
|-------|--------|---------|----------------|
| 1 | Day 0 | "You're in. Here's how Nerava works." | Always |
| 2 | Day 2 | "One step to start earning on every charge" | `vehicle_connected` = false |
| 3 | Day 4 | "What happens the next time you charge" | Always |

**Personalization tokens:** `{{ vehicle_make_model }}`, `{{ referral_code }}`
**Push equivalent:** Day 0 push "Welcome to Nerava" + Day 2 push "Connect your vehicle to start earning"

### Flow 2: Driver Activation Nudge (P1)

**Enrollment trigger:** `signup_date` > 7 days ago AND `first_verified_charge_date` is unknown
**Exit condition:** `first_verified_charge_date` becomes known

| Email | Timing | Subject | Personalization |
|-------|--------|---------|-----------------|
| 1 | Day 7 | "You've already missed out on [X] charges worth of rewards" | `{{ vehicle_make_model }}` if connected |
| 2 | Day 14 | "Still here if you need us" | Soft tone |

**Push equivalent:** Day 7 push only — "Your {{ vehicle_make_model }} is ready. Just plug in to start earning."

### Flow 3: Post-Charge Recap (P2)

**Enrollment trigger:** `session_completed` event received
**Frequency:** After every verified charge (recurring)

| Email | Timing | Subject | Dynamic Content |
|-------|--------|---------|-----------------|
| 1 | Within 15 min of session end | "You earned ${{ last_session_earnings/100 }} on your last charge" | Session duration, kWh, wallet balance, tier progress, 1-2 nearby merchants |

**Push equivalent:** Push fires immediately on session end (already built). Email is the richer follow-up.
**Suppression:** If user has push enabled, consider email-only weekly digest instead of per-session.

### Flow 4: Driver Re-engagement (P3)

**Enrollment trigger:** `days_since_last_session` > 30
**Exit condition:** New `session_completed` event

| Email | Timing | Subject | Personalization |
|-------|--------|---------|-----------------|
| 1 | Day 30 | "Your wallet is waiting — ${{ wallet_balance/100 }} in unclaimed rewards" | Wallet balance, lifetime stats, new merchants/campaigns |
| 2 | Day 45 | "We'll be here when you need us" | Zero pressure, preference management link |

**Post-flow:** Suppress all automated emails for 60 days. Flag for manual outreach if high-value (total_sessions > 10).

### Flow 5: Merchant Welcome + Setup (P1)

**Enrollment trigger:** `business_claimed_date` is known
**Exit condition:** `first_deal_live_date` is known OR Day 5 reached

| Email | Timing | Subject | Content |
|-------|--------|---------|---------|
| 1 | Day 0 | "Your business is on Nerava — here's what happens next" | How drivers find them, no POS required, daily cap explainer |
| 2 | Day 2 | "What should your first deal offer?" | 3 deal templates by `{{ business_type }}` |
| 3 | Day 4 | "What other merchants near EV chargers are doing" | Social proof, aggregate stats |

### Flow 6: Merchant Activation Nudge (P1)

**Enrollment trigger:** `business_claimed_date` > 5 days ago AND `first_deal_live_date` is unknown
**Exit condition:** `first_deal_live_date` becomes known

| Email | Timing | Subject | Content |
|-------|--------|---------|---------|
| 1 | Day 5 | "Drivers are charging near you right now" | `{{ nearby_charger_count }}` chargers nearby, `{{ nearest_charger_network }}` |
| 2 | Day 10 | "Can we help you get set up?" | Human touch, support link |

### Flow 7: Merchant Performance Digest (P2)

**Enrollment trigger:** `active_deal` = true OR `last_deal_active_date` within 14 days
**Frequency:** Weekly, Monday morning

| Email | Timing | Subject | Dynamic Content |
|-------|--------|---------|-----------------|
| 1 | Every Monday | "Your Nerava results — week of [date]" | `{{ weekly_driver_visits }}`, `{{ weekly_deal_claims }}`, `{{ weekly_spend }}`, week-over-week trend |

**Conditional logic:**
- Zero visits: Lead with "low driver density" explanation, not failure
- Best week ever: Celebration + prompt to increase cap
- Deal paused: Show missed traffic + one-click reactivate

### Flow 8: Merchant Re-engagement (P3)

**Enrollment trigger:** `active_deal` = false AND `last_deal_active_date` > 14 days ago
**Exit condition:** `active_deal` becomes true

| Email | Timing | Subject | Content |
|-------|--------|---------|---------|
| 1 | Day 14 | "You've been invisible to Nerava drivers for 2 weeks" | Missed driver volume, best week stats, one-click reactivate |
| 2 | Day 21 | "Is everything okay with your listing?" | Check-in, support options |

---

## Critical Issue: 34% of Drivers Have No Email

**Current state:** 19 of 29 drivers (66%) have email addresses. 10 drivers signed up via phone OTP only and have no email on file.

**Impact:** Those 10 drivers receive zero lifecycle emails. They're invisible to every flow Daniel designed.

**Solution — Dual-channel delivery:**

| Channel | Coverage | Best For |
|---------|----------|----------|
| **Email** (HubSpot) | 66% of drivers | Rich content, deal templates, weekly digests |
| **Push notifications** (APNs + FCM) | 100% of drivers with the app | Time-sensitive: post-charge recap, activation nudge |

**Recommendation:** For every flow, build a push notification equivalent as a fallback. The backend push service is already live and working on both iOS and Android. The push versions should be shorter (1-2 sentences) and link to the relevant app screen.

**Additionally:** Add an "email capture" prompt in the driver app — after first charging session, prompt "Add your email to get weekly earning reports." This converts phone-only users to email-reachable contacts.

---

## HubSpot Setup Checklist (Daniel's Tasks)

### Before Engineering Starts

- [ ] Confirm HubSpot portal ID and share with engineering
- [ ] Create a HubSpot Private App (Settings → Integrations → Private Apps)
  - Scopes needed: `crm.objects.contacts.write`, `crm.objects.contacts.read`
  - Share the access token with engineering (will be set as `HUBSPOT_PRIVATE_APP_TOKEN` env var)

### Create Contact Properties

Create these 25 custom properties in HubSpot (Settings → Properties → Create property):

**Driver properties (Contact object):**
1. `signup_date` — Date picker
2. `vehicle_connected` — Single checkbox
3. `vehicle_type` — Single-line text
4. `vehicle_make_model` — Single-line text
5. `has_tesla_connected` — Single checkbox
6. `first_verified_charge_date` — Date picker
7. `last_session_date` — Date picker
8. `last_session_earnings` — Number (store as cents)
9. `total_sessions` — Number
10. `total_rewards_earned` — Number (store as cents)
11. `total_kwh_delivered` — Number
12. `avg_session_duration_min` — Number
13. `wallet_balance` — Number (store as cents)
14. `tier_status` — Dropdown: Bronze, Silver, Gold, Platinum
15. `referral_code` — Single-line text
16. `referrals_made` — Number

**Merchant properties (Contact object or Custom object):**
17. `business_claimed_date` — Date picker
18. `first_deal_live_date` — Date picker
19. `active_deal` — Single checkbox
20. `business_type` — Dropdown: Restaurant, Cafe, Retail, Grocery, Services, Other
21. `last_deal_active_date` — Date picker
22. `total_driver_visits` — Number
23. `weekly_driver_visits` — Number
24. `weekly_deal_claims` — Number
25. `weekly_spend` — Number (store as cents)

**Calculated properties (create in HubSpot):**
- `days_since_last_session` — Calculated: today minus `last_session_date`
- `lifecycle_stage` — Managed by workflows

### Build Workflows (After Properties Are Populated)

Build in order of priority:

**P1 — Build first (highest leverage):**
1. Driver Welcome Series (3 emails)
2. Merchant Welcome + Setup (3 emails)
3. Driver Activation Nudge (2 emails)
4. Merchant Activation Nudge (2 emails)

**P2 — Build second (retention):**
5. Post-Charge Recap (1 recurring email)
6. Merchant Performance Digest (1 weekly email)

**P3 — Build third (win-back):**
7. Driver Re-engagement (2 emails)
8. Merchant Re-engagement (2 emails)

### Write Email Copy

For each flow, Daniel writes:
- Subject line (with personalization tokens like `{{ contact.vehicle_make_model }}`)
- Body copy (with dynamic content blocks)
- CTA button text and link
- Mobile-optimized design (most drivers check on phone)

---

## Engineering Implementation Plan

### Phase 1: Wire Driver Events (4 days)

| Day | Task |
|-----|------|
| 1 | Add `vehicle_connected`, `session_completed`, `first_session_completed` events to HubSpot adapter |
| 2 | Wire events into `tesla_auth.py` (vehicle connect), `session_event_service.py` (session end) |
| 3 | Add `referral_redeemed`, `wallet_credited`, `withdrawal_completed` events |
| 4 | Test full driver lifecycle in dry-run mode, verify all 16 driver properties sync |

### Phase 2: Wire Merchant Events (3 days)

| Day | Task |
|-----|------|
| 5 | Add `merchant_claimed`, `merchant_deal_published`, `merchant_deal_paused` events |
| 6 | Wire into `merchants_domain.py` (claim, exclusive CRUD) and `exclusive.py` (visit complete) |
| 7 | Build Monday morning weekly rollup job for `weekly_driver_visits`, `weekly_deal_claims`, `weekly_spend` |

### Phase 3: Enrich Existing Events + Go Live (3 days)

| Day | Task |
|-----|------|
| 8 | Add `referral_code`, `tier_status`, `wallet_balance` to existing event payloads |
| 9 | Add push notification fallbacks for email-less drivers |
| 10 | Switch `HUBSPOT_SEND_LIVE=true` on production, monitor first batch of events |

### Phase 4: Email Capture (2 days, optional)

| Day | Task |
|-----|------|
| 11 | Add "Add your email for weekly reports" prompt in driver app after first session |
| 12 | Wire email update to HubSpot contact merge (phone contact → add email) |

---

## Environment Variables (Engineering Sets These)

```
HUBSPOT_ENABLED=true
HUBSPOT_SEND_LIVE=false          # Start in dry-run, switch to true after testing
HUBSPOT_PRIVATE_APP_TOKEN=xxx    # From Daniel's Private App setup
HUBSPOT_PORTAL_ID=xxx            # From HubSpot account
```

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Driver activation rate (first charge within 7 days) | 40%+ | `first_verified_charge_date` - `signup_date` < 7 days |
| Merchant activation rate (first deal within 5 days) | 30%+ | `first_deal_live_date` - `business_claimed_date` < 5 days |
| Post-charge email open rate | 50%+ | HubSpot analytics |
| Driver 30-day retention (session in last 30 days) | 60%+ | `days_since_last_session` < 30 |
| Merchant weekly digest open rate | 40%+ | HubSpot analytics |
| Re-engagement win-back rate | 15%+ | Contacts re-entering active lifecycle stage |

---

## Timeline

| Week | Engineering | Daniel |
|------|------------|--------|
| **Week 1** | Wire driver events (Phase 1) | Create 25 HubSpot properties, set up Private App |
| **Week 2** | Wire merchant events (Phase 2) | Write email copy for P1 flows (Welcome + Nudge) |
| **Week 3** | Go live with dry-run → live (Phase 3) | Build P1 workflows in HubSpot, test with dry-run data |
| **Week 4** | Email capture prompt (Phase 4) | Build P2 + P3 workflows, activate all flows |

**First emails sending by end of Week 3. All 8 flows live by end of Week 4.**
