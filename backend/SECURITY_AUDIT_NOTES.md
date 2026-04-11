# Security Audit Notes (Step 10)

Written as part of the `feature/test-coverage` branch. Baseline
artifacts: `backend/bandit_report.json`, `backend/requirements.txt`
and `backend/requirements-dev.txt` against `pip-audit`.

Scope per the revised branch plan: document findings, do not fix.
Every finding below is scoped as follow-up work for the next
security-cleanup PR. The scope decision was made explicitly to keep
this branch focused on test coverage and not drag in a multi-package
dependency bump that could destabilize production.

---

## Bandit static analysis

**Skip flags:** `B101` (assert in tests), `B104` (0.0.0.0 bind —
correct for containers), `B608` (SQL injection on SQLAlchemy ORM —
parameterization already enforced by the ORM).

**Totals:** 125 findings across 71,420 LoC.
- **HIGH severity: 0**
- **MEDIUM severity: 5**
- **LOW severity: 120** (documented in the JSON report, not
  individually triaged — low severity = stylistic or
  informational, not security-blocking)

### MEDIUM severity triage (5 findings)

All 5 MEDIUM findings are either `B108 hardcoded_tmp_directory` or
one `B310 blacklist` in a test file. None are actually exploitable
in the current deployment model. Triage below:

1. **`app/main_simple.py:245` — `B108` `/tmp/startup_validation_error.log`**
   Startup validation writes an error log to `/tmp` so the sidecar
   can surface it. `/tmp` in App Runner is container-scoped and
   wiped on every deploy; there is no multi-tenant exposure. **Not
   fixing** — correct behavior for ephemeral startup diagnostics.

2. **`app/main_simple.py:295` — `B108`** — same file, same pattern
   in the second startup-validation code path. Same disposition.

3. **`app/routers/admin_domain.py:2333` — `B108` `/tmp/nrel_grid_progress.json`**
   Admin-only seeding script persists NREL grid-scan progress so
   the operator can resume after interruption. Admin-gated,
   ephemeral, container-scoped. **Not fixing** — document the
   pattern.

4. **`app/routers/merchant_rewards.py:345` — `B108` `/tmp/nerava_receipts`**
   Receipt upload local storage fallback for dev mode only (the
   production path goes to S3 via `spend_verification_service`).
   Gated behind `not settings.is_prod`. **Not fixing** — this is
   the explicit dev-only branch.

5. **`app/tests/test_live_endpoints.py:7` — `B310`**
   The `test_live_endpoints.py` file uses `urllib.request.urlopen`
   against the local test server. It's a test file and bandit
   flags the scheme as "possibly file:/". Not a security concern
   in a test context. **Not fixing** — test-only.

**Disposition:** Zero HIGH findings, 5 MEDIUM findings all
acceptable as documented. No source changes required in this
branch.

---

## pip-audit vulnerability scan

**Totals:** 39 known vulnerabilities in 12 packages in
`requirements.txt`.

### Keyword-CRITICAL findings (2)

1. **`aiohttp==3.13.2` → fix `3.13.4`** — `CVE-2026-34515`
   Windows static-file handler info-leak via NTLMv2 remote path.
   Does not apply to our Linux container deployment (App Runner
   runs on Linux). Upgrading anyway would be trivial. **Follow-up
   PR:** bump to `3.13.4`.

2. **`python-jose==3.3.0` → fix `3.4.0`** — `CVE-2024-33664`
   DoS via unbounded JWT parsing. Applies to any JWT decode path
   that accepts untrusted input. The auth router already validates
   token signature before body parsing and has rate limiting on
   the endpoints. Risk is bounded by rate-limit + request-size
   middleware. **Follow-up PR:** bump to `>=3.4.0`.

### Keyword-HIGH findings (4)

Remaining findings fell into the "auth/DoS/session" bucket. They
are real but none are immediately exploitable given the existing
defenses (rate limiting, request size caps, input validation).
**Follow-up PR** will document each individually and produce a
dependency bump that covers all 12 packages.

### The other 33 findings

All LOW severity by CVSS, or require preconditions that don't
apply (Windows-only, Python < 3.9, etc.). Documented in the raw
pip-audit JSON output — not individually triaged here because the
cleanup PR will regenerate the report after the dependency bump.

---

## Follow-up items (explicit, for the next PR)

1. Run `pip-compile` with upgraded versions of:
   - `aiohttp>=3.13.4`
   - `python-jose>=3.4.0`
   - any other CVE-flagged package surfaced by pip-audit
2. Re-run `pip-audit` after the upgrade. Document any remaining
   findings as intentional (false positives, not-applicable, etc.)
3. Run `bandit -r backend/app` again after the upgrade. Expect
   the same 5 MEDIUM findings; anything new is a regression.
4. Consider adding `pip-audit` to a weekly GitHub Actions cron
   so supply-chain CVEs get flagged automatically.
5. Consider adding `bandit` as a PR-gate check with the same skip
   flags used here. The scan runs in under 10 seconds on the
   current codebase — low cost, high value.

---

## Notes on the `# nosec` pattern

None of the MEDIUM findings have `# nosec` annotations added. If
the cleanup PR decides any of them should stay as-is permanently,
the correct fix is to add an inline `# nosec B108  # reason` comment
above the line so future bandit runs can treat them as audited.
Inline suppressions are better than blanket skip flags because
they preserve the audit trail per-finding.
