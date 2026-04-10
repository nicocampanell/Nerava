# Nerava Infrastructure & Product Gap Analysis

**Date:** April 3, 2026
**Purpose:** Audit reality against the stated promise — "the Stripe for EV dwell time" — across product, infrastructure, data, legal, commercial, and financial dimensions.
**Methodology:** Full codebase audit, CloudWatch log analysis, production data review. No production DB access (RDS in VPC).

---

## SECTION 1 — PARTNER API AND SDK AUDIT

The core promise: any operator can integrate Nerava in days.

### What Exists

| Component | Status | Evidence |
|-----------|--------|----------|
| Partner REST API | **13 endpoints live** | 6 partner-facing + 7 admin-only (`partner_api.py`, `admin_partners.py`) |
| API Key Auth | **Implemented** | `nrv_pk_{32_hex}` format, SHA-256 hashed, scope-checked per endpoint |
| Webhook Delivery | **Implemented** | Fire-and-forget with 3 retries, exponential backoff (`webhook_delivery_service.py`) |
| HMAC-SHA256 Signing | **Implemented** | `X-Nerava-Signature: sha256={hexdigest}`, `X-Nerava-Event`, `X-Nerava-Event-Id` headers |
| Shadow Driver Creation | **Implemented** | `partner_{slug}_{driver_id}@partner.nerava.network`, auto-created on first session |
| Idempotent Session Ingest | **Implemented** | `(source, source_session_id)` unique constraint, 409 on conflict |

### Gaps

| # | Gap | Current Reality | Done Looks Like | Effort | Priority |
|---|-----|----------------|-----------------|--------|----------|
| 1.1 | **No OCPP webhook receiver** | Zero OCPP code exists. The word appears once in a comment. | OCPP 1.6-J StatusNotification + MeterValues receiver, OCPP 2.0.1 TransactionEvent receiver, both mapping to SessionEvent creation. | 5-8 days | **P0** |
| 1.2 | **No iframe wallet embed** | No embed, widget, or white-label code exists. All wallet UI is in the driver SPA. | Standalone `/embed/wallet?partner_key=X&driver_id=Y&theme=dark` route that renders wallet balance + payout UI in an iframe-friendly page with postMessage API. | 5-7 days | **P1** |
| 1.3 | **No self-service partner onboarding** | All onboarding requires admin JWT + manual API calls. | Public partner signup page → API key generation → webhook config → test session → go-live checklist. | 8-10 days | **P1** |
| 1.4 | **No sandbox environment** | Partners hit production immediately. Soft-signal `status="candidate"` exists but creates real DB records. | Separate test API keys (`nrv_tk_*`) that hit isolated test tables or an in-memory sandbox with synthetic session generation. | 5-7 days | **P1** |
| 1.5 | **No published API documentation** | FastAPI auto-generates `/docs` and `/redoc` but they're not formally published. No integration guide, no code samples. | Hosted API reference (e.g., `docs.nerava.network`), partner integration guide, webhook event catalog, error code reference, Python/Node.js code samples. | 5-7 days | **P1** |
| 1.6 | **No partner SDK** | Partners must implement REST calls, auth, HMAC verification, retry logic, and idempotency from scratch. | `@nerava/partner-sdk` (npm) and `nerava-partner` (PyPI) with typed session submission, webhook verification, and grant polling. | 7-10 days | **P2** |
| 1.7 | **No partner dashboard** | Partners can only interact via API. No UI to see sessions, grants, usage, webhook logs. | Partner portal showing session volume, grant rates, campaign matches, webhook delivery history, API key management. | 10-14 days | **P2** |

**Actual time-to-integration today:** 2-3 weeks for a competent developer, assuming James is available for provisioning, answering questions, and debugging. Blockers: no OCPP receiver (Trident's chargers speak OCPP), no docs, no sandbox.

---

## SECTION 2 — FIVE REVENUE STREAM READINESS

### Stream 1: Fleet SaaS ($4/vehicle/month) — NOT BUILT

| Aspect | Status |
|--------|--------|
| Fleet operator account model | Does not exist |
| Vehicle enrollment system | Does not exist (only Tesla OAuth for individual drivers) |
| Recurring Stripe billing per vehicle | Does not exist |
| Fleet dashboard | Does not exist |
| Smartcar multi-OEM integration | Code exists (`smartcar_service.py`) but not validated at scale beyond Tesla |
| Self-service vehicle onboarding | Does not exist |

**Current reality:** There is no fleet SaaS product. The pitch claims $4/vehicle/month but there is no model, no billing, no dashboard, and no way for a fleet operator to enroll vehicles. The only vehicle connection flow is individual Tesla OAuth for consumer drivers.

**To close:** Fleet operator model, vehicle batch enrollment API, Stripe recurring billing tied to vehicle count, fleet analytics dashboard. **Effort: 6-8 weeks. Priority: P1.**

---

### Stream 2: Insurance Behavioral Data ($3-5/vehicle/month) — NOT BUILT

| Aspect | Status |
|--------|--------|
| Structured data product | Does not exist |
| Data anonymization service | Does not exist |
| Bulk export API (CSV/Parquet) | Does not exist (`POST /v1/account/export` returns user's own data in JSON, not a data product) |
| Insurance partner model | Does not exist |
| Data licensing agreement | Does not exist |
| CCPA-compliant consent per data record | Per-type consent exists (analytics/marketing), not per-record |
| Schema documentation | Does not exist as a standalone artifact |

**Current reality:** Session data is comprehensive (30+ columns per session including vehicle, charger, energy, location, telemetry, quality score). Consent infrastructure exists. But there is no anonymization pipeline, no export API for data buyers, no licensing logic, and no insurance partner has seen the data.

**To close:** Data anonymization service, bulk export API with partner auth, data product schema documentation, insurance partner account model. **Effort: 4-6 weeks. Priority: P2.**

---

### Stream 3: Location Intelligence ($200/location/month) — PARTIALLY BUILT

| Aspect | Status |
|--------|--------|
| Per-location session data | Available (indexed on charger_id + location) |
| Dwell time computation | Available via `merchant_analytics.py` |
| Admin analytics dashboard | Exists (`admin_analytics.py` — daily aggregations) |
| Location owner self-service dashboard | Does not exist |
| Subscription billing | Does not exist for location intelligence specifically |
| API for location partners | Does not exist |

**Current reality:** The data exists and is queryable by admin. No location partner can self-serve. No subscription billing tied to location intelligence.

**To close:** Location partner onboarding, self-service analytics dashboard, per-location API endpoints, subscription billing. **Effort: 3-4 weeks. Priority: P2.**

---

### Stream 4: Merchant Commerce (4-6% transaction fee) — CLOSABLE TODAY

| Aspect | Status |
|--------|--------|
| Merchant self-onboarding | **Live** — Google SSO claim flow, DomainMerchant creation |
| Exclusive offer creation | **Live** — ChargerMerchant links, merchant portal CRUD |
| Driver claim + presence verification | **Live** — GPS-verified charging + 250-350m proximity |
| Visit tracking dashboard | **Live** — Visits page shows verified visits with status |
| Toast POS integration (AOV calibration) | **Live** — OAuth + order data, mock mode for dev |
| Stripe billing for merchants | **Live** — Card on file setup, Pro tier subscriptions |
| Automated per-claim charge deduction | **Partially built** — billing infrastructure exists, charge-per-claim automation needs wiring |

**Current reality:** This is the most mature revenue stream. A merchant can claim their business, set up an exclusive offer, and see verified driver visits. The billing infrastructure exists but automated per-claim charging needs final wiring.

**To close:** Wire automated claim-based charge deduction. **Effort: 3-5 days. Priority: P0.**

---

### Stream 5: Sponsor Campaigns (20% platform fee) — CLOSABLE TODAY

| Aspect | Status |
|--------|--------|
| Self-serve campaign creation | **Live** — Full 4-step wizard in console app |
| 12+ targeting rules | **Live** — Charger, zone, time, day, duration, network, connector, driver caps, allowlist, partner controls |
| Budget enforcement | **Live** — `SELECT FOR UPDATE` atomic decrement, auto-pause on exhaustion |
| Stripe funding | **Live** — Checkout → webhook → auto-activate |
| Campaign reporting | **Live** — Real-time spent_cents, grant count, cost per session |
| Grant clawback | **Live** — Per-grant reversal on fraud/invalidation |

**Critical finding from April 2026 audit:** `PLATFORM_AUDIT_APRIL_2026.md` reports that `spent_cents` was not incrementing correctly — a campaign was 5x over budget but still active. The atomic `decrement_budget_atomic()` code exists and uses `SELECT FOR UPDATE`, but there may be a code path that bypasses it.

**To close:** Verify and fix the budget counter bug identified in the April audit. **Effort: 1-2 days. Priority: P0.**

---

### Revenue Stream Summary

| Stream | Status | Closable Today | Effort to Close | Priority |
|--------|--------|---------------|-----------------|----------|
| Fleet SaaS | Not built | No | 6-8 weeks | P1 |
| Insurance Data | Not built | No | 4-6 weeks | P2 |
| Location Intelligence | Partially built | No | 3-4 weeks | P2 |
| Merchant Commerce | 95% built | Yes (days) | 3-5 days | P0 |
| Sponsor Campaigns | Live | Yes (bug fix) | 1-2 days | P0 |

**Of the five revenue streams promised to partners and investors, two are closable today and three do not exist as products.**

---

## SECTION 3 — DATA INFRASTRUCTURE AUDIT

### What Exists (Strong)

| Requirement | Status | Evidence |
|-------------|--------|----------|
| Complete session data record | **Excellent** | 30+ columns: vehicle_id, vehicle_vin, vehicle_make/model/year, duration_minutes, kwh_delivered, lat/lng, charger_network, connector_type, power_kw, battery_start/end_pct, session_start/end, quality_score, source, verification_method |
| Anti-fraud quality scoring | **Implemented** | `quality_score` (0-100) computed on session end. Device fingerprinting, geo-jump detection, verify attempt tracking (`fraud.py`) |
| Double-entry ledger | **Implemented** | `wallet_ledger` (USD) + `nova_transactions` (Nova points), both immutable with timestamps and references |
| Consent management | **Implemented** | Per-type consent (analytics, marketing) with grant/revoke timestamps, IP logging, privacy policy versioning |
| Data export | **Implemented** | `POST /v1/account/export` returns JSON with full behavioral dataset per user |
| Data retention policy | **Implemented** | 7-day to 1-year TTL by record type (`data_retention.py`), with anonymization for location data |

### Gaps

| # | Gap | Current Reality | Done Looks Like | Effort | Priority |
|---|-----|----------------|-----------------|--------|----------|
| 3.1 | **No VIN hashing** | `vehicle_vin` stored as raw text in `session_events` | VIN hashed (SHA-256 + salt) at ingest time; raw VIN never persisted in behavioral dataset | 1 day | **P1** |
| 3.2 | **No automated data retention** | `data_retention.py` exists but must be manually invoked — no scheduler | Cron job or App Runner startup task runs retention daily | 1 day | **P1** |
| 3.3 | **No bulk data export for partners** | Export is per-user JSON only. No CSV/Parquet bulk export for insurance/data buyers | Bulk export API with partner auth, anonymization, date range filtering, CSV/Parquet output | 5-7 days | **P2** |
| 3.4 | **No consent-gated data access** | Consent exists but no check gates data reads/exports. A data buyer could theoretically receive data from non-consented users | Data export pipeline checks `UserConsent.is_granted("analytics")` before including each user's records | 2-3 days | **P1** |
| 3.5 | **No automated reconciliation** | No job comparing `SUM(wallet_ledger)` vs `DriverWallet.balance_cents` or `SUM(nova_transactions)` vs `nova_balance` | Nightly reconciliation job that flags mismatches and alerts via SNS | 3-4 days | **P1** |
| 3.6 | **No sponsor refund for unspent budget** | `clawback_grant()` reverses individual grants, but sponsors cannot withdraw unspent campaign balance | `POST /v1/campaigns/{id}/refund` returns remaining balance to sponsor's payment method via Stripe | 3-4 days | **P1** |

### Dataset Size

As of March 15, 2026: **68 sessions, 8 drivers, 625.5 kWh delivered.** Growth rate: ~15-20 sessions/week. This is pre-product-market-fit scale. The data infrastructure is overbuilt for current volume (which is correct for a platform play) but the dataset itself is not yet commercially meaningful for insurance or location intelligence buyers.

---

## SECTION 4 — FINANCIAL INFRASTRUCTURE AUDIT

### What Works

| Component | Status | Evidence |
|-----------|--------|----------|
| Atomic budget enforcement | **Live** | `decrement_budget_atomic()` with `SELECT FOR UPDATE`, pre-check, auto-pause |
| Driver wallet & payouts | **Live** | `get_or_create_wallet`, Stripe Express onboarding, min $1 withdrawal, daily/weekly limits |
| Double-entry ledger | **Live** | `wallet_ledger` (USD credits/debits) + `nova_transactions` (points) |
| Grant idempotency | **Live** | `session_event_id` + `idempotency_key` + `payload_hash` uniqueness constraints |
| Stripe funding | **Live** | Checkout → webhook → auto-activate campaign, fee-inclusive (20% platform fee deducted) |
| Payout fee schedule | **Live** | Free above $20, $0.25 + 0.25% below |

### Critical Issues

| # | Issue | Current Reality | Impact | Effort | Priority |
|---|-------|----------------|--------|--------|----------|
| 4.1 | **Budget counter bug** | April 2026 audit found `spent_cents` not incrementing correctly — New-User-Campaign was 5x over budget ($8 spent vs $1.60 budget) but still active | Sponsors are being overcharged; trust-breaking for first paid sponsor | 1-2 days | **P0** |
| 4.2 | **14 failed payouts** | All 14 recent payouts failed with "Insufficient funds in Stripe account" | Drivers cannot withdraw rewards; trust-breaking | 1 day | **P0** |
| 4.3 | **Stuck payout (3/27)** | One payout in `processing` state for 6+ days with no resolution | Driver's funds locked in pending_balance_cents | 0.5 days | **P0** |
| 4.4 | **No payout monitoring** | Failed payouts sit in DB with no alerting | Silent failures accumulate until a user complains | 2 days | **P1** |
| 4.5 | **No sponsor refund flow** | Sponsors cannot reclaim unspent campaign budget | Legal liability if sponsor wants money back | 3-4 days | **P1** |
| 4.6 | **No automated reconciliation** | No balance vs ledger audit | Silent balance drift possible | 3-4 days | **P1** |
| 4.7 | **Test diagnostic button in production** | Orange "Test Push" button visible in driver AccountPage, leaks device token info | Security/UX issue for any user who sees it | 0.5 days | **P0** |

---

## SECTION 5 — LEGAL AND COMPLIANCE GAPS

### What Exists

| Document | Status | Location |
|----------|--------|----------|
| Privacy Policy | **Published** | `apps/landing/app/privacy/page.tsx` (Feb 17, 2026) — covers location data, Tesla API, behavioral profiling |
| Terms of Service | **Published** | `apps/landing/app/terms/page.tsx` (Mar 11, 2026) — covers prohibited conduct, fraud prevention |
| CCPA Account Deletion | **Implemented** | `DELETE /v1/account` — anonymizes PII, deletes tokens, logs audit trail |
| GDPR Data Export | **Implemented** | `POST /v1/account/export` — JSON with full behavioral dataset |
| Consent Management | **Implemented** | Grant/revoke endpoints with timestamps, IP logging |
| PII Scrubbing (Sentry) | **Implemented** | Regex patterns strip phone numbers, JWT tokens, Stripe keys |
| iOS Privacy Manifest | **Implemented** | `PrivacyInfo.xcprivacy` — declares location tracking, UserDefaults |

### Gaps

| # | Gap | Blocks | Who Acts | Effort | Priority |
|---|-----|--------|----------|--------|----------|
| 5.1 | **No Master Services Agreement (MSA)** | Blocks Trident partnership — no legal framework for operator integration | James + Attorney | 1-2 weeks | **P0** |
| 5.2 | **No Data Processing Agreement (DPA)** | Blocks any operator handling EU drivers or requiring GDPR compliance | Attorney | 1 week | **P1** |
| 5.3 | **No data licensing agreement** | Blocks insurance data sales — no legal framework for data buyer access | James + Attorney | 1-2 weeks | **P2** |
| 5.4 | **No contractor IP assignment** | Risk that code contributors claim ownership of Nerava IP | James + Attorney | 3-5 days | **P1** |
| 5.5 | **SAFE not reviewed by attorney** | Blocks investor closes if terms have issues | Attorney | 1 week | **P1** |
| 5.6 | **No formal option plan** | Cannot make enforceable equity grants to team members | James + Attorney | 2-3 weeks | **P1** |
| 5.7 | **Parker consulting agreement — governing law** | If Maine instead of Delaware, creates jurisdictional complexity | James + Attorney | 1 day | **P1** |
| 5.8 | **Android privacy policy not linked** | Play Store submission will be rejected without app-specific privacy link | James | 1 day | **P0** |

---

## SECTION 6 — OPERATOR EXPERIENCE GAPS

### What Exists

| Component | Status |
|-----------|--------|
| Admin dashboard (`admin.nerava.network`) | **Live** — merchants, users, sessions, campaigns, chargers, analytics |
| Sponsor console (`console.nerava.network`) | **Live** — campaign CRUD, targeting builder, budget tracking, charger explorer |
| Auto-generated API docs | **Available** at `/docs` (Swagger) and `/redoc` (ReDoc) — not formally published |
| Admin endpoint inventory | **Documented** — `docs/api/ADMIN_ENDPOINTS_INVENTORY.md` (50+ endpoints) |
| Health check workflow | **Live** — GitHub Actions pings all prod endpoints every 30 min |

### Gaps

| # | Gap | Current Reality | Done Looks Like | Effort | Priority |
|---|-----|----------------|-----------------|--------|----------|
| 6.1 | **No published API reference** | FastAPI auto-docs exist but are not published, branded, or versioned | `docs.nerava.network` with versioned API reference, webhook catalog, integration guide, code samples | 5-7 days | **P0** |
| 6.2 | **No public status page** | No uptime visibility for operators. Health checks run but results are internal GitHub issues. | `status.nerava.network` showing current uptime, incident history, component status | 2-3 days | **P1** |
| 6.3 | **No partner portal** | Partners interact only via API. No UI for sessions, grants, webhooks, API keys. | Self-service portal: API key management, session explorer, webhook logs, usage analytics | 10-14 days | **P2** |
| 6.4 | **No campaign test mode** | Sponsors must commit real budget to test targeting rules | `test: true` flag on campaign creation — runs matching without budget impact, returns simulated results | 3-4 days | **P1** |
| 6.5 | **No onboarding checklist** | Onboarding is ad-hoc, requiring James's direct involvement at every step | Automated checklist: sign MSA → create account → generate API key → configure webhook → submit test session → verify grant → go live | 5-7 days | **P1** |

**Current onboarding process (step by step):**
1. James has a call with the operator
2. James creates partner record via admin API
3. James generates API key and sends it manually
4. James explains the API over email/call (no docs)
5. Operator's developer implements from scratch (no SDK)
6. James manually debugs integration issues
7. James manually activates partner status

**Every step requires James.** This does not scale past 2-3 partners.

---

## SECTION 7 — DRIVER EXPERIENCE GAPS

### What Exists

| Component | Status |
|-----------|--------|
| Session polling & detection | **Live** — 60s poll via Tesla Fleet API, auto-creates/ends sessions |
| Session history | **Live** — Scrollable list with duration, kWh, incentive status, GPS trail map |
| Energy reputation (tiers/streaks) | **Live** — Bronze/Silver/Gold/Platinum, streak tracking |
| Wallet & payouts | **Live** — Stripe Express onboarding, withdrawal with limits |
| Push notifications | **Live** — Dual-platform (APNs + FCM) |
| Merchant discovery | **Live** — Carousel, charger detail amenities, distance/walk time |
| Claim reward flow | **Live** — GPS-verified charging + proximity, exclusive session lifecycle |
| Referral system | **Live** — QR code + share link, $5 mutual credit |
| Account deletion | **Live** — CCPA-compliant anonymization |
| Analytics (PostHog) | **Live** — Anonymous tracking from first app open, identify on login |

### Gaps

| # | Gap | Current Reality | Done Looks Like | Effort | Priority |
|---|-----|----------------|-----------------|--------|----------|
| 7.1 | **No-app integration incomplete** | Shadow drivers get `reward_destination="partner_managed"` but there's no partner-facing wallet or payout UI. The partner must build their own reward delivery. | Iframe wallet embed (see 1.2) or hosted reward page at `rewards.nerava.network/{partner}/{driver}` | 5-7 days | **P1** |
| 7.2 | **Android not on Play Store** | Build and signing config ready. Firebase configured. App icons are placeholders. | Real app icons, Play Store listing, privacy policy link, published to Play Store | 3-5 days | **P0** |
| 7.3 | **No in-app merchant checkout** | Driver claims offer, walks to merchant, shows code manually. No in-app purchase flow. | In-app browser checkout or Stripe payment link for merchant purchases | 10-14 days | **P2** |
| 7.4 | **No receipt OCR** | Designed (Taggun API, $0.04/scan) but not implemented. Spend verification is manual only. | `POST /v1/receipts/verify` with image upload, OCR extraction, merchant/amount/timestamp matching | 7-10 days | **P2** |
| 7.5 | **Demo/test features in production** | `?demo=1` URL param enables mock charging. Orange "Test Push" button visible in Account page. | Strip all demo features from production builds via `VITE_ENV` build-time flag | 0.5 days | **P0** |
| 7.6 | **APP_FIRST_OPEN event missing** | Cannot distinguish new installs from returning users in analytics. This is the blind spot you noticed — download ≠ app open ≠ signup. | Fire one-time `APP_FIRST_OPEN` event on first launch, track onboarding funnel (screen viewed → login shown → login completed) | 1 day | **P1** |

**Driver activation rate:** Cannot be computed from available data. 68 sessions from 8 drivers over ~3 weeks = strong engagement from those connected, but total connected vehicles and conversion funnel are not tracked.

---

## SECTION 8 — COMPETITIVE AND STRATEGIC GAPS

### What would cause a sophisticated acquirer to pass?

**The dataset is not yet commercially meaningful.** 68 sessions from 8 drivers is a prototype, not a data asset. An acquirer (Tesla, Amazon, Upside, ChargePoint) would look at session volume, driver count, geographic diversity, and data completeness. At current scale, the behavioral dataset is indistinguishable from a demo. The architecture is correct — the data model is comprehensive, the ledger is sound, the incentive engine is production-grade — but the volume is pre-PMF.

**The weakest claim in pitch materials:** "5,000 connected vehicles live on iOS and Android" and "$5-8 revenue per vehicle per month." The production database shows 8 active drivers and 68 sessions. If the 5,000 figure refers to EVject hardware units deployed (not Nerava-connected vehicles), the pitch conflates hardware distribution with software activation, which a diligence process will catch.

### What would cause Trident to walk away?

**No OCPP receiver.** Trident's chargers speak OCPP. The Trident brief says "OCPP webhook receiver: needs OCPP version confirmation" and marks it as "READY TO DEPLOY (pending Trident input)." In reality, zero OCPP code exists in the codebase. If Trident's developer asks "where do I point my OCPP StatusNotification messages?" the answer today is "nowhere." This is a day-one blocker for the partnership.

### What metric would most accelerate the Kreg equity swap?

**Verified sessions per week from a non-Nerava operator.** One partner (EVject or Trident) submitting real sessions through the Partner API and receiving grants would prove the platform thesis — that Nerava is infrastructure others build on, not just a consumer app. 100 partner-submitted sessions/week with verified incentive matching would be more valuable than 10,000 direct-app sessions for the equity swap conversation.

### What is the weakest claim relative to what is built?

**"Five simultaneous revenue streams from a single charging session."** Two of five streams are closable today (Merchant Commerce and Sponsor Campaigns). Three do not exist as products (Fleet SaaS, Insurance Data, Location Intelligence). The pitch implies all five are live or near-live. A technical diligence review would find that 60% of the stated revenue model has not been built.

---

## PRIORITIZED BUILD LIST

### P0 — Blocks First Partner or First Deal

| # | Item | Owner | Est. Days | Target Date |
|---|------|-------|-----------|-------------|
| P0-1 | **Fix campaign budget counter bug** — verify `spent_cents` increments on every grant path | Engineer | 1-2 | Apr 8 |
| P0-2 | **Resolve 14 failed payouts + 1 stuck payout** — fund Stripe balance, reconcile driver wallets | James + Engineer | 1 | Apr 7 |
| P0-3 | **Remove test diagnostic button from production** — strip demo features | Engineer | 0.5 | Apr 7 |
| P0-4 | **Build OCPP 1.6-J webhook receiver** — StatusNotification + MeterValues → SessionEvent | Engineer | 5-8 | Apr 16 |
| P0-5 | **Wire automated per-claim merchant billing** — charge card on file per verified visit | Engineer | 3-5 | Apr 14 |
| P0-6 | **Publish API reference at docs.nerava.network** — versioned docs, webhook catalog, integration guide | Engineer | 5-7 | Apr 16 |
| P0-7 | **Create MSA template for operator partners** — legal framework for Trident integration | James + Attorney | 7-10 | Apr 18 |
| P0-8 | **Publish Android to Play Store** — real icons, privacy policy link, Play Store listing | Engineer + James | 3-5 | Apr 11 |
| P0-9 | **Link Android privacy policy** — required for Play Store submission | James | 1 | Apr 7 |

### P1 — Required Before $500K SAFE Closes

| # | Item | Owner | Est. Days | Target Date |
|---|------|-------|-----------|-------------|
| P1-1 | **Build iframe wallet embed for partners** — white-label, URL-param theming | Engineer | 5-7 | Apr 25 |
| P1-2 | **Build self-service partner onboarding** — signup → API key → webhook config → test session | Engineer | 8-10 | May 2 |
| P1-3 | **Add partner sandbox/test mode** — test API keys, synthetic sessions, no budget impact | Engineer | 5-7 | Apr 25 |
| P1-4 | **Add campaign test mode** — dry-run matching without budget commitment | Engineer | 3-4 | Apr 18 |
| P1-5 | **Add payout monitoring + alerts** — CloudWatch alarms on failed payouts | Engineer | 2 | Apr 11 |
| P1-6 | **Automate data retention job** — cron/scheduled task for `data_retention.py` | Engineer | 1 | Apr 8 |
| P1-7 | **Hash VINs in session_events** — SHA-256 + salt at ingest, never persist raw | Engineer | 1 | Apr 8 |
| P1-8 | **Add consent-gated data access** — check consent before including user in exports | Engineer | 2-3 | Apr 11 |
| P1-9 | **Build automated balance reconciliation** — nightly ledger vs balance audit | Engineer | 3-4 | Apr 14 |
| P1-10 | **Build sponsor refund flow** — return unspent campaign budget via Stripe | Engineer | 3-4 | Apr 14 |
| P1-11 | **Add APP_FIRST_OPEN event + onboarding funnel tracking** — analytics blind spot fix | Engineer | 1 | Apr 8 |
| P1-12 | **Review SAFE with attorney** | James + Attorney | 5-7 | Apr 14 |
| P1-13 | **Execute contractor IP assignment** — all contributors sign | James + Attorney | 3-5 | Apr 14 |
| P1-14 | **Fix Parker consulting agreement governing law** | James + Attorney | 1 | Apr 8 |
| P1-15 | **Create DPA template** | Attorney | 5-7 | Apr 18 |
| P1-16 | **Add public status page** — `status.nerava.network` | Engineer | 2-3 | Apr 11 |
| P1-17 | **No-app driver reward page** — hosted reward page for partner drivers | Engineer | 5-7 | Apr 25 |

### P2 — Required Before $3M Series A

| # | Item | Owner | Est. Days | Target Date |
|---|------|-------|-----------|-------------|
| P2-1 | **Build Fleet SaaS product** — operator model, vehicle enrollment, recurring billing, dashboard | Engineer | 30-40 | Jun 6 |
| P2-2 | **Build insurance data product** — anonymization, bulk export API, data licensing | Engineer | 20-30 | May 23 |
| P2-3 | **Build location intelligence dashboard** — self-service, subscription billing | Engineer | 15-20 | May 9 |
| P2-4 | **Build partner SDK** — npm + PyPI packages | Engineer | 7-10 | May 2 |
| P2-5 | **Build partner portal** — self-service dashboard for API keys, sessions, grants | Engineer | 10-14 | May 9 |
| P2-6 | **Implement receipt OCR** — Taggun integration for spend verification | Engineer | 7-10 | May 9 |
| P2-7 | **Build in-app merchant checkout** — Stripe payment within driver app | Engineer | 10-14 | May 16 |
| P2-8 | **OCPP 2.0.1 support** — TransactionEvent receiver (in addition to 1.6-J) | Engineer | 3-5 | May 2 |
| P2-9 | **Create data licensing agreement template** | James + Attorney | 7-10 | May 2 |
| P2-10 | **Establish formal option plan with board resolutions** | James + Attorney | 14-21 | May 16 |

---

## BOTTOM LINE

Nerava has built a production-grade incentive engine, a sound double-entry financial ledger, comprehensive session data capture, and a functional merchant commerce loop. The Sponsor Campaign revenue stream is live and working. The Merchant Commerce stream is days away from being closable.

The gap between what is built and what is claimed is significant in three areas:

1. **Three of five revenue streams do not exist as products** (Fleet SaaS, Insurance Data, Location Intelligence)
2. **The OCPP receiver — required for Trident integration — has not been started** despite being described as "ready to deploy"
3. **The dataset (68 sessions, 8 drivers) does not support the "5,000 connected vehicles" claim** in pitch materials

The architecture is sound. The data model is right. The engineering quality is high. What is missing is the last mile of product work to make the platform promise real — and honest communication about what is live versus what is planned.

**Recommended sequence:** Fix P0 financial bugs (days 1-2) → Build OCPP receiver (days 3-10) → Publish API docs (days 3-10) → Ship Android to Play Store (days 3-7) → Create MSA (days 1-14) → Partner onboarding + sandbox (days 10-20).

If work starts Monday April 7, the first partner integration (Trident) could be technically unblocked by April 18 and commercially unblocked (with MSA) by April 25.
