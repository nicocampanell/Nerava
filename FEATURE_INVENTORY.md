# Nerava Feature Inventory

> Generated 2026-04-06 from codebase analysis. Only reports what is actually implemented.

---

## 1. Session Detection and Vehicle Telemetry

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Tesla OAuth Connect | Driver app | Driver links Tesla account via OAuth, selects vehicle for charging verification | Live |
| Tesla Vehicle Select | Driver app | Multi-vehicle selection after Tesla OAuth (`VehicleSelectScreen.tsx`) | Live |
| Charging Session Polling | Driver app + API | 60s polling loop (`useSessionPolling`) calls `POST /v1/charging-sessions/poll`; backend checks Tesla Fleet API | Live |
| Session Detection & Creation | API | `SessionEventService` creates `session_events` row when charging detected, matches to nearest charger within 500m | Live |
| Session Telemetry Updates | API | Updates kWh, battery %, power_kW, location on each poll while charging | Live |
| Session End & Stale Cleanup | API | Ends session when no longer charging; auto-closes sessions not updated in 15 min | Live |
| Session Reopening | API | If car still charging within 30 min of stale close, reopens existing session instead of duplicate | Live |
| Active Session Banner | Driver app | `ActiveSessionBanner` shows current charging status on home screen | Live |
| Session History | Driver app | `SessionActivityScreen` displays past sessions with `SessionCard` components | Live |
| Session Trail Map | Driver app | `SessionTrailMap` renders GPS breadcrumbs as Leaflet polyline (points from each poll) | Live |
| Smartcar Integration | API | `ev_smartcar.py` — OAuth connect, status, disconnect, telemetry for non-Tesla EVs | Live (feature-flagged) |
| Tesla Fleet Telemetry Webhook | API | `tesla_telemetry.py` — webhook receiver for push-based vehicle telemetry | Built, not connected to live vehicles |
| Tesla Telemetry Config | API | `tesla_telemetry_config.py` — per-vehicle streaming configuration endpoint | Built, not connected |
| Fleet Telemetry Infra | Terraform | `infra/terraform/fleet-telemetry/` — ECS Fargate, NLB, Route53 | Built, not deployed |
| Tesla Mock Mode | API | `mock_tesla.py` — fake Tesla API responses for dev/demo | Live (dev only) |
| EV Verification Codes | API + Link app | `EV-XXXX` codes valid 2 hours; Link app (`apps/link/`) displays PIN in car browser | Live |
| Demo Charging | API | `demo_charging.py` — simulated charging sessions for demo mode | Live (dev/demo only) |
| Virtual Key | API | `virtual_key.py` — Tesla virtual key provisioning, status, webhook (5 endpoints, feature-flagged `FEATURE_VIRTUAL_KEY_ENABLED`) | Built, behind feature flag |
| Vehicle Onboarding | API | `vehicle_onboarding.py` — structured vehicle setup flow (3 endpoints) | Built |
| Client Telemetry | API | `client_telemetry.py` — ingests client-side events (battery, connectivity, performance) | Live |

## 2. Driver Incentives and Wallet

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Incentive Engine | API | `incentive_engine.py` evaluates ended sessions against active campaigns; highest-priority campaign wins per grant type | Live |
| Nova Points System | API | `nova_service.py` — double-entry points ledger with idempotent grants/redemptions | Live |
| Energy Reputation Tiers | Driver app + API | `EnergyReputationCard` displays Bronze/Silver/Gold/Platinum tier, streak, progress | Live |
| Driver Wallet | Driver app | `WalletModal` shows balance, pending funds, transaction history | Live |
| Wallet Ledger | Driver app + API | `useWalletLedger` fetches double-entry transaction history | Live |
| Stripe Express Payouts | Driver app + API | `requestWithdrawal` -> Stripe Transfer (min $20, max 3/day, $1000/week) | Live |
| Payout History | Driver app | `usePayoutHistory` displays past withdrawals with status | Live |
| Stripe Account Setup | Driver app + API | Create Stripe Express account + onboarding link | Live |
| Earnings Screen | Driver app | `/earnings` route — dedicated earnings/transaction history view | Live |
| Driver Campaigns View | Driver app | `useDriverCampaigns` shows available campaigns near driver location/charger | Live |
| Account Stats | Driver app | `useAccountStats` fetches driver profile stats (sessions, earnings, tier) | Live |
| Plaid Bank Linking | Driver app + API | `plaid.py` — link bank accounts via Plaid (link token, exchange, list/remove sources) | Built |
| Virtual Cards | API | `virtual_cards.py` — create virtual payment cards, view active (2 endpoints) | Built, no frontend |
| Apple Wallet Pass | API | `wallet_pass.py` — create/refresh Apple Wallet passes with charging stats (1389 lines) | Built |
| Google Wallet Pass | API | `wallet_pass.py` — create/refresh Google Wallet passes, eligibility check | Built |
| Wallet Timeline | API | `GET /wallet/timeline` returns chronological wallet events | Built |
| CLO (Card-Linked Offers) | API | `clo.py` — Fidel API: link cards, verify transactions, manage offers, webhook (8 endpoints) | Built, no frontend |

## 3. Merchant Commerce and Offers

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Merchant Acquisition Funnel | Merchant portal | `/find` -> `/preview` -> `/claim` flow for merchants to discover and claim business | Live |
| Google SSO for Merchants | Merchant portal | OAuth login via Google, callback at `/auth/google/callback` | Live |
| Business Claim | Merchant portal | `ClaimBusiness` + `SelectLocation` — search GBP/Places, claim business, create `DomainMerchant` | Live |
| Dashboard Overview | Merchant portal | `Overview` — merchant business overview with key metrics | Live |
| Exclusives Management | Merchant portal | `Exclusives` + `CreateExclusive` — CRUD for exclusive offers shown to drivers | Live |
| Merchant Visits | Merchant portal | `Visits` — view ExclusiveSession records (driver visits) with status | Live |
| Customer Exclusive View | Merchant portal | `/exclusive/:exclusiveId` — staff-facing view to verify driver's active exclusive | Live |
| Merchant Settings | Merchant portal | `Settings` — business profile, EV reward config, Toast POS connect/disconnect | Live |
| Merchant Billing | Merchant portal + API | `Billing` — payment methods, Pro subscription ($200/mo), invoice history with PDF, Stripe portal | Live |
| Merchant Pro Tier | Merchant portal | Pro subscription gates: session-level analytics, customer visit frequency, unlimited exclusives, walk traffic reports | Live |
| Magic Link Claim Verify | Merchant portal | `/claim/verify` — email-based magic link business claim verification | Live |
| EV Arrivals | Merchant portal | `EVArrivals` — curbside arrival notifications, check-in code redemption, delivery confirmation, notification settings | Built, not routed |
| Nerava Ads | Merchant portal | `NeravaAds` — ad placements with two tiers: Flat Rate ($99/mo) and CPM ($5/1K views), Stripe checkout, impression stats | Live |
| Merchant Loyalty | Merchant portal + API | `Loyalty` + `loyalty.py` — punch-card loyalty programs (create/manage cards, track progress, claim rewards) | Live |
| Toast POS Integration | Merchant portal + API | `ToastCallback` + `toast_pos.py` — connect Toast POS for AOV auto-calibration (mock default) | Built |
| Merchant Preview | Merchant portal | `MerchantPreview` — HMAC-signed preview page for acquisition funnel | Live |
| Merchant Analytics | API | `merchant_analytics.py` — merchant performance analytics | Live |
| Merchant Reports | API | `merchant_reports.py` — reporting endpoints | Built |
| Merchant Balance | API | `merchant_balance.py` — campaign balance and funding management | Live |
| Brand Image Upload | Merchant portal | `BrandImageUpload` — merchant brand photo upload | Built |
| Pickup Packages | Merchant portal | `PickupPackages` + `CreatePickupPackage` — manage pickup packages for drivers | Built |
| Merchant Insights | Merchant portal | `Insights` page — analytics/insights view | Built |
| Square POS Integration | API | `square.py` + `demo_square.py` — Square POS for check-in/redemption | Built |
| Claim Reward (Driver) | Driver app | `MerchantActionSheet` + `ClaimConfirmModal` — claim merchant offer while charging | Live |
| Active Visit Tracker | Driver app | Walking path visualization, timer, progress bar during merchant visit | Live |
| Request to Join | Driver app + API | Non-Nerava merchants show "Request to Join Nerava" button | Live |
| Claim Details | Driver app | `/claim/:sessionId` — view details of active/completed claim | Live |
| Receipt Upload | Driver app + API | `useUploadReceipt` — upload receipt photo for spend verification | Built |
| Merchant Carousel | Driver app | Horizontal merchant discovery carousel on home screen | Live |
| Merchant Details Screen | Driver app | `/merchant/:merchantId` — full merchant details | Live |
| Merchant Detail Modal | Driver app | Overlay version of merchant details | Live |
| While You Charge | Driver app + API | `/wyc` + `while_you_charge.py` — merchant deals during charging dwell time | Live |
| Ad Impressions | API | `ad_impressions.py` — record and query merchant ad impression stats | Live |

## 4. Sponsor and Campaign Tooling

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Console Login | Console | `Login` + `console_auth.py` — sponsor authentication | Live |
| Console Dashboard | Console | `Dashboard` — campaign overview (active count, sessions funded, budget remaining, avg cost/session) | Live |
| Campaign List | Console | `Campaigns` — table with status filtering, budget bars, session counts | Live |
| Create Campaign Wizard | Console | `CreateCampaign` — 4-step wizard (Details -> Targeting -> Budget -> Review) with geofencing, time, charger network, driver caps | Live |
| Campaign Detail | Console | `CampaignDetail` — KPIs, grants list, pause/resume, rule editing | Live |
| Charger Explorer | Console | `ChargerExplorer` — interactive map with network-colored pins, search, deep-link to campaign creation | Live |
| Console Billing | Console | `Billing` — budget overview and spending transaction table | Live |
| Console Settings | Console | `Settings` — account info, API docs link, placeholders for team management | Live |
| Campaign CRUD API | API | `campaigns.py` — full lifecycle (create, update, pause, resume, archive) | Live |
| Campaign Sessions API | API | `campaign_sessions.py` — sessions with incentive/campaign data | Live |
| Corporate Classifier | API | `corporate_classifier.py` — classify corporate vs local targeting | Built |
| Admin Campaigns | Admin | `CampaignsPage` — admin view of all campaigns across sponsors | Live |
| Charger Portal (Nova) | Charger portal | Standalone Next.js portal for charger owners with savings dashboard, Nova budget purchase, sessions table, charts | Built, not exposed (all data mocked) |

## 5. Data Capture and Analytics

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| PostHog Analytics | All apps | `packages/analytics` — shared PostHog wrapper, 35+ event types in `events.ts` | Live |
| PostHog Events Relay | API | `posthog_events.py` — server-side PostHog event relay (geofence events) | Live |
| Admin Dashboard | Admin | `Dashboard` — KPI cards (drivers, merchants, chargers, sessions, revenue), charts | Live |
| Admin Users | Admin | `UsersPage` — user search, wallet view, balance adjustment | Live |
| Admin Merchants | Admin | `Merchants` — merchant management, Square status, Nova balance | Live |
| Admin Chargers | Admin | `ChargersPage` + `ChargingLocations` — charger CRUD, merchant linking, Places mapping | Live |
| Admin Active Sessions | Admin | `ActiveSessions` — view currently active charging sessions | Live |
| Admin Exclusives | Admin | `Exclusives` — manage all exclusive offers with pause/resume | Live |
| Admin Overrides | Admin | `Overrides` — force-close sessions, emergency pause with confirmation | Live |
| Admin Deployments | Admin | `Deployments` — trigger deploys to backend, driver, admin, merchant | Live |
| Admin Logs | Admin | `Logs` — audit log viewer with search and filtering | Live |
| Admin Seed Manager | Admin | `SeedManager` — seed chargers/merchants by state | Live |
| Admin Demo Location | Admin | Demo mode location override for testing | Live |
| Admin Analytics API | API | `admin_analytics.py` — admin analytics endpoints | Live |
| Admin Chargers API | API | `admin_chargers.py` — admin charger management | Live |
| Admin Partners | API | `admin_partners.py` — partner + API key CRUD | Live |
| Feature Flags | API | `flags.py` — environment-based flags with admin toggle | Live |
| Grid Metrics | API | `grid.py` — charging grid metrics: current, time-series, impact summary (183 lines) | Built |
| ML Recommendations | API | `ml.py` — ML-based hub and perk recommendations (105 lines) | Built |
| Insights API | API | `insights_api.py` — event and merchant insights (26 lines, minimal) | Built, minimal |
| Client Telemetry | API | `client_telemetry.py` — ingest client-side performance/error events | Live |

## 6. Social, Community, and Crowdsourced Features

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Social Follow/Feed | API | `social.py` — follow users, followers/following, feed, pool (5 endpoints, 106 lines) | Built, no frontend |
| Challenges | API | `challenges.py` — create/join challenges, credit, leaderboard, active list (5 endpoints, 211 lines) | Built, no frontend |
| Community Events | API | `events_api.py` — create events, join events (155 lines) | Built, no frontend |
| Amenity Voting | Driver app + API | `useVoteAmenity` — vote on charger amenities (WiFi, restrooms, etc.) | Built |
| Charger Discovery | Driver app | `Discovery` component — charger browsing with map/list toggle | Live |
| Charger Detail Sheet | Driver app | `ChargerDetailSheet` — charger info with nearby merchants, amenities tab | Live |
| Charger Map | Driver app | `ChargerMap` — Leaflet map with charger/merchant/user pins (OpenStreetMap tiles) | Live |
| Charger Search | Driver app + API | `searchChargers` — text search with geocoding | Live |
| Charger Favorites | Driver app + API | `toggleChargerFavorite` / `fetchChargerFavorites` | Live |
| GPT Finder | API | `gpt.py` — AI-powered merchant/charger search, session links, social (7 endpoints, 383 lines) | Built |

## 7. Referral and Growth Mechanics

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Referral Code System | Driver app + API | `referrals.py` — get code, view stats, redeem codes (3 endpoints, 78 lines) | Built |
| Referral Join Route | Driver app | `/join?ref=CODE` stores code in sessionStorage, redirects home | Built |
| Referral Hooks | Driver app | `useReferralCode` + `useReferralStats` — React Query hooks | Built |
| Affiliate Tracking | API | `affiliate_api.py` — track clicks, notifications (2 endpoints, 18 lines) | Built, minimal |
| Onboarding Flow | Driver app | `OnboardingGate` + `OnboardingFlow` — new user onboarding screens | Live |
| Preferences | Driver app | `Preferences` component — driver preference capture | Live |
| Account Page | Driver app | `AccountPage` — profile, favorites, settings, login/logout | Live |
| Login Modal | Driver app | Phone OTP + Apple Sign-In + Google Sign-In | Live |
| Consent Management | API | `consent.py` — granular consent (data, marketing, location) with grant/revoke (186 lines) | Built |
| Merchant Funnel | API | `merchant_funnel.py` — public acquisition funnel endpoints | Live |

---

## Partner API (External Integration Surface)

| Feature | Surface | Description | Status |
|---------|---------|-------------|--------|
| Partner Session Ingest | Partner API | `POST /v1/partners/sessions` — submit external charging sessions (idempotent) | Live |
| Partner Session List/Detail | Partner API | `GET /v1/partners/sessions`, `GET /sessions/{id}` — query partner sessions | Live |
| Partner Session Update | Partner API | `PATCH /v1/partners/sessions/{id}` — update telemetry or complete session | Live |
| Partner Grants | Partner API | `GET /v1/partners/grants` — list grants for partner sessions | Live |
| Partner Available Campaigns | Partner API | `GET /v1/partners/campaigns/available` — campaigns matching partner trust tier | Live |
| Partner Profile | Partner API | `GET /v1/partners/me` — profile + usage stats | Live |
| Partner Auth | API | `X-Partner-Key` header, SHA-256 hashed `nrv_pk_` prefix keys, scope-checked | Live |

---

## Uncategorized / Unexpected

| Feature | Location | Description | Status |
|---------|----------|-------------|--------|
| Link App (Car PIN) | `apps/link/` | Standalone app for EV car browser — generates `EV-XXXX` PIN codes for phone check-in | Built |
| Landing Page | `apps/landing/` | Marketing site: hero, driver/merchant/sponsor sections, download CTA, plus `/privacy`, `/terms`, `/support` | Live |
| iOS WKWebView Shell | `Nerava/` | Native iOS app wrapping driver web app with JS bridge, push, location, geofencing | Live |
| Android WebView Shell | `mobile/nerava_android/` | Native Android app mirroring iOS: WebView + FCM + native bridge + geofencing | Live |
| Charger Portal | `charger-portal/` | Next.js charger owner portal (savings dashboard, Nova budget, sessions, charts) — all data mocked | Built, not exposed |
| EnergyHub | API | `energyhub.py` — charge start/stop events, charging windows (utility/grid integration, 180 lines) | Built, no frontend |
| Dual Zone | API | `dual_zone.py` — experimental dual-zone session tracking (50 lines, feature-flagged) | Built, behind flag |
| Reservations | API | `reservations.py` — soft-reserve charger slots (1 endpoint, 38 lines) | Built, minimal |
| Pool API | API | `pool_api.py` — charging pool summary and ledger (160 lines) | Built |
| Discover API | API | `discover_api.py` — discovery feed endpoint (33 lines) | Built, minimal |
| Offers API | API | `offers_api.py` — nearby offers endpoint (60 lines) | Built |
| Recommend API | API | `recommend.py` — location-based recommendation | Built |
| Hubs API | API | `hubs.py` — recommended/nearby charging hubs | Built |
| Push Notifications | API | `notifications.py` — dispatch push to APNs/FCM (nearby merchant alerts on charging) | Live |
| Device Token Registration | Driver app + API | `registerDeviceToken` — register APNs/FCM tokens | Live |
| Twilio SMS Webhook | API | `twilio_sms_webhook.py` — inbound SMS handling | Built |
| Stripe Webhooks | API | `stripe_webhooks.py` + `purchase_webhooks.py` — payment event processing | Live |
| Phone Check-in | Driver app | `/s/:token` — SMS-linked check-in flow | Built |
| EV Home | Driver app | `/ev-home` — dedicated EV-centric home view | Built |
| EV Order Flow | Driver app | `/ev-order` — EV order creation flow | Built |
| Pre-Charging Screen | Driver app | `/pre-charging` — charger selection and session prep | Built |
| Merchant Arrival | Driver app | `/m/:merchantId` — phone-first EV arrival flow | Built |
| Tesla Telemetry (Driver) | Driver app + API | `configureTelemetry` — driver-initiated telemetry subscription | Built |
| Primary Experience | Merchant portal | `PrimaryExperience` — primary experience configuration component | Built |
| Loom Modal | Merchant portal | `LoomModal` — embedded Loom video explainer in merchant portal | Built |
| Demo Nav | Merchant portal | `DemoNav` — demo mode navigation (conditional on `VITE_DEMO_MODE`) | Built (dev only) |
| Debug Tools | Admin + API | `dev_tools.py`, `debug_verify.py`, `debug_pool.py`, `analytics_debug.py` | Built (dev only) |
| Health / Meta | API | `health.py` + `meta.py` — health check, version, debug endpoints | Live |
| Bootstrap | API | `bootstrap.py` — initial data bootstrap for demo/dev | Built |
| Seed Scripts | Backend scripts | `seed_chargers_bulk.py`, `seed_merchants_free.py`, `seed_if_needed.py` | Built (ops) |
| Daily Prod Report | CI/Script | `daily_prod_report.py` — CloudWatch daily digest -> SNS | Live |
| Prod Health Check | CI/Script | `prod_api_health_check.py` — API health check for GitHub Actions | Live |

---

## Status Summary

| Status | Count |
|--------|-------|
| **Live in production** | ~65 features |
| **Built but not exposed / no frontend** | ~30 features |
| **Built, behind feature flag** | 3 (virtual key, dual zone, Smartcar) |
| **Built, minimal stub (< 50 lines)** | 5 (affiliate, discover, reservations, insights, dual zone) |
| **Infrastructure built, not deployed** | 1 (Fleet Telemetry) |
