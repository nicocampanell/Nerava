# Copilot Code Review Instructions — Nerava

## What Nerava Is

Nerava is **"the Stripe for EV charging dwell time"** — a verified commerce platform for the EV charging ecosystem. Drivers charge their EVs, nearby merchants reach them with offers during 20-45 minute charging sessions. The core billing event is **Claim + Presence**: driver actively charging + within walking distance + taps "Claim Offer" = one qualified charging lead billed to the merchant.

## Architecture

- **Backend:** FastAPI monolith (Python 3.9), SQLAlchemy 2, Alembic, PostgreSQL on RDS, deployed on AWS App Runner
- **Driver App:** React 19, Vite 7, Tailwind 4, React Router 7, React Query — WKWebView on iOS, WebView on Android
- **Merchant Portal:** React 18, Vite 5, Radix UI
- **Admin Dashboard:** React 18, Vite 5, Radix UI, Recharts
- **Sponsor Console:** React 18, Vite 5, Radix UI, React Router 6
- **Infrastructure:** AWS (App Runner, RDS PostgreSQL, S3 + CloudFront, SES, ECR)

## Critical Review Rules

### Python / Backend

1. **Python 3.9 ONLY.** Do NOT approve PEP 604 union syntax (`str | None`). Use `Optional[str]`. This has caused production crashes and rollbacks.
2. **Every file that uses `logger` must have `import logging` and `logger = logging.getLogger(__name__)`.** A missing logger import caused auth to fail for all users in production.
3. **All new model columns require an Alembic migration.** Flag any new column added to a SQLAlchemy model without a corresponding migration file.
4. **`SELECT FOR UPDATE` on any budget/balance mutation.** The incentive engine and wallet operations must use row-level locking. Flag any `UPDATE` to `spent_cents`, `balance_cents`, or `nova_balance` without `with_for_update()`.
5. **No hardcoded secrets.** Flag any string that looks like an API key, password, JWT secret, or connection string. We've had keys committed before.
6. **No `print()` in production code.** Use `logger.info/error/warning`. Print statements don't appear in CloudWatch logs.
7. **App Runner entry point is `main_simple.py`.** Any startup code must go before the light-mode early return.
8. **Two merchant models exist.** `merchants` (WYC, driver-facing) and `domain_merchants` (portal, merchant-facing). Flag any confusion between the two.

### TypeScript / Frontend

1. **API calls must go through `api.ts` service layer.** Flag any raw `fetch()` calls outside of the service file.
2. **`VITE_API_BASE_URL` must be set for production builds.** Without it, apps default to `localhost:8001`. This broke admin, merchant, and console portals in production.
3. **No `localStorage` for sensitive data** other than `access_token`. Session state should use React Query or Context.
4. **Mock/demo features must be gated** behind admin account check (`public_id` verification), not just a localStorage flag.
5. **Driver app uses Tailwind 4** (not 3). Other apps use Tailwind 3. Don't confuse the configs.

### Database / Migrations

1. **All columns must be nullable with defaults** when adding to existing tables. Non-nullable columns without defaults will crash on existing rows.
2. **Migrations must have both `upgrade()` and `downgrade()`.** Flag any migration missing downgrade.
3. **Check for FK violations.** `domain_merchants.id` (UUID) does NOT exist in `merchants.id` (string). Creating a `MerchantPerk` with a DomainMerchant ID causes FK violation in PostgreSQL.

### Security

1. **Rate limiting on all auth endpoints.** Flag any new auth endpoint without rate limit middleware.
2. **CORS origin validation.** New domains must be added to the CORS allowlist.
3. **JWT tokens must include `iss: "nerava"` and `aud: "nerava-api"`.** Tokens without these claims are rejected.
4. **Partner API keys use `X-Partner-Key` header** with SHA-256 hashing. Never log or return the full key.

### Infrastructure

1. **Docker images must be `linux/amd64`.** No ARM/Graviton. App Runner only runs x86_64.
2. **App Runner env vars are preserved on update** only if you DON'T pass `RuntimeEnvironmentVariables`. If you pass them, you must include ALL vars or they get wiped.
3. **CloudFront invalidation required** after every S3 deploy. Without it, users see stale content for up to 24 hours.

## Known Issues to Watch For

- `spent_cents` budget counter had a bug where it wasn't incrementing — any changes to grant creation must verify atomic budget decrement
- SendGrid credits are exhausted — email OTP now uses AWS SES
- SES is in sandbox mode — only verified recipient emails work until production access is approved
- The referral system's `grant_referral_rewards()` was never called — verify it's wired to session completion
- Tesla Fleet API polling costs $0.002/request — flag any code that polls more frequently than every 60 seconds

## Test Coverage Expectations

- Backend: pytest with in-memory SQLite. New endpoints need at least one happy-path test.
- Driver app: Vitest + React Testing Library. New components need render tests.
- E2E: Playwright for critical flows (charging session, wallet, merchant claim).
