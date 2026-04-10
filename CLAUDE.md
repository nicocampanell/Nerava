# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Stack and Runtime Constraints

- **Production runtime: Python 3.9.** Never use PEP 604 union syntax (`X | None`). Always use `Optional[X]` with `from typing import Optional`.
- All new Python files must have `from __future__ import annotations` OR use `Optional` from typing. Never both.
- Frontend: React + TypeScript + Vite. FastAPI monolith on AWS App Runner. PostgreSQL via SQLAlchemy. Stripe Express. Tesla Fleet API. Smartcar.
- Before writing any new utility function, search the codebase for an existing one. Specifically: distance calculations live in `app/services/geo.py`, email sending uses `send_email()` not `send()`, wallet mutations go through `driver_wallet.py`.

---

## Pre-Change Checklist (Required Before Every Edit)

Before making any code change, answer these questions out loud in your response:

1. Does this touch a wallet, balance, or ledger? If yes, does every mutation use `with_for_update()`?
2. Does this add a new Python type hint? Is it `Optional[X]` not `X | None`?
3. Does this add a new utility function? Have I checked if one already exists?
4. Does this touch auth or user data? Does every endpoint that returns user data verify ownership, not just role?
5. Does this add any credential, API key, or secret? It must come from environment variables only. Never hardcode.
6. Does this add a new import? Is it actually used?
7. Does this change a transaction? Does every `db.commit()` have a corresponding try/except/rollback?
8. Does this change a webhook handler? Is it idempotent — safe to receive the same event twice?
9. Does this change polling or `setInterval`? Use recursive `setTimeout`, not `setInterval`, for async work.
10. Does this change an environment check? Use `settings.is_prod` not raw string comparison like `settings.ENV == "prod"`.

If any answer is "no" or "I am not sure", fix it before proceeding.

---

## Financial and Wallet Rules (Non-Negotiable)

Nerava is a financial product. These rules are absolute:

- Every wallet balance mutation (`balance_cents`, `pending_balance_cents`, `nova_balance`, `lifetime_earned_cents`) requires `with_for_update()` on the row before mutation. No exceptions.
- Every budget decrement must be inside a transaction that rolls back if the subsequent grant creation fails. Budget reduced with no grant created is a financial integrity bug.
- Every payout webhook handler (paid, failed) requires `with_for_update()` before mutating wallet state.
- Visit number allocation (`max(...) + 1`) is not atomic. Use a database sequence or a locked select (`order_by(...desc()).with_for_update().first()`).
- Double-entry means every credit has a corresponding debit. Never create one without the other in the same transaction.
- All `incentive_grants.amount_cents` changes must be atomic via `CampaignService.decrement_budget_atomic()`.

---

## Authorization Rules

- Every endpoint that returns or modifies user-specific data must verify both role AND ownership. Checking role only is insufficient.
- Idempotency key lookups must filter by `driver_id` or the relevant owner. A global lookup by key alone allows one user to access another user's state.
- Merchant endpoints must verify the requesting user owns the specific `merchant_id` in the request, not just that they have the merchant role (use `_verify_merchant_ownership()` in `exclusive.py`).
- Never return raw exception text to the client. Catch and return a generic error message. Log the full exception server-side with `logger.error(..., exc_info=True)`.
- Mock/demo features must be gated by admin account check (`public_id` verification), not just a localStorage flag or env variable.

---

## Secret and Credential Rules

- Never hardcode API keys, secrets, or credentials in source code. Always use environment variables via `settings` or `os.getenv()`.
- Never commit log files. Add `*.log` and `logs/` to `.gitignore` before creating any log output.
- Never put API keys in JSON fixtures, documentation, or archive files.
- If you see any string matching `AIza[A-Za-z0-9_-]{35}` (Google API key pattern), `sk_live_*`, `sk_test_*`, or similar credential patterns anywhere, flag it immediately and do not proceed until it is removed.
- MD5 and SHA1 used for non-security purposes (caching, ETags, idempotency keys) require `usedforsecurity=False` on Python 3.9+.
- JWT secret must not default to `"dev-secret-change-me"` outside dev/test environments. Fail closed in prod/staging.

---

## Transaction Safety Rules

- Every `db.commit()` must be wrapped in try/except with `db.rollback()` in the except block.
- Never flush without committing unless you explicitly need the ID before the transaction ends.
- Budget restores after rollback must be committed (`db.commit()`), not just flushed.
- Refresh token rotation must roll back the new token if the old token deletion fails.
- `update_session()` webhooks must be idempotent. Check `is_new` flag before firing downstream events like `partner.session.resolved`.
- `TeslaOAuthState.store()` and any other `db.merge()` + `db.commit()` sequence must be wrapped in try/except with rollback.

---

## Python Quality Rules

- No bare `except: pass`. Use `contextlib.suppress(SpecificException)` or log the exception.
- No `raise SomeError()` without `from original_exception` (or `from None`) in except blocks (B904).
- No `print()` statements in production code. Use the module-level logger.
- **Every file that uses `logger` must have `import logging` AND `logger = logging.getLogger(__name__)` at the top.** This is the #1 rule — the bug that triggered the 13-round audit.
- Before using any variable, confirm it is imported or defined in scope. Never assume a variable exists because it exists in another file.
- When writing a new file, add all imports at the top before writing any logic.
- Distance calculations: always `from app.services.geo import haversine_m`. Never write a local haversine function.

---

## Frontend Quality Rules

- Never use `setInterval` for async polling. Use recursive `setTimeout` that only fires the next poll after the current one resolves (single-flight pattern with `inFlightRef`).
- Async errors inside `navigator.geolocation` callbacks bypass outer try/catch. Wrap the async logic inside the callback in its own try/catch.
- Event listeners added at module level must be cleaned up in an `import.meta.hot.dispose()` hook to prevent duplication on Vite HMR reload. Use named callback references so `removeEventListener` actually removes them.
- `console.log` with request or response payloads must be gated by a dev-only check (`API_DEBUG` flag) before shipping. Remove or gate all payload logging before any demo or production deploy.
- Mock and demo mode must be gated by admin identity check (specific `public_id` comparison), not just an environment flag.
- Auth errors (401, `refresh_failed`, `no_refresh_token`) in polling should stop the poll loop via `window.dispatchEvent(new CustomEvent('nerava:session-expired'))`, not retry after 30 seconds.
- API calls must go through `src/services/api.ts` — flag any raw `fetch()` calls outside of the service file.
- `VITE_API_BASE_URL` must be set for production builds. Without it, apps default to `localhost:8001` which breaks production.

---

## Before Every Commit

Run these checks in order. Do not commit if any fail:

```bash
# From repo root
ruff check backend/app/
ruff check backend/app/ --fix  # Auto-fix if possible

# If pre-commit is installed
pre-commit run --all-files
```

If pre-commit is not installed: `pip install pre-commit && pre-commit install`

---

## Before Every New File

1. Search for existing utilities: `grep -r "function_name_or_concept" backend/app/`
2. Check `app/services/geo.py` before writing any distance calculation
3. Check `app/services/driver_wallet.py` or `payout_service.py` before writing any wallet mutation
4. Check `app/core/email_sender.py` for the correct method signature before calling it (`send_email`, not `send`)
5. Confirm all imports at the top are actually used in the file
6. Add `import logging` and `logger = logging.getLogger(__name__)` at the top

---

## Demo and Investor Safety

Nerava is actively fundraising and demoing to investors. These rules protect the demo:

- Mock charging and demo mode must require a specific hardcoded `public_id` check, not just an env flag
- Never show raw database errors, stack traces, or internal state to the client
- Wallet balance must always be accurate. Any doubt about transaction safety means stop and fix before continuing
- `VITE_API_BASE_URL` must be set correctly for each environment: empty string for dev (uses Vite proxy), full URL for production
- Never push directly to `main`. Always go through a PR with CodeRabbit review and passing CI.

---

## When You Are Unsure

If you are unsure whether a pattern is safe, stop and ask before writing it. Specifically:

- "Is this mutation safe under concurrent load?" — ask before writing
- "Does this endpoint need a row lock?" — ask before writing
- "Is there already a utility for this?" — search before writing
- "Is this credential coming from environment variables?" — verify before writing
- "Will this run on Python 3.9?" — check type hints before writing

The cost of asking is one message. The cost of a production wallet bug is unquantifiable.

---

## Incident History (Learn From These — Never Reintroduce)

These bugs were found in production or in the 13-round audit (April 2026). Never reintroduce them:

- **`auth.py`**: Used `logger` on 15+ lines without importing `logging`. All email OTP crashed with `NameError`. **Fix: import `logging` and define `logger` at the top of every file.**
- **`incentive_engine.py:308`**: Wallet mutation without `with_for_update()` caused lost updates under concurrent grants. **Fix: always lock row before mutating wallet or budget.**
- **`incentive_engine.py:248-286`**: Budget decremented but grant creation could fail, leaving orphaned budget. **Fix: wrap post-decrement in try/except with rollback + budget restore.**
- **`exclusive.py:1081`**: `visit_number = max(...) + 1` non-atomic. Duplicate visit numbers possible under concurrent load. **Fix: `order_by(...desc()).with_for_update().first()` then increment.**
- **`exclusive.py:1239`**: Any authenticated driver could enumerate/redeem any merchant's visits via merchant endpoints. **Fix: `_verify_merchant_ownership()` checks `DomainMerchant.owner_user_id`, not just role.**
- **`exclusive.py:294`**: Idempotency lookup by key alone leaked session state across drivers. **Fix: global unique constraint — driver mismatch returns 409.**
- **`pwa_responses.py`, `merchants_google.py`, `places_google.py`, `google_distance_matrix_client.py`**: Google API key hardcoded in source. **Fix: `os.getenv("GOOGLE_API_KEY", "")`. Key has been rotated in Google Cloud Console.**
- **`backend/logs/seed_city.log`**: 1.5 MB log file with API keys in request URLs committed to repo. **Fix: `backend/logs/` in `.gitignore`, never commit logs.**
- **`config.py:14`**: JWT secret defaulted to `"dev-secret-change-me"` in all environments. **Fix: fallback only when `ENV in ("dev", "development", "test")`, empty string otherwise.**
- **`config.py:510`**: `settings.ENV == "prod"` failed against `"production"` and `"Prod"`. **Fix: `settings.is_prod` property with lowercase membership check. No raw string comparison.**
- **`weekly_merchant_report.py:197`**: Called `email_sender.send(to=, html=)` but interface defines `send_email(to_email=, subject=, body_text=, body_html=)`. **Fix: check method signatures before calling.**
- **`useSessionPolling.ts`**: `setInterval` with async work caused overlapping polls and duplicate session events. **Fix: recursive `setTimeout` with `inFlightRef` single-flight guard.**
- **`useSessionPolling.ts`**: Auth errors (401) triggered 30s retry instead of stopping. **Fix: `isAuthError()` check — dispatch `nerava:session-expired` event and stop polling.**
- **`smartcar_client.py`**: Entire file had zero imports. Every variable was undefined. Dead code that would crash on first import. **Fix: imports at top of every file. Delete dead code.**
- **`api.ts`**: Module-level event listeners leaked on Vite HMR. **Fix: named callbacks + `import.meta.hot.dispose()` + `removeEventListener`.**
- **`api.ts`**: `console.log` of full request/response payloads in production leaked sensitive data. **Fix: `API_DEBUG` flag gated on `import.meta.env.DEV`.**
- **`payout_service.py:633, 670`**: Webhook handlers mutated wallet without `with_for_update()`. **Fix: lock before mutate on both paid and failed paths.**
- **`partner_api.py:135`**: Completion webhook fired on every PATCH replay. **Fix: only fire when `is_new` flag is truthy.**
- **`partner_api.py:183`**: `lat`/`lng` query params accepted but never applied to geo filtering. **Fix: haversine distance check against campaign geo radius.**
- **`auth.py:163`**: Logout endpoint used `get_current_user` (raises on expired token) instead of `get_current_user_optional`. Refresh-token logout path unreachable. **Fix: use `get_current_user_optional` for endpoints that have alternate auth paths.**
- **`auth.py:283, 839`**: Error handlers leaked raw exception text to client. **Fix: generic message to client, full exception logged server-side.**
- **Haversine function**: Duplicated in 10 files (`dual_zone.py`, `verify_dwell.py`, `intent_service.py`, `ml_ranker.py`, `merchant_charger_map.py`, `while_you_charge.py`, `merchant_details.py`, `bootstrap.py`, `drivers_domain.py`, `analyze_texas_chargers.py`). **Fix: always `from app.services.geo import haversine_m`.**
- **MD5/SHA1 without `usedforsecurity=False`**: 6 instances in `cache/layers.py`, `idempotency.py`, `purchases.py`, `hubs_dynamic.py`, `apple_wallet_pass.py`. **Fix: add `usedforsecurity=False` to every non-security hash call.**
- **PyJWT 2.10.1 CVE-2026-32597**: `crit` header bypass vulnerability. **Fix: upgraded to `>=2.12.0`.**
- **SendGrid credits exhausted** (April 2026 incident): email OTP failed for all users. **Fix: migrated to AWS SES with domain verification via Route53 DKIM + App Runner instance role.**
- **`VITE_API_BASE_URL` missing** in admin/merchant/console production builds: defaulted to `localhost:8001`, broke all three portals. **Fix: explicit env var in every production build command.**
- **Mock charging not gated to admin**: any user could set `localStorage.debug_mock_charging = 'true'` and fake sessions. **Fix: specific `public_id` check in `useSessionPolling.ts` AND `AccountPage.tsx`.**
- **PEP 604 syntax** (`X | None`) in 8+ backend files crashes Python 3.9. **Fix: `Optional[X]` with `from typing import Optional`. Lint rule `UP045` is ignored in `ruff.toml`.**
- **CI pipeline broken**: `continue-on-error: true` swallowed failures, `pytest-cov` not installed, legacy tests imported `app.main`. **Fix: removed failure swallowing, added test deps, skipped broken legacy tests with `--ignore`.**

**See `AUDIT_RETROSPECTIVE_REPORT.md` for the full 90-issue post-mortem.**

---

## What is Nerava

Nerava is **"Google Ads for EV charging dwell time"** — a verified commerce platform for the EV charging ecosystem. When drivers charge their EVs at supported locations, nearby merchants can reach them with offers during their 20-45 minute charging dwell time. The core billing event is **Claim + Presence**: a driver actively charging + within walking distance + taps "Claim Offer" = one qualified charging lead billed to the merchant. Merchants buy prepaid campaign credits (4% of AOV per claim). Sponsors can also create campaigns that reward drivers for charging at specific locations/times. The system has a driver-facing app, merchant portal, admin dashboard, sponsor console, landing page, iOS native shell, Android app, and a FastAPI backend.

## Repository Structure

This is a monorepo with independently deployed apps:

- **`apps/driver`** — Driver mobile/web app (React 19, Vite 7, Tailwind 4, React Router 7, React Query)
- **`apps/merchant`** — Merchant portal + acquisition funnel (React 18, Vite 5, Radix UI, React Hook Form)
- **`apps/admin`** — Admin dashboard (React 18, Vite 5, Radix UI, Recharts)
- **`apps/console`** — Sponsor campaign management portal (React 18, Vite 5, Radix UI, React Router 6)
- **`apps/landing`** — Marketing site (Next.js 14, static export for S3)
- **`apps/link`** — Link redirect app (React 18, Vite 5, minimal)
- **`backend/`** — FastAPI monolith (Python, SQLAlchemy 2, Alembic, Pydantic 2)
- **`packages/analytics`** — Shared PostHog analytics wrapper (`@nerava/analytics`)
- **`Nerava/`** — iOS Xcode project (WKWebView shell wrapping the driver web app)
- **`mobile/nerava_android/`** — Android app (Kotlin, WebView shell mirroring iOS, FCM, native bridge)
- **`Nerava-Campaign-Portal/`** — Newer iteration of sponsor campaign portal (React Router 7, may supersede `apps/console`)
- **`e2e/`** — Cross-app Playwright E2E tests
- **`infra/terraform/`** — AWS infrastructure (Terraform configs for ECS, RDS, ALB, Route53, CloudWatch)
- **`infra/setup_monitoring.sh`** — AWS CLI script to bootstrap CloudWatch alarms + SNS alerting
- **`infra/nginx/`** — Nginx reverse proxy config for Docker Compose

## Build & Dev Commands

### Backend (FastAPI)

```bash
# Run locally (from repo root or backend/)
cd backend && uvicorn app.main_simple:app --reload --port 8001

# Run tests (uses in-memory SQLite automatically)
cd backend && pytest
cd backend && pytest tests/test_checkin.py              # single file
cd backend && pytest tests/test_checkin.py::test_name   # single test
cd backend && pytest -k "keyword"                       # by keyword

# Database migrations
cd backend && python -m alembic upgrade head            # apply all
cd backend && python -m alembic revision --autogenerate -m "description"  # create new
```

### Driver App

```bash
cd apps/driver && npm install && npm run dev     # localhost:5173, uses VITE_API_BASE_URL (no proxy)
cd apps/driver && npm run build                  # TypeScript check + Vite build
cd apps/driver && npm run lint
cd apps/driver && npm run test                   # Vitest
cd apps/driver && npx vitest run                 # Vitest single run (no watch)
```

### Merchant Portal

```bash
cd apps/merchant && npm install && npm run dev   # localhost:5174, proxies /v1 to :8001
cd apps/merchant && npm run build
cd apps/merchant && npm run lint
```

### Admin Dashboard

```bash
cd apps/admin && npm install && npm run dev      # localhost:3001
cd apps/admin && npm run build
cd apps/admin && npm run lint
```

### Landing Page

```bash
cd apps/landing && npm install && npm run dev
cd apps/landing && npm run build                 # Next.js static export
cd apps/landing && npm run lint
```

### Console (Sponsor Portal)

```bash
cd apps/console && npm install && npm run dev    # localhost:5176, proxies /v1 to :8001
cd apps/console && npm run build
cd apps/console && npm run lint
```

### E2E Tests

```bash
cd e2e && npm install && npx playwright test
cd e2e && npx playwright test --ui               # interactive mode
```

### Full Stack (Docker Compose)

```bash
docker-compose up                               # all services
# Backend :8001, Landing :80/, Driver :80/app/, Merchant :80/merchant/, Admin :80/admin/, PostHog :8081
```

## Backend Architecture

### Entry Point

- **`app/main_simple.py`** — Production entry point used by App Runner, Docker, and tests. Initializes Sentry (when `SENTRY_DSN` is set), registers all routers, and mounts middleware.

### Key Layers

- **`app/routers/`** — FastAPI route handlers. All routes use `/v1` prefix.
- **`app/services/`** — Business logic layer (arrival, checkin, checkout, payments, notifications, etc.)
- **`app/models/`** — SQLAlchemy ORM models. `domain.py` has core models (Zone, EnergyEvent, DomainMerchant, DomainChargingSession, NovaTransaction). Other key models: `user.py`, `arrival_session.py`, `exclusive_session.py`, `tesla_connection.py`, `campaign.py` (sponsor campaigns), `session_event.py` (SessionEvent + IncentiveGrant), `partner.py` (Partner + PartnerAPIKey).
- **`app/schemas/`** — Pydantic request/response schemas
- **`app/dependencies/`** — FastAPI dependency injection (`get_db`, auth, feature flags, partner API key auth)
- **`app/middleware/`** — Auth (JWT), rate limiting, metrics, region routing, audit logging, security headers, request size limits
- **`app/integrations/`** — Third-party clients (legacy Google Places, Google Distance Matrix, NREL, Overpass/OSM)
- **`app/cache/`** — Two-layer caching (L1 in-memory + L2 Redis) with TTL support

### Middleware Stack

| Middleware | Purpose |
|-----------|---------|
| `auth.py` | JWT verification, extract current user from token |
| `audit.py` | Audit logging (user actions, resource changes) |
| `logging.py` | Structured request/response logging (request_id, method, path, status, duration_ms, user_id) |
| `metrics.py` | Prometheus metrics collection |
| `ratelimit.py` | Rate limiting per user/IP (Redis-backed, configurable) |
| `region.py` | Region routing and context injection |
| `request_id.py` | Generate/track X-Request-ID for distributed tracing |
| `request_size.py` | Enforce max request body size limits |
| `security_headers.py` | HSTS, X-Content-Type-Options, CSP, etc. |

### Database

- **Dev:** SQLite (`sqlite:///./nerava.db`)
- **Production:** PostgreSQL on RDS (`nerava-db.c27i820wot9o.us-east-1.rds.amazonaws.com`)
- **Migrations:** Alembic, run from `backend/` directory. ~105 migration files in `alembic/versions/`.
- **Lazy engine init:** `app/db.py` creates the engine on first access, not at import time. This matters for container health checks.

### Key Database Tables

| Table | Purpose |
|-------|---------|
| `users` | Drivers, merchants, admins with roles |
| `session_events` | Verified EV charging sessions (30 columns: timing, energy, location, telemetry, anti-fraud) |
| `incentive_grants` | Links sessions to campaign rewards (one grant per session max) |
| `campaigns` | Sponsor campaigns with budget, targeting rules (JSON), status lifecycle |
| `driver_wallets` | Driver reward balances, pending funds, Stripe Express account links |
| `wallet_ledger` | Double-entry transaction ledger for wallet credits/debits |
| `payouts` | Driver withdrawal records (pending → processing → paid/failed) |
| `nova_transactions` | Double-entry Nova points ledger (grants, redemptions, transfers) |
| `chargers` | EV charger locations, network, connector type, power rating (indexed lat/lng) |
| `domain_merchants` | Merchant records with perk config, QR tokens, Square integration |
| `zones` | Geographic zones (center lat/lng + radius) |
| `exclusive_sessions` | Active exclusive offer sessions (charger arrival + merchant unlock, countdown) |
| `tesla_connections` | Tesla OAuth tokens per driver (access_token, refresh_token, vehicle_id, vin) |
| `ev_verification_codes` | EV-XXXX codes, valid for 2 hours |
| `device_tokens` | APNs/FCM push notification tokens |
| `partners` | External integration partners (charging networks, fleet platforms, driver apps) with trust tiers and rate limits |
| `partner_api_keys` | SHA-256 hashed API keys for partner authentication (`nrv_pk_` prefix) |

### Auth Flow

JWT-based authentication with OTP (Twilio Verify) for phone-first login. Apple Sign-In, Google Sign-In, and Tesla OAuth also supported. The driver app login modal shows Apple/Google buttons above the phone OTP form (buttons hidden if `VITE_APPLE_CLIENT_ID`/`VITE_GOOGLE_CLIENT_ID` env vars not set). The `OTP_PROVIDER=stub` env var enables fake OTP in dev.

### Key Integrations

- **Stripe** — Payments and payouts (`app/services/stripe_service.py`, `app/services/payout_service.py`)
- **Twilio** — OTP verification (`app/services/auth/twilio_verify.py`)
- **PostHog** — Analytics across all apps (`packages/analytics`)
- **Tesla Fleet API** — Vehicle charging verification (`app/services/tesla_oauth.py`, `app/routers/tesla_auth.py`)
- **Google Places (New API)** — Merchant enrichment and search (`app/services/google_places_new.py`). Legacy client at `app/integrations/google_places_client.py`.
- **Sentry** — Error tracking (initialized in `main_simple.py` when `SENTRY_DSN` is set)
- **Square** — POS integration for merchant check-in/redemption (`app/services/square_service.py`)
- **Smartcar** — Alternative EV API for non-Tesla vehicles (`app/services/smartcar_service.py`)

## Core Business Logic

### Charging Session Lifecycle

The hot path of the entire system. Managed by `SessionEventService` in `app/services/session_event_service.py`.

**Flow:**
1. Driver app polls `POST /v1/charging-sessions/poll` every **60 seconds** (visibility-aware, pauses when backgrounded)
2. Backend checks Tesla Fleet API for charging state on driver's selected vehicle
3. **Not charging → Charging:** Creates `SessionEvent` row, matches to nearest charger via geolocation (500m radius)
4. **Charging → Charging:** Updates telemetry (kwh, battery %, power_kw), backfills location if missing
5. **Charging → Not charging:** Ends session, computes quality_score (anti-fraud), triggers `IncentiveEngine.evaluate_session()`
6. **Server-side cache:** 30-second dedup per driver to avoid redundant Tesla API calls
7. **Stale cleanup:** Auto-closes sessions not updated in 15 minutes

**Per-session data footprint:** ~500 bytes in `session_events` + ~300 bytes in `incentive_grants` (if matched) + indexes. At 1M drivers averaging 2.5 sessions/week, that's ~10.75M rows/month, ~5.4 GB raw data/month, ~100 GB/year with indexes.

### Incentive Engine

Evaluates sessions against active campaigns when a session ends. Managed by `IncentiveEngine` in `app/services/incentive_engine.py`.

**Matching rules (ALL are AND-ed):**
- Minimum/maximum duration
- Charger IDs or charger networks (Tesla, ChargePoint, etc.)
- Zone IDs or geographic radius (haversine distance)
- Time of day window (handles overnight spans)
- Day of week
- Minimum power (kW) for DC fast charging targeting
- Connector types (CCS, Tesla, CHAdeMO)
- Driver session count bounds (new vs repeat driver rules)
- Driver allowlist (email or user ID)
- Per-driver caps (daily/weekly/total limits per campaign)
- Partner session controls: `allow_partner_sessions`, `rule_partner_ids`, `rule_min_trust_tier`

**Grant logic:**
- One session = one grant max (highest priority campaign wins, no stacking)
- Grants created only on session END
- Atomic budget decrement prevents overruns
- Idempotent via `session_event_id` uniqueness constraint
- `reward_destination` field routes rewards: `nerava_wallet` (Nerava drivers), `partner_managed` (partner handles rewards), `deferred` (pending account creation)
- Partner shadow driver sessions skip wallet/Nova credit (partner handles their own rewards)

### Nova Transaction System

Double-entry points ledger in `app/services/nova_service.py`.

- Every grant/redemption is an atomic Nova transaction
- Idempotent via `idempotency_key` + `payload_hash` (SHA256). Same key + same hash = returns existing transaction. Same key + different hash = 409 Conflict.
- `grant_to_driver()` increments both `nova_balance` and `energy_reputation_score` (1:1 ratio for `driver_earn` type)
- Transaction types: `driver_earn`, `admin_grant`, `driver_redeem`, `transfer`

### Energy Reputation System

Gamified tier system in `app/services/reputation.py`.

| Tier | Points Required | Color |
|------|----------------|-------|
| Bronze | 0 | `#78716c` |
| Silver | 100 | `#64748b` |
| Gold | 300 | `#eab308` |
| Platinum | 700+ | `#06b6d4` |

- Points accrue 1:1 with Nova earned from charging sessions
- Non-incentive sessions (no campaign match) earn 5 base reputation points if quality_score > 30
- Streak days computed from consecutive days with completed sessions (handles PostgreSQL + SQLite dialects)
- API: `GET /v1/charging-sessions/reputation` returns tier, points, progress, streak

### Driver Wallet & Payout Flow

Stripe Express payouts in `app/services/payout_service.py`.

1. Wallet auto-created on first access (`get_or_create_wallet`)
2. Campaign grants credit `balance_cents` + create `wallet_ledger` entry
3. Withdrawal: validates min $20, max 3/day, max $1000/week
4. Moves funds: `balance_cents` → `pending_balance_cents` (atomic)
5. Creates Stripe Transfer to driver's Express account
6. Webhook confirms completion → status `paid`

### Exclusive Session Flow

Merchant deal activation in `app/routers/exclusive.py`.

1. Driver arrives at charger → app detects via geolocation
2. Driver selects merchant → activates exclusive offer
3. Countdown timer starts (default 60 minutes)
4. Driver visits merchant → verification (QR scan, dwell time, or manual)
5. Redemption code generated for merchant POS

## Frontend Architecture

### Driver App (`apps/driver`)

- **Framework:** React 19, Vite 7, Tailwind 4 (not 3), React Router 7, React Query
- **API client:** `src/services/api.ts` — all API calls, React Query hooks, type-safe responses
- **Validation:** `src/services/schemas.ts` — Zod schemas for API response validation
- **State:** React Query for server state, React Context for local state
- **Pattern:** Feature-folder: `src/components/FeatureName/FeatureName.tsx`

**Key components:**

| Component | Purpose |
|-----------|---------|
| `DriverHome/DriverHome.tsx` | Main home screen — session status, charger list, map/card toggle, merchant carousel |
| `ChargerMap/ChargerMap.tsx` | Leaflet map with charger/merchant/user pins (OpenStreetMap tiles) |
| `PreCharging/PreChargingScreen.tsx` | Charger selection and session start |
| `SessionActivity/SessionActivityScreen.tsx` | Charging history, stats, energy reputation card |
| `SessionActivity/EnergyReputationCard.tsx` | Gamified tier/streak/progress display |
| `SessionActivity/SessionCard.tsx` | Individual charging session display |
| `SessionActivity/ActiveSessionBanner.tsx` | Active charging session banner |
| `MerchantCarousel/MerchantCarousel.tsx` | Horizontal merchant discovery carousel |
| `MerchantDetails/MerchantDetailsScreen.tsx` | Full merchant details (distance, perk, wallet) |
| `MerchantDetail/MerchantDetailModal.tsx` | Merchant detail modal overlay |
| `ExclusiveActiveView/ExclusiveActiveView.tsx` | Active exclusive session with countdown |
| `EVArrival/ActiveSession.tsx` | Active EV charging session management |
| `EVOrder/EVOrderFlow.tsx` | EV order creation flow |
| `WhileYouCharge/WhileYouChargeScreen.tsx` | Merchant deals during charging |
| `Wallet/WalletModal.tsx` | Wallet balance and payout management |
| `Earnings/Earnings.tsx` | Driver earnings dashboard |
| `Account/AccountPage.tsx` | Profile, favorites, settings, login/logout |
| `Account/LoginModal.tsx` | Phone OTP + Apple/Google Sign-In |
| `TeslaLogin/VehicleSelectScreen.tsx` | Vehicle selection after Tesla OAuth |
| `shared/PrimaryFilters.tsx` | Category and distance filters |
| `shared/SearchBar.tsx` | Charger/merchant search input |
| `ErrorBoundary.tsx` | React error boundary |
| `SessionExpiredModal.tsx` | Expired session notification |

**Key hooks:**

| Hook | Purpose |
|------|---------|
| `useSessionPolling` | Polls charging state every 60s, visibility-aware, tracks duration/kwh/incentive |
| `usePageVisibility` | Tracks foreground/background, pauses polling when hidden |
| `useExclusiveSessionState` | Manages exclusive deal lifecycle (activation, countdown, completion) |
| `useGeolocation` | Wrapper for navigator.geolocation with error handling |
| `useNativeBridge` | Communication bridge to iOS WKWebView (location, auth, device tokens) |
| `useArrivalLocationPolling` | 5-second GPS polling during arrival confirmation |
| `useDriverSessionState` | Driver session lifecycle (start, active, end) |
| `useEVContext` | EV charging context (charger selection, charging state) |
| `useViewportHeight` | Responsive layout height tracking |
| `useDemoMode` | Toggle demo/mock mode for development |

**Contexts:**

| Context | Purpose |
|---------|---------|
| `DriverSessionContext` | Global session state: charger target, session active/ended, incentive data |
| `FavoritesContext` | User's favorited merchants, persisted to localStorage |

**Design tokens:** Custom values in `tailwind.config.js` — `rounded-card`, `rounded-button`, `rounded-pill`, `rounded-modal`, `shadow-figma-card`.

### Merchant Portal (`apps/merchant`)

- Radix UI + Tailwind 3 + `sonner` for toasts
- **Public acquisition funnel** (no auth): `/find` → `/preview` → `/claim`. Backend at `/v1/merchant/funnel/*`. Preview URLs are HMAC-signed with 7-day TTL.
- **Dashboard routes** (auth required): `/dashboard`, `/exclusives`, `/ev-arrivals`, `/visits`, `/settings`

### Admin/Console

- Both use Radix UI + Tailwind 3
- Admin on port 3001, Console on port 5176, both proxy `/v1` to backend

## iOS App Architecture (`Nerava/`)

WKWebView shell wrapping the driver web app. **No App Store push needed for web-only changes.**

### NeravaApp.swift
- `AppDelegate` for remote notification handling (APNs device token registration)
- Creates singletons: `LocationService`, `SessionEngine`, `GeofenceManager`, `APIClient`
- Handles universal links / deep links via `DeepLinkHandler`

### WebViewContainer.swift
- Wraps `WebViewRepresentable` (WKWebView)
- Overlays: LoadingOverlay, OfflineOverlay, ErrorOverlay
- Error types: network, server (HTTP), SSL, processTerminated, unknown

### NativeBridge.swift
- Bidirectional JS ↔ Swift communication via `window.neravaNative`
- **JS → Swift methods:** `setChargerTarget()`, `setAuthToken()`, `confirmExclusiveActivated()`, `confirmVisitVerified()`, `endSession()`, `requestAlwaysLocation()`, `getLocation()`, `getSessionState()`, `getPermissionStatus()`, `getAuthToken()`, `openExternalUrl()`
- **Swift → JS messages:** `SESSION_STATE_CHANGED`, `PERMISSION_STATUS`, `LOCATION_RESPONSE`, `AUTH_TOKEN_RESPONSE`, `DEVICE_TOKEN_REGISTERED`, `NATIVE_READY`
- Origin whitelisting for security

### NotificationService.swift
- APNs authorization request, local notifications for session/arrival events
- Stores APNs token for forwarding to backend

## Android App Architecture (`mobile/nerava_android/`)

Kotlin WebView shell mirroring the iOS app. **No Play Store push needed for web-only changes.**

### Key Classes

| Class | Purpose |
|-------|---------|
| `MainActivity.kt` | WebView setup, permissions, FCM registration, deep links, error overlays |
| `NativeBridge.kt` | Bidirectional JS ↔ Kotlin via `@JavascriptInterface`. Same `window.neravaNative` API as iOS |
| `BridgeInjector.kt` | JavaScript injection script (equivalent to iOS WKUserScript) |
| `BridgeMessage.kt` | Sealed class for native → web messages (mirrors iOS `NativeBridgeMessage` enum) |
| `SessionEngine.kt` | Background session management, geofence transitions |
| `LocationService.kt` | FusedLocationProvider, foreground + background location |
| `GeofenceManager.kt` | Geofence registration/monitoring for charger proximity |
| `FCMService.kt` | Firebase Cloud Messaging token management + push handling |
| `SecureTokenStore.kt` | EncryptedSharedPreferences for auth + FCM tokens |
| `APIClient.kt` | OkHttp REST client for backend communication |
| `DeepLinkHandler.kt` | Intent → web URL resolution for deep links |

### Native Bridge (JS ↔ Kotlin)
- **JS → Kotlin:** `AndroidBridge.onMessage()` via `@JavascriptInterface` (same actions as iOS)
- **Kotlin → JS:** `window.neravaNativeCallback(action, payload)` via `evaluateJavascript()`
- **Messages:** `SESSION_STATE_CHANGED`, `PERMISSION_STATUS`, `LOCATION_RESPONSE`, `AUTH_TOKEN_RESPONSE`, `DEVICE_TOKEN_REGISTERED`, `NATIVE_READY`, `PUSH_DEEP_LINK`, `ERROR`
- Bridge parity with iOS confirmed as of 2026-03-04

### Build
```bash
cd mobile/nerava_android
./gradlew assembleDebug          # debug APK
./gradlew bundleRelease          # release AAB for Play Store
./gradlew test                   # unit tests
```

### Release Signing
- Keystore properties read from `keystore.properties` (not committed)
- Template at `keystore.properties.example`
- ProGuard rules in `app/proguard-rules.pro` keep bridge classes + `@JavascriptInterface`

## Growth Readiness & Cost

- **`GROWTH_GAP_ANALYSIS.md`** — Blockers and priorities for Android Play Store launch and iOS hardening. Living document with checkboxes.
- **`COST_ANALYSIS.md`** — Full breakdown of every paid service, monthly/usage-based costs, DDoS risk assessment, and optimization roadmap. Key findings:
  - Minimum viable production cost: **~$52-88/mo** (after removing optional services)
  - Biggest cost risks at scale: Twilio OTP ($20K/mo at 100K users) and Google Places ($13K/mo) — both replaceable with free alternatives
  - DDoS worst case: **~$15K** from CloudWatch log ingestion (7-day sustained L7 attack) — mitigated by adding AWS WAF ($5/mo)
  - No AWS WAF configured currently — application-layer rate limiting only

## Analytics Events

Tracked via PostHog (`packages/analytics`). Key events in `apps/driver/src/analytics/events.ts`:

- **Session:** `SESSION_START`, `PAGE_VIEW`, `HOME_REFRESHED`
- **Auth:** `OTP_START`, `OTP_VERIFY_SUCCESS/FAIL`
- **Charging:** `CHARGING_SESSION_DETECTED`, `CHARGING_SESSION_ENDED`, `CHARGING_INCENTIVE_EARNED`, `CHARGING_ACTIVITY_OPENED`
- **Intent:** `INTENT_CAPTURE_REQUEST/SUCCESS/FAIL`
- **Exclusive:** `EXCLUSIVE_ACTIVATE_CLICK/SUCCESS/FAIL`, `EXCLUSIVE_COMPLETE_CLICK/SUCCESS/FAIL`
- **Merchant:** `MERCHANT_CLICKED`, `MERCHANT_DETAIL_VIEWED`, `MERCHANT_FAVORITED`, `MERCHANT_SHARED`
- **Arrival:** `ARRIVAL_VERIFIED`, `EV_ARRIVAL_CONFIRMED`, `EV_ARRIVAL_GEOFENCE_TRIGGERED`
- **Virtual Key:** `VIRTUAL_KEY_PAIRING_STARTED/COMPLETED/FAILED`, `VIRTUAL_KEY_ARRIVAL_DETECTED`
- **Other:** `SEARCH_QUERY`, `DEVICE_TOKEN_REGISTERED`, `PREFERENCES_SUBMIT`

## Testing

### Backend Tests

- **Framework:** pytest with in-memory SQLite
- **Fixtures:** `backend/tests/conftest.py` provides `db` (isolated session per test, auto-rollback), `client` (FastAPI TestClient with dependency overrides), `test_user`, `test_merchant`
- **Test DB override:** Uses `app.dependency_overrides` to inject test sessions into both `app.db.get_db` and `app.dependencies.get_db`
- **Root `tests/` directory** has security/integration tests that import from `backend/`
- **Key test files:** `test_session_event_service.py`, `test_incentive_engine.py`, `test_payout_service.py`, `test_campaign_service.py`, `test_tesla_oauth.py`, `test_security_headers.py`, `test_partner_api.py`

### Frontend Tests

- **Driver app:** Vitest + React Testing Library + jsdom
- **E2E:** Playwright (root `e2e/` and `apps/driver/e2e/`)

## CI/CD

- **Backend tests:** `.github/workflows/backend-tests.yml` — pytest on Python 3.10 (triggers on `backend/` changes)
- **Driver app:** `.github/workflows/ci-driver-app.yml` — lint, build, vitest, Playwright (triggers on `apps/driver/` changes)
- **Deploy:** `.github/workflows/deploy-prod.yml` — full production pipeline (includes Trivy container scan); `.github/workflows/deploy-driver-app.yml` — Docker build + ECR push
- **Security:** `.github/workflows/codeql-driver-app.yml` — CodeQL for JS/TS; `.github/workflows/codeql-backend.yml` — CodeQL for Python; `.github/workflows/backend-security.yml` — pip-audit + bandit SAST (PRs + weekly)
- **Monitoring:** `.github/workflows/health-check.yml` — pings all prod endpoints every 30 min, opens GitHub issues on failure; `.github/workflows/daily-report.yml` — daily 7am ET production report via `backend/scripts/daily_prod_report.py`; `.github/workflows/prod-validation.yml` — live API validation (daily + post-deploy)

## Infrastructure & Production Deployment

### Actual Production Architecture (what's running)

The production architecture does **NOT** match the ECS setup in `deploy-prod.yml`. The actual running services are:

- **Backend:** AWS **App Runner** (not ECS). Service ARN: `arn:aws:apprunner:us-east-1:566287346479:service/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3`
- **Driver app:** Static site on **S3 + CloudFront** (bucket: `app.nerava.network`)
- **Merchant portal:** S3 + CloudFront
- **Admin dashboard:** S3 + CloudFront
- **Landing page:** S3 + CloudFront
- **Link app:** S3 + CloudFront
- **Database:** RDS PostgreSQL (`nerava-db.c27i820wot9o.us-east-1.rds.amazonaws.com`)

### CloudFront Distribution IDs

| App | Domain | Distribution ID |
|-----|--------|-----------------|
| Landing | `nerava.network` | `E29NMGJ14FEJSE` |
| Driver | `app.nerava.network` | `E2UEQFQ3RSEEAR` |
| Merchant | `merchant.nerava.network` | `E2EYO3ZPM3S1S0` |
| Admin | `admin.nerava.network` | `E1WZNEUSEZC1X0` |
| Link | `link.nerava.network` | `E10ZCPA7D2D99W` |

### Pre-Deploy: Commit and Push

**IMPORTANT:** Before ANY deployment (backend or frontend), always commit all relevant changes and push to GitHub. This ensures the deployed code matches what's in the repo and enables rollbacks via git history.

```bash
# 1. Stage and commit changes
git add <relevant files>
git commit -m "Description of changes"

# 2. Push to GitHub
git push origin <branch>
```

Never deploy uncommitted or unpushed code. The git history should always reflect what's running in production.

### Deploying the Backend (App Runner)

The backend uses **manual deployment** (`AutoDeploymentsEnabled: false`). The CI workflow (`deploy-prod.yml`) pushes to the `nerava/backend` ECR repo but App Runner reads from the **`nerava-backend`** ECR repo with explicit image tags. To deploy:

```bash
# 0. Commit and push all changes to GitHub first (see Pre-Deploy section above)

# 1. Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 566287346479.dkr.ecr.us-east-1.amazonaws.com

# 2. Build for amd64 (required even on Apple Silicon)
docker build --platform linux/amd64 -t 566287346479.dkr.ecr.us-east-1.amazonaws.com/nerava-backend:<TAG> ./backend

# 3. Push to ECR
docker push 566287346479.dkr.ecr.us-east-1.amazonaws.com/nerava-backend:<TAG>

# 4. Update App Runner to new image tag
aws apprunner update-service \
  --service-arn "arn:aws:apprunner:us-east-1:566287346479:service/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3" \
  --source-configuration '{"ImageRepository":{"ImageIdentifier":"566287346479.dkr.ecr.us-east-1.amazonaws.com/nerava-backend:<TAG>","ImageConfiguration":{"Port":"8000"},"ImageRepositoryType":"ECR"},"AutoDeploymentsEnabled":false,"AuthenticationConfiguration":{"AccessRoleArn":"arn:aws:iam::566287346479:role/nerava-apprunner-ecr-access"}}' \
  --region us-east-1

# 5. Wait for deployment (~3-4 minutes)
aws apprunner list-operations \
  --service-arn "arn:aws:apprunner:us-east-1:566287346479:service/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3" \
  --region us-east-1 --query 'OperationSummaryList[0].[Type,Status]' --output text

# 6. Verify health
curl https://api.nerava.network/healthz
```

**Important:** When updating App Runner, only the `ImageIdentifier` needs to change. All environment variables are preserved from the existing service configuration — do NOT pass `RuntimeEnvironmentVariables` in the update or you risk wiping them.

### Rollback Procedure

To roll back the backend to a previous image tag:

```bash
# 1. Find the previous image tag (check ECR or deploy logs)
aws ecr describe-images --repository-name nerava-backend --region us-east-1 \
  --query 'sort_by(imageDetails,&imagePushedAt)[-5:].imageTags' --output text

# 2. Update App Runner to the previous tag
aws apprunner update-service \
  --service-arn "arn:aws:apprunner:us-east-1:566287346479:service/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3" \
  --source-configuration '{"ImageRepository":{"ImageIdentifier":"566287346479.dkr.ecr.us-east-1.amazonaws.com/nerava-backend:<PREVIOUS_TAG>","ImageConfiguration":{"Port":"8000"},"ImageRepositoryType":"ECR"},"AutoDeploymentsEnabled":false,"AuthenticationConfiguration":{"AccessRoleArn":"arn:aws:iam::566287346479:role/nerava-apprunner-ecr-access"}}' \
  --region us-east-1

# 3. Verify health after ~3 min
curl https://api.nerava.network/healthz
```

For frontend rollback, redeploy the previous git commit's build to S3.

### Deploying Frontend Apps (S3 + CloudFront)

Frontend apps are static builds deployed to S3 with CloudFront cache invalidation:

```bash
# 0. Commit and push all changes to GitHub first (see Pre-Deploy section above)

# Driver app example
cd apps/driver && VITE_API_BASE_URL=https://api.nerava.network VITE_ENV=prod npm run build
aws s3 sync dist/ s3://app.nerava.network/ --delete --region us-east-1
aws cloudfront create-invalidation --distribution-id E2UEQFQ3RSEEAR --paths "/*" --region us-east-1
```

CloudFront invalidation takes ~15-20 seconds to complete.

### Production Logs

Backend logs are in CloudWatch under the App Runner application log group:

```bash
# Tail live logs (exclude health checks)
aws logs tail "/aws/apprunner/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3/application" --follow --region us-east-1

# Search for errors in last hour
aws logs filter-log-events \
  --log-group-name "/aws/apprunner/nerava-backend/88e85a3063c14ea9a1e39f8fdf3c35e3/application" \
  --start-time $(($(date +%s)*1000 - 3600000)) \
  --filter-pattern "ERROR" \
  --region us-east-1 \
  --query 'events[*].message' --output text
```

Log format: `YYYY-MM-DD HH:MM:SS,ms [LEVEL] logger_name: message`
Structured request logs from `app.middleware.logging` include: `request_id`, `method`, `path`, `status_code`, `duration_ms`, `user_id`, `user_agent`.

### Docker Architecture: x86_64 (AMD64) Only

All Docker images **must** be built for `linux/amd64`. On Apple Silicon Macs, use `--platform linux/amd64`. Do not add ARM/Graviton support or use `--platform linux/arm64` without updating both the App Runner configuration and the CI build steps in `.github/workflows/deploy-prod.yml`.

## Key System Details

### Tesla Fleet API Integration

- **OAuth service:** `app/services/tesla_oauth.py` — token management, vehicle data, charging verification
- **Router:** `app/routers/tesla_auth.py` — endpoints under `/v1/auth/tesla/`
- **Models:** `app/models/tesla_connection.py` — TeslaConnection (OAuth tokens), EVVerificationCode (EV-XXXX codes)
- **Charging states accepted:** `{"Charging", "Starting"}` — defined in `TeslaOAuthService.CHARGING_STATES`
- **Multi-vehicle:** `verify_charging_all_vehicles()` checks ALL vehicles on a Tesla account, not just the stored primary
- **Wake retry:** Up to 3 attempts with 5s delays for 408 timeouts or unknown (None) charging states
- **EV codes:** Format `EV-XXXX`, valid for 2 hours, stored in `ev_verification_codes` table

### Campaign / Incentive System

Sponsors create campaigns via the console (`apps/console`) that reward drivers for charging at specific locations.

- **Models:** `Campaign` (budget, targeting rules as JSON, status lifecycle, partner controls), `SessionEvent` (verified charging session, partner fields), `IncentiveGrant` (links a campaign reward to a session, reward_destination)
- **Backend:** `app/routers/campaigns.py` (`/v1/campaigns/*`), `app/routers/campaign_sessions.py` (`/v1/charging-sessions/*`), `app/routers/partner_api.py` (`/v1/partners/*`)
- **Services:** `campaign_service.py` (CRUD + lifecycle), `incentive_engine.py` (evaluates sessions against campaign rules, including partner controls), `corporate_classifier.py` (corporate vs local targeting), `session_event_service.py` (session + grant CRUD), `partner_session_service.py` (external session ingest)

### Partner Incentive API (External Session Ingest)

Enables external partners (charging networks, fleet platforms, driver apps) to submit charging sessions and receive incentive evaluations against active Nerava campaigns via API.

- **Models:** `Partner` (trust tier, rate limit, webhook config), `PartnerAPIKey` (SHA-256 hashed, `nrv_pk_` prefix, scoped)
- **Auth:** `X-Partner-Key` header → `app/dependencies/partner_auth.py`. Key hashed + matched against `partner_api_keys.key_hash`. Scope-checked per endpoint.
- **Partner API Router:** `app/routers/partner_api.py` (`/v1/partners/*`)
  - `POST /sessions` — Submit a charging session (idempotent via `partner_session_id`)
  - `GET /sessions` — List partner's sessions
  - `GET /sessions/{id}` — Get session + grant details
  - `PATCH /sessions/{id}` — Update telemetry or complete session
  - `GET /grants` — List grants for partner's sessions
  - `GET /campaigns/available` — Active campaigns matching partner's trust tier
  - `GET /me` — Partner profile + usage stats
- **Admin Router:** `app/routers/admin_partners.py` (`/v1/admin/partners/*`) — Partner + key CRUD (JWT admin auth)
- **Services:** `partner_service.py` (partner CRUD, key generation), `partner_session_service.py` (session ingest, shadow driver resolution, quality scoring)
- **Shadow users:** Partner drivers get `auth_provider="partner"` users with email `partner_{slug}_{driver_id}@partner.nerava.network`. Satisfies `SessionEvent.driver_user_id` NOT NULL FK.
- **Trust tiers:** 1=hardware-verified (+20 quality), 2=api-verified (+10), 3=app-reported (-10). Campaigns can require minimum tier.
- **Idempotency:** `(source, source_session_id)` unique constraint on `session_events`. Source is `partner_{slug}`.
- **Tests:** `backend/tests/test_partner_api.py` — 19 tests covering full flow, auth, idempotency, campaign matching, partner controls

### Merchant Enrichment

When a merchant is resolved via the acquisition funnel, the backend enriches it from Google Places:

- **Service:** `app/services/merchant_enrichment.py` — calls `google_places_new.py` for place details, photos, open/closed status
- **Gotchas:** Google Places photo URLs can exceed 500 chars; the `primary_photo_url` and `photo_url` columns are varchar(255), so long URLs are stored only in the `photo_urls` JSON column. The `priceLevel` field is a string enum (e.g. `PRICE_LEVEL_MODERATE`), not an integer.

### Feature Flags

Environment-based flags in `app/routers/flags.py`:
- Checked via `is_feature_enabled(flag_name, environment)`, resolves based on `ENV` env var (dev/staging/prod)
- Endpoints: `GET /v1/flags`, `GET /v1/flags/{flag_name}`, `POST /v1/flags/{flag_name}/toggle` (admin only)

### Backend Scripts

Key scripts in `backend/scripts/`:

- `prod_api_health_check.py` — comprehensive production API health check (used by `health-check.yml` workflow)
- `daily_prod_report.py` — queries CloudWatch Logs Insights for daily digest, publishes to SNS
- `db_backup.sh` / `db_restore.sh` — database backup and restore
- `seed_chargers_bulk.py` — bulk charger seeding from NREL/Overpass
- `seed_merchants_free.py` — merchant seeding for free tier
- `seed_if_needed.py` — auto-seed on first run
- `run_migrations.py` — migration runner

## Environment Variables

### Backend (key vars from `app/core/config.py`)

```
# Auth
JWT_SECRET / NERAVA_SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES (default 10080 = 7 days)

# Database
DATABASE_URL (default sqlite:///./nerava.db), REDIS_URL (optional)

# Stripe
STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, ENABLE_STRIPE_PAYOUTS
MINIMUM_WITHDRAWAL_CENTS (2000), WEEKLY_WITHDRAWAL_LIMIT_CENTS (100000)

# Tesla
TESLA_CLIENT_ID, TESLA_CLIENT_SECRET, TESLA_MOCK_MODE, TESLA_WEBHOOK_SECRET

# Google / Apple
GOOGLE_PLACES_API_KEY, GOOGLE_CLIENT_ID, APPLE_CLIENT_ID

# Partner API
PARTNER_DEFAULT_RATE_LIMIT_RPM (default 60)

# Other
ENV (dev/staging/prod), OTP_PROVIDER (stub for dev), SENTRY_DSN
FRONTEND_URL, PUBLIC_BASE_URL, API_BASE_URL, DRIVER_APP_URL
TOKEN_ENCRYPTION_KEY (Fernet), PLATFORM_FEE_BPS (2000 = 20%)
```

### Driver App (VITE_ prefix)

```
VITE_API_BASE_URL, VITE_ENV, VITE_APPLE_CLIENT_ID, VITE_GOOGLE_CLIENT_ID
VITE_SENTRY_DSN, VITE_POSTHOG_KEY
```

## Scaling & Cost Considerations

### Data Volume at Scale

The charging session polling endpoint (`POST /v1/charging-sessions/poll`) is the hot path. Each active driver polls every 60 seconds.

**Per-session storage:** ~500 bytes (`session_events`) + ~300 bytes (`incentive_grants` if matched) + index overhead (~60-80%).

| Scale | Sessions/month | DB storage/year | CloudWatch logs/month | Est. total cost/month |
|-------|---------------|-----------------|----------------------|----------------------|
| 10K drivers | 107K | ~1 GB | ~10.8 GB | ~$135 |
| 100K drivers | 1.07M | ~10 GB | ~108 GB | ~$708 |
| 1M drivers | 10.75M | ~100 GB | ~1.08 TB | ~$3,530 |

**Key cost drivers:** CloudWatch log ingestion ($0.50/GB) exceeds DB cost at scale. Polling compute (App Runner instances) is the largest single cost. App Runner caps at 25 instances — need ECS/EKS migration at ~500K+ drivers.

### Known Scaling Gaps

- **No data retention policy:** Session events accumulate forever. Need TTL-based archival to S3.
- **No CloudWatch log sampling:** Every poll request logged. Should skip 200s on `/poll` or sample 1-in-N.
- **Polling-based architecture:** Push-based (Tesla webhooks) would eliminate majority of compute cost.
- **Tesla API rate limits:** Undocumented limits; at 100K+ concurrent polls/minute, will likely be throttled.
- **App Runner ceiling:** 25-instance cap per service.

### Domain Model Gotcha

There are **two** DriverWallet models:
- `app/models/driver_wallet.py` → `DriverWallet` — Stripe payout wallet (balance_cents, stripe_account_id). PK is `id` (UUID), unique on `driver_id`.
- `app/models_domain.py` → `DriverWallet` (re-exported from driver_wallet.py) — Same model. The `energy_reputation_score` column lives on this model (added via migration 018). The domain.py file re-exports it for backward compatibility.

When querying reputation, use `DriverWallet.user_id` (mapped to `driver_id` column) not `DriverWallet.id`.

### Partner Session Columns on Existing Tables

Three existing tables have partner-related columns added via migration 105:
- **`session_events`**: `partner_id` (FK to partners.id, nullable), `partner_driver_id` (partner's driver identifier, nullable). Indexed on `(partner_id, session_start)`.
- **`incentive_grants`**: `reward_destination` (default `"nerava_wallet"`, also `"partner_managed"` or `"deferred"`).
- **`campaigns`**: `allow_partner_sessions` (bool, default true), `rule_partner_ids` (JSON, nullable), `rule_min_trust_tier` (int, nullable).

All columns are nullable with defaults — zero risk to existing data. Sessions from Nerava's own app have `partner_id=NULL` and skip all partner checks in the IncentiveEngine.

## Business Model: Three Verified Outcome Categories

Nerava monetizes verified EV outcomes across charging, spend, and data. The company is described by **outcome type**, not customer type.

### 1. Charging Outcomes

Nerava verifies and monetizes charging behavior. Examples: verified charging sessions, off-peak charging, charger utilization shifts, dwell-time thresholds, charger-cluster activation, campaign-triggered charging behavior.

**Who pays:** Charging networks, utilities, hardware brands (e.g., EVject), sponsors.

**Campaign type:** `session` — driver charges at the right place/time, grant pays on session end. This is the **existing system** (live today).

### 2. Spend Outcomes

Nerava verifies and monetizes charger-adjacent commerce. Examples: verified merchant visits, receipt-confirmed purchases, redemptions, spend thresholds, local conversion during charging dwell.

**Who pays:** Merchants, retail chains, restaurants, hotels, local sponsors.

**Campaign type:** `receipt` — driver charges nearby, visits merchant, uploads receipt photo. Grant is `pending_verification` until receipt OCR confirms merchant + spend + timestamp. Phase 1 uses Taggun OCR ($0.04/scan). Phase 2 upgrades to card-linked verification via Fidel API (automatic, no driver action per visit).

### 3. Data Outcomes

Nerava monetizes intelligence created from verified charging and spend behavior. Examples: charger safety/quality reviews, charging behavior surveys, photo reports, location ratings.

**Campaign type:** `data` — driver charges at charger, submits structured data (review, survey, photos). Grant is `pending_verification` until submission validated.

**Who pays:** Charging networks, utilities, real estate groups, brands, insurers, enterprise partners.

### Campaign Stacking

One driver session can earn grants from **up to three campaigns** (one per type) funded by **different buyers**. The unique constraint on `incentive_grants` is `(session_event_id, grant_type)` not just `session_event_id`. Each campaign type has "highest priority wins" within its category. Buyers only see their own campaign performance.

### Merchant Funding Model

- **Free trial:** Merchant enters promo code (e.g., `NERAVA100`) → gets $100 campaign balance, platform fee waived, campaign auto-activates
- **Paid deposits:** Merchant adds funds via Stripe checkout → 20% platform fee → net credited to campaign balance
- **Auto-pause:** Campaign pauses when balance < reward amount
- **Sponsor trials:** Same promo code system (e.g., `EVJECT500` → $500 free credit)

This model mirrors Upside's pay-for-performance approach: merchants pay only when verified customers spend money.

## Tesla Fleet API Costs & Polling Strategy

### Per-Request Pricing (effective Jan 1, 2025)

| Category | Unit | Cost | Per-request |
|----------|------|------|-------------|
| **vehicle_data** (REST) | 500 requests | $1.00 | **$0.002/request** |
| **wake_up** | 50 requests | $1.00 | **$0.02/wake** |
| **Commands** | 1,000 requests | $1.00 | $0.001/command |
| **Streaming Signals** | 150,000 signals | $1.00 | $0.0000067/signal |
| **specs endpoint** | per result | — | $0.10/result |

Every account gets a **$10/month credit** (covers ~2 vehicles at light usage). No volume discounts or tiers.

### Rate Limits

- `vehicle_data`: 60 requests/minute per device
- `wake_up`: 3 requests/minute
- Device commands: 30 requests/minute

### Cost at Nerava's Scale

Current architecture: `vehicle_data` every 60s per active driver.

| Scale | Sessions/month | Tesla API cost/month |
|-------|---------------|---------------------|
| 1,000 drivers | 2,500 | $200-300 |
| 10,000 drivers | 25,000 | $2,000-3,000 |
| 100,000 drivers | 250,000 | $20,000-30,000 |

Per session: ~$0.06 (vehicle_data) + ~$0.04 (wake calls) = **~$0.08-0.12/session**

### Fleet Telemetry vs Polling

| Method | Cost/hr/vehicle | At 100K drivers/mo |
|--------|----------------|-------------------|
| REST polling (60s) | $0.12/hr | $20K-30K |
| Fleet Telemetry streaming | $0.007/hr | $2K-4K |

Streaming is **18x cheaper**. Tesla explicitly says "the vehicle_data endpoint should never be polled regularly."

### Upcoming Tesla Changes

- **July 1, 2025:** vehicle_data charged at 2 command credits per call (effectively doubling cost)
- **October 1, 2025:** Legacy vehicle (Intel Atom) discount removed
- Tesla is sunsetting REST polling in favor of Fleet Telemetry

### Server-Side Polling Strategy (Current Plan)

**Phase 1 (immediate):** Extend `scheduled_polls` worker to continuous server-side polling
- Client triggers first poll → if charging detected, set `next_poll_at = now + 60s`
- Worker picks it up every 120s, polls Tesla, updates session
- If still charging: re-enqueue `next_poll_at = now + 60s`
- If not charging: end session, evaluate incentive, clear `next_poll_at`
- Client switches to lazy DB read (`GET /active`) once session detected
- ~50 lines of code change to existing worker

**Phase 2 (weeks):** Deploy Fleet Telemetry
- Apply existing Terraform in `infra/terraform/fleet-telemetry/`
- ECS Fargate task (~$30/mo) receives WebSocket streams from vehicles
- SQS/Kinesis queue → Lambda/ECS worker processes state transitions
- Auto-subscribe vehicles during Tesla OAuth flow
- Vehicles with `telemetry_enabled=true` get push-based sessions
- Others fall back to Phase 1 server-side polling

**Phase 3 (scale):** Full Fleet Telemetry, remove polling entirely

### Fleet Telemetry Infrastructure (Existing)

- **Terraform:** `infra/terraform/fleet-telemetry/` — ECS Fargate task, NLB, Route53, Secrets Manager
- **Backend:** `app/routers/tesla_telemetry.py` (webhook receiver), `app/routers/tesla_telemetry_config.py` (per-vehicle config)
- **Service:** `app/services/tesla_oauth.py` → `subscribe_vehicle_telemetry()` (configures vehicle streaming fields)
- **Model:** `TeslaConnection.telemetry_enabled` column exists
- **Certs:** `infra/certs/` — EC keys (need rotation to Secrets Manager)
- **Missing:** Real CA cert (Let's Encrypt), message queue, event-driven session processor, virtual key pairing UX

## Merchant Exclusive System Architecture

### Two Merchant Models (Critical Gotcha)

There are **two separate merchant tables** that represent the same physical business:

| Table | Model | ID Format | Purpose |
|-------|-------|-----------|---------|
| `domain_merchants` | `DomainMerchant` | UUID (`17047c8e-...`) | Merchant portal accounts, owned by logged-in merchant users |
| `merchants` | `Merchant` (WYC) | String (`osm_123`, `google_ChIJ...`) | Driver-facing merchant data from OSM/Google seeding |

A single restaurant may have **multiple WYC Merchant records** (one from OSM seeding, one from Google Places) and **one DomainMerchant** record (created when the merchant claims their business via the portal).

### ChargerMerchant Links

`charger_merchants` is the junction table connecting chargers to nearby WYC merchants. Each link has:
- `exclusive_title` — The offer shown in the driver app (e.g., "Free Garlic Knots")
- `exclusive_description` — Offer detail text
- `distance_m`, `walk_duration_s` — Proximity data

**The driver app reads `ChargerMerchant.exclusive_title` directly** from the charger detail endpoint. This is the source of truth for what drivers see.

### The Matching Problem

When a merchant edits their exclusive via the portal, the system must find ALL ChargerMerchant links for their business across ALL WYC merchant variants. The `_find_all_charger_merchant_links()` helper in `merchants_domain.py` does this by matching:
1. WYC merchants by `place_id` (matching DomainMerchant's `google_place_id`)
2. WYC merchants by name (case-insensitive match on DomainMerchant's `name`)

**Known issue:** If the DomainMerchant name doesn't exactly match any WYC merchant name (e.g., "The Heights Pizzeria and Drafthouse" vs "The Heights Pizzeria"), the lookup returns empty. Edits from the portal won't propagate to the driver app.

### Exclusive CRUD Flow

- **Create:** Sets `exclusive_title` on all ChargerMerchant links found by the helper
- **Update:** Same — updates all links, WYC `perk_label`, and DomainMerchant `perk_label`
- **Toggle (on/off):** Sets `exclusive_title = None` on all links when disabled
- **List:** Uses the same helper, filters for `exclusive_title IS NOT NULL`, deduplicates by title
- **IDs:** Exclusives from ChargerMerchant links use `cm_{link.id}` format (integer PK)

### MerchantPerk Table (Legacy, DO NOT USE for new exclusives)

`merchant_perks` has an FK to `merchants.id` (WYC table). The DomainMerchant UUID does NOT exist in the WYC table, so creating a MerchantPerk with a DomainMerchant ID causes **FK violation in PostgreSQL**. All exclusive operations now go through ChargerMerchant links directly.

## Merchant Portal Auth & Claim Flow

### Google SSO Flow

1. Merchant clicks "Sign in with Google" on `/claim`
2. Redirects to Google OAuth consent screen
3. Returns to `/callback` with auth code
4. `GoogleCallback.tsx` exchanges code → stores `access_token` in localStorage
5. Calls `fetchMyMerchant()` to check if user already has a claimed business
6. If merchant exists → sets `businessClaimed=true`, `merchant_id` → navigates to `/overview`
7. If no merchant → navigates to `/claim/location` for business search

### Claim Flow

1. `/claim/location` (`SelectLocation.tsx`) — tries GBP locations first, falls back to Places search
2. User searches, selects a business, clicks "Confirm Location"
3. `POST /v1/merchant/claim` → creates `MerchantLocationClaim` + `DomainMerchant`, returns `merchant_id`
4. Frontend stores `merchant_id`, `businessClaimed=true`, `place_id`, `merchant_name` in localStorage
5. Full page reload via `window.location.href` to `/overview` (not React Router navigate — needed because `App.tsx` reads `isClaimed` from localStorage at render time, and client-side navigation doesn't re-render `App`)

### Auth Guard

`App.tsx` checks `localStorage.getItem('businessClaimed') === 'true'` to decide dashboard vs claim flow. Uses `window.location.href` (not `navigate()`) after claiming because React Router navigation doesn't re-render the parent `App` component to pick up the new localStorage value.

### Merchant Ownership (Multiple DomainMerchant Records)

A user can end up with multiple DomainMerchant records if they re-claim. `AuthService.get_user_merchant(db, user_id, merchant_id=)` accepts an optional `merchant_id` to verify ownership of a specific record. All dashboard endpoints pass the requested `merchant_id` to this check. `link_location_to_merchant()` now reuses existing DomainMerchant records when the same user re-claims.

### Key localStorage Keys

| Key | Purpose |
|-----|---------|
| `access_token` | JWT for API auth |
| `merchant_id` | DomainMerchant UUID |
| `merchant_name` | Display name |
| `businessClaimed` | Gate for dashboard access |
| `merchant_authenticated` | Skip Google login on claim page |
| `place_id` | Google Place ID |

## Claim-Based Merchant Billing (Current Direction)

**Supersedes the receipt OCR model.** See `CHARGING_VERIFIED_COMMERCE.md` for full implementation plan.

### Core Billing Event: Claim + Presence

A billable event happens when:
1. Driver is in an **active charging session** (Tesla/Smartcar verified)
2. Driver is **within 250-350m** of the merchant (GPS verified)
3. Driver taps **"Claim Offer"** (explicit intent)

This is a **qualified EV charging lead** — stronger than a Google Ad click.

### Pricing: AOV-Based Dynamic Pricing

| AOV Bracket | Claim Cost (4% of AOV) |
|------------|----------------------|
| Under $10 | $0.40 |
| $10-25 | $1.00 |
| $25-50 | $2.00 |
| $50-100 | $4.00 |

Merchants prepay campaign budgets. Daily cap protects against overspend. Auto-pause when budget exhausted.

### Toast POS Integration (Read-Only)

**Purpose:** Auto-calibrate AOV from real order data, track claim-to-order conversion.

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/merchant/pos/toast/connect` | Start OAuth flow |
| `POST /v1/merchant/pos/toast/callback` | Exchange code for tokens |
| `GET /v1/merchant/pos/status` | Connection status + AOV |
| `POST /v1/merchant/pos/toast/disconnect` | Remove credentials |
| `GET /v1/merchant/pos/toast/aov` | Calculate AOV from orders |

- **Service:** `backend/app/services/toast_pos_service.py`
- **Router:** `backend/app/routers/toast_pos.py`
- **Token storage:** `MerchantOAuthToken` with `provider="toast"`, `gbp_account_id` reused for restaurant GUID
- **Mock mode:** `TOAST_MOCK_MODE=true` (default) — returns realistic fake data
- **OAuth state:** Falls back to in-memory dict if `pos_oauth_states` table not yet migrated
- **Config:** `TOAST_CLIENT_ID`, `TOAST_CLIENT_SECRET`, `TOAST_MOCK_MODE`

## Known Production Issues & Gotchas

### Python 3.9 Constraint

Production Docker image uses Python 3.9-slim. **Do NOT use PEP 604 union syntax** (`str | None`). Use `Optional[str]` instead. This caused a startup crash and rollback.

### Schema Drift

Some SQLAlchemy model columns don't exist in the production PostgreSQL database:
- `nova_transactions.campaign_id` — commented out in model, not yet migrated
- `pos_oauth_states` table — migration 111 exists but not applied to production

When adding columns to models, **always create an Alembic migration** and apply it to production before deploying code that references the column.

### App Runner Deployment

- `main_simple.py` is the entry point, **NOT** `lifespan.py`
- Startup events use `@app.on_event("startup")` in `main_simple.py` (line ~1314)
- Default startup mode is "light" — skips optional background workers and returns early
- Any startup code must go BEFORE the light mode early return
- `print(flush=True)` stdout may not appear in CloudWatch logs — use `logger.info()` instead

### Multi-Instance State

App Runner can run multiple instances. **In-memory dicts don't share state** across instances. OAuth state tokens stored in-memory on instance A won't be found when the callback hits instance B. Use database-backed storage or pass state through the authenticated user.

### Hardcoded Asadas Grill References

~15 hardcoded references to "Asadas Grill" across backend code from the demo period:
- `chargers.py` lines 222, 377: hardcoded photo URL path
- `chargers.py` line 908: seed exclusive titles
- `main_simple.py`: static file mount for photos
- `bootstrap.py`: cluster setup, place ID matching
- Various seed scripts in `backend/scripts/`

These should be cleaned up as the system generalizes to support any merchant.

## Session Bug Fixes (Deployed 2026-03-12)

Three critical fixes deployed to production (`session-fix-20260312-1005`):

1. **Stale timeout 5min → 15min** in `_close_stale_session()` — prevents premature session closure when app backgrounds
2. **Session reopening logic** — if car is still charging within 30 min of a stale-cleanup or manual close, reopens the existing session instead of creating a duplicate
3. **Incentive evaluation on all end paths** — `_close_stale_session()` and `end_session_manual()` now both call `IncentiveEngine.evaluate_session()`, fixing silent reward loss

Also fixed: `charge_data` → `charge_state` variable name bug in reopening logic that would have caused `NameError` at runtime.

## Driver Claim Flow (Deployed 2026-03-18)

Claim Reward and Request to Join CTAs added to the driver app's charger detail amenities tab.

### Claim Reward Flow (Nerava Merchants)

1. Driver taps merchant in Amenities tab → `MerchantActionSheet` opens
2. Nerava merchants (`is_nerava_merchant=true`) show green "Claim Reward" button with exclusive title
3. If not charging, shows "Plug in to claim" hint (button still visible)
4. Tap → `ClaimConfirmModal` with charging eligibility check
5. Confirm → `POST /v1/exclusive/activate` creates `ExclusiveSession` (ACTIVE)
6. `ActiveVisitTracker` appears: walking path visualization, elapsed timer, progress bar
7. "I'm Done — Complete Visit" → marks session COMPLETED

### Request to Join Flow (Non-Nerava Merchants)

1. Non-Nerava merchants show blue "Request to Join Nerava" button
2. Tap → `POST /v1/merchants/{placeId}/request-join` with merchant name
3. Toast confirmation: "Request sent for {name}"
4. Badge shows request count on amenities tab (e.g., "3 requested")
5. Already-requested merchants show disabled "Requested" button

### Key Files

| File | Purpose |
|------|---------|
| `apps/driver/src/components/ChargerDetail/ChargerDetailSheet.tsx` | Action sheet, claim modal, visit tracker |
| `backend/app/routers/exclusive.py` | Exclusive session activation/verification/completion |
| `backend/app/routers/chargers.py` | `is_nerava_merchant` + `join_request_count` on nearby merchants |
| `backend/app/services/push_service.py` | `send_nearby_merchant_push()` on charging detection |

### Charger Detail API Fields (NearbyMerchantResponse)

Two new fields added to `/v1/chargers/{id}/detail` and `/v1/chargers/discovery`:
- `is_nerava_merchant: bool` — true if merchant has an active exclusive/perk
- `join_request_count: int` — number of drivers who requested this merchant join Nerava

### Merchant Portal Visits

The Visits page (`merchant.nerava.network/visits`) queries `ExclusiveSession` records. Since `ExclusiveSession.merchant_id` stores WYC merchant IDs (not DomainMerchant UUIDs), the endpoint uses `_find_all_charger_merchant_links()` to resolve WYC IDs from the DomainMerchant, plus matches by `google_place_id`. Statuses: ACTIVE (blue), VERIFIED/COMPLETED (green), EXPIRED/REJECTED (red).

### Session Trail Map (GPS Breadcrumbs)

The session trail map on `SessionCard` renders GPS points collected during charging:
- Points stored in `session_events.session_metadata["location_trail"]` as `[{lat, lng, ts}, ...]`
- Collected from the phone's `navigator.geolocation` on each poll (`POST /v1/charging-sessions/poll`)
- One point per poll (~60s intervals), capped at 120 points
- **Requires app to be in foreground** — polling pauses when backgrounded (`usePageVisibility`)
- Rendered via Leaflet `Polyline` in `SessionTrailMap.tsx`

### Exclusive Activation Gotchas

- **Auth provider check:** Accepts any verified provider (phone, google, apple). Previously blocked Google SSO users with 428.
- **Merchant ID resolution:** Frontend sends Google Place ID as `merchant_id`, but `exclusive_sessions` has FK to WYC `merchants` table. Backend resolves `place_id` → WYC `merchant.id` before insert.
- **HTTPException swallowing:** The catch-all `except Exception` was catching `HTTPException` and re-raising as 500. Now re-raises `HTTPException` before the catch-all.
- **Name matching:** `_find_all_charger_merchant_links()` uses partial name matching (SQL LIKE) as fallback when exact match fails (e.g., "The Heights Pizzeria" vs "The Heights Pizzeria and Drafthouse").
