# Claude Code MCP Recommendations for Nerava

**Date:** April 2, 2026
**Purpose:** MCP servers to improve development quality, speed, security, and monitoring

---

## What Are MCPs?

Model Context Protocol (MCP) servers give Claude Code direct access to databases, APIs, monitoring systems, and infrastructure — without leaving the terminal. Instead of copy-pasting AWS logs or manually querying the database, Claude can do it directly.

---

## Priority 1: Install Now (Immediate Impact)

### 1. PostgreSQL MCP — Direct Database Access

**Why:** We currently can't query production RDS from local (VPC-only). This MCP lets Claude run read-only queries against production to debug issues, audit data, and generate reports without manual SQL.

**Impact:** Eliminates the "I can't reach the DB" blocker that slowed down merchant seeding, campaign auditing, and user debugging throughout this sprint.

```bash
# First: create a read-only database user in production
# Then:
claude mcp add --scope local --transport stdio \
  --env DATABASE_URL="postgresql://readonly_user:password@nerava-db.c27i820wot9o.us-east-1.rds.amazonaws.com:5432/nerava" \
  postgres -- npx @modelcontextprotocol/server-postgres
```

**Use cases:**
- "How many sessions did driver 3 have this week?"
- "Show me all campaigns where spent_cents > budget_cents" (audit the budget bug)
- "Count chargers by state" (verify seeding)
- "Find all exclusive sessions for Heights Pizzeria"

---

### 2. Sentry MCP — Error Monitoring

**Why:** We have Sentry configured (`SENTRY_DSN` set on App Runner) but currently check errors via CloudWatch log grep. The Sentry MCP gives Claude direct access to error traces, stack traces, and deployment correlation.

**Impact:** Faster debugging. Instead of "check the logs for 500 errors", Claude can pull the exact stack trace and affected users.

```bash
claude mcp add --scope local --transport stdio \
  --env SENTRY_AUTH_TOKEN="your_sentry_auth_token" \
  sentry -- npx @sentry/mcp-server
```

**Use cases:**
- "What errors happened in the last hour?"
- "Show me the stack trace for the verify visit 500"
- "Which users are hitting the most errors?"

---

### 3. GitHub MCP — PR and Issue Management

**Why:** We manage all code via GitHub but currently use `gh` CLI or manual git commands. The GitHub MCP lets Claude create PRs, review code, manage issues, and check CI status directly.

**Impact:** Faster code review, PR creation, and issue tracking.

```bash
claude mcp add --scope project --transport stdio \
  --env GITHUB_TOKEN="ghp_your_token" \
  github -- npx @modelcontextprotocol/server-github
```

**Use cases:**
- "Create a PR for the campaign budget fix"
- "What's the status of our CI checks?"
- "List all open issues tagged as bugs"

---

## Priority 2: Install This Week (Operational Improvement)

### 4. Stripe MCP — Payment Analysis

**Why:** We have Stripe Express payouts, campaign deposits, and soon 6-figure sponsor payments. Direct Stripe access lets Claude audit transactions, debug failed payouts, and verify deposit flows.

**Impact:** Critical for the campaign money flow fixes. Can verify payout status, check Express account health, and audit transaction history.

```bash
claude mcp add --scope local --transport stdio \
  --env STRIPE_API_KEY="sk_live_your_key" \
  stripe -- npx stripe-mcp-server
```

**Use cases:**
- "Show me all failed payouts in the last 30 days"
- "What's the Stripe balance for Nerava's platform account?"
- "List all Express accounts and their onboarding status"
- "Check if the campaign deposit payment intent succeeded"

---

### 5. AWS MCP — Infrastructure Management

**Why:** We deploy via App Runner, ECR, S3, CloudFront, and RDS. Currently using raw `aws` CLI commands. The AWS MCP gives Claude structured access to infrastructure state.

**Impact:** Faster deployments, better log analysis, easier infrastructure debugging.

```bash
claude mcp add --scope local --transport stdio \
  aws -- npx @anthropic-ai/aws-mcp-server
```

**Use cases:**
- "What's the current App Runner deployment status?"
- "Show me CloudWatch errors from the last hour"
- "List all ECR images for nerava-backend"
- "What's our current RDS storage usage?"

---

### 6. Playwright MCP — Browser Testing

**Why:** Google Play kept rejecting us for "unresponsive UI". With Playwright MCP, Claude can launch a browser, load the app, and test UI flows automatically before deploying.

**Impact:** Catch UI bugs before they reach Google Play review.

```bash
claude mcp add --scope project --transport stdio \
  playwright -- npx @playwright/mcp
```

**Use cases:**
- "Open the driver app, deny location, and screenshot what happens"
- "Test the claim flow end-to-end and report any errors"
- "Take a screenshot of the admin portal dashboard"

---

## Priority 3: Install This Month (Quality of Life)

### 7. Slack MCP — Team Communication

**Why:** If the team grows, Claude can post deployment notifications, error alerts, and status updates to Slack channels.

```bash
claude mcp add --scope local --transport stdio \
  --env SLACK_BOT_TOKEN="xoxb_your_token" \
  slack -- npx @modelcontextprotocol/server-slack
```

---

### 8. Notion MCP — Documentation

**Why:** If you use Notion for product specs, meeting notes, or roadmaps, Claude can read and update them directly.

```bash
claude mcp add --transport http notion https://mcp.notion.com/mcp \
  --header "Authorization: Bearer ntn_your_token"
```

---

## Configuration Summary

### Quick Install Script (Run Once)

```bash
# 1. PostgreSQL (read-only production access)
claude mcp add --scope local --transport stdio \
  --env DATABASE_URL="postgresql://readonly:pass@nerava-db.xxx.rds.amazonaws.com:5432/nerava" \
  postgres -- npx @modelcontextprotocol/server-postgres

# 2. Sentry (error monitoring)
claude mcp add --scope local --transport stdio \
  --env SENTRY_AUTH_TOKEN="your_token" \
  sentry -- npx @sentry/mcp-server

# 3. GitHub (code management)
claude mcp add --scope project --transport stdio \
  --env GITHUB_TOKEN="ghp_your_token" \
  github -- npx @modelcontextprotocol/server-github

# 4. Stripe (payment analysis)
claude mcp add --scope local --transport stdio \
  --env STRIPE_API_KEY="sk_live_your_key" \
  stripe -- npx stripe-mcp-server

# Verify all are connected:
claude mcp list
```

---

## Security Notes

1. **PostgreSQL:** Always use a **read-only** database user. Never give Claude write access to production via MCP.
2. **Stripe:** Use a **restricted API key** with only the permissions needed (read payouts, read transactions). Don't use the full secret key.
3. **GitHub:** Use a **fine-grained personal access token** scoped to only the Nerava repo.
4. **Scope:** Use `--scope local` for anything with secrets (not committed to repo). Use `--scope project` only for tools that don't need secrets.
5. **Secrets in config:** Use `${ENV_VAR}` references in `.mcp.json`, never hardcode secrets.

---

## Expected Impact

| MCP | Problem It Solves | Time Saved Per Week |
|-----|------------------|-------------------|
| PostgreSQL | Can't query production DB directly | 2-3 hours |
| Sentry | Manual CloudWatch log grep for errors | 1-2 hours |
| GitHub | Manual git/gh commands for PRs | 30 min |
| Stripe | Manual Stripe dashboard checks | 1 hour |
| AWS | Manual AWS CLI for deployments/logs | 1-2 hours |
| Playwright | Manual testing before Play Store submission | 2-3 hours |

**Total estimated savings: 8-12 hours per week** of context-switching and manual tooling.
