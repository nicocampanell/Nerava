# Nerava Intelligence — Product Gap Report

**Date:** April 6, 2026
**Product:** On-demand charger location intelligence subscriptions ($200/location/month)
**Core thesis:** Property owners, REITs, charging operators, and fleet managers pay for real-time and historical EV charger utilization data. TomTom polling is already running. The report you generated today IS the product. This doc covers what's needed to make it sellable.

---

## What Already Exists (Surprisingly Solid Foundation)

| Component | Status | Location |
|-----------|--------|----------|
| TomTom availability collector | **Live, running 24/7** | `workers/availability_collector.py` — 25 stations, 15-min polling |
| charger_availability_snapshots table | **16,784+ rows** | Model: `models/charger_availability.py`, indexed on (charger_id, recorded_at) |
| Admin API for availability data | **Live** | `GET /v1/admin/analytics/availability/{charger_id}` (1-168hr window) |
| Weekly merchant reports | **Live** | `workers/weekly_merchant_report.py` — HTML email every Monday 8am CT |
| Merchant analytics service | **Live** | `services/merchant_reports.py` — sessions, dwell time, peak hours, walk traffic |
| Stripe subscription billing | **Live** | `models/merchant_subscription.py` — Pro plan with Stripe webhooks |
| Field alert threshold | **Live (logs only)** | 60% occupancy alert during business hours, deduplicated |
| K-anonymity protection | **Live** | Min 5 unique drivers to publish metrics |
| Charger grid seeding | **Built** | `scripts/seed_chargers_grid.py` — NREL API, covers 100 US metros |

**Bottom line:** You have the data pipeline, the storage, the analytics engine, and the billing integration. What's missing is the customer-facing packaging: PDF reports, self-serve location setup, a subscriber portal, and on-demand report generation.

---

## Gap 1: PDF Report Generation (P0)

**Current:** Reports are HTML emails only. The charger activity report I built today is a standalone HTML file you screenshot or print-to-PDF manually.

**Required:** Automated PDF generation from a report template — branded, paginated, downloadable, and email-attachable.

### Implementation Options

| Option | Library | Effort | Quality |
|--------|---------|--------|---------|
| **WeasyPrint** (recommended) | Python, renders HTML/CSS to PDF | 2-3 days | High — supports CSS Grid, Flexbox, custom fonts |
| Puppeteer/Playwright | Headless Chrome, screenshot-to-PDF | 2 days | Highest fidelity but heavier dependency |
| ReportLab | Python, programmatic PDF construction | 4-5 days | Most control but slowest to build |
| html-pdf-node | Node.js wrapper around Puppeteer | 2 days | Good if you want Node in the pipeline |

### Recommended: WeasyPrint

```
pip install weasyprint
```

**Build:**
- Jinja2 HTML template (one template per report type)
- CSS stylesheet matching the Nerava Intelligence brand (navy/teal/white from the report I built)
- `ReportService.generate_pdf(charger_ids, date_range, subscriber)` → returns PDF bytes
- Store in S3, return signed URL (1-hour TTL)
- Attach to weekly email as PDF, or serve on-demand via API

**Endpoints:**
- `GET /v1/intelligence/reports/{location_id}/latest.pdf` — latest weekly report
- `POST /v1/intelligence/reports/generate` — on-demand report for custom date range
- `GET /v1/intelligence/reports/history` — list past reports with download links

**Effort:** 3-4 days | **Priority:** P0

---

## Gap 2: Subscriber Self-Serve Location Setup (P0)

**Current:** Monitored locations are hardcoded in `MONITORED_STATIONS` in `availability_collector.py`. Adding a new location requires a code commit and deployment.

**Required:** When a subscriber pays, their charger locations automatically start being monitored.

### Implementation

**Step 1: Admin/API endpoint to add monitored locations**
- `POST /v1/intelligence/locations` — subscriber adds a charger to monitoring
- Accepts: charger_id (from NREL), or lat/lng + radius to auto-discover nearby chargers
- Resolves TomTom availability ID via reverse geocode or NREL → TomTom mapping
- Creates `intelligence_subscriptions` record linking subscriber → locations → billing

**Step 2: Dynamic collector**
- Refactor `availability_collector.py` to query `intelligence_subscriptions` table instead of hardcoded list
- Poll all active subscription locations + any global monitoring locations
- Auto-scale polling batch size based on subscriber count vs. free tier budget

**Step 3: TomTom ID resolution**
- Not all chargers have TomTom availability IDs
- Build a lookup: NREL ID → TomTom availability ID (via lat/lng proximity match)
- Cache the mapping; fallback to "data unavailable" if no TomTom coverage

**New tables:**
```sql
intelligence_subscriptions
├── id (UUID)
├── subscriber_id (FK to users or a new subscriber entity)
├── subscriber_type ('property_owner', 'operator', 'fleet', 'reit')
├── stripe_subscription_id
├── status ('active', 'canceled', 'trial')
├── created_at, updated_at

intelligence_locations
├── id (UUID)
├── subscription_id (FK)
├── charger_id (FK to chargers)
├── tomtom_availability_id
├── monitoring_started_at
├── monitoring_status ('active', 'paused', 'no_coverage')
```

**Effort:** 5-7 days | **Priority:** P0

---

## Gap 3: Subscriber Portal / Dashboard (P1)

**Current:** No dedicated UI for intelligence subscribers. Admin can query JSON endpoints.

**Required:** Web dashboard at `intelligence.nerava.network` (or a section within the existing console app) where subscribers can:

- View all their monitored locations on a map
- See real-time availability per station
- View historical utilization charts (hourly, daily, weekly)
- Download PDF reports
- Configure alert thresholds
- Manage billing / add locations

### Build Options

| Option | Effort | Notes |
|--------|--------|-------|
| **New section in apps/console** | 5-7 days | Reuse Radix UI + Recharts, add "Intelligence" nav section |
| Standalone app | 10-14 days | Separate deployment, more flexibility |
| Embedded in merchant portal | 3-5 days | Fastest but mixes merchant and property owner UX |

**Recommended:** Add an "Intelligence" section to `apps/console` with a new role (`intelligence_subscriber`). Reuses the existing React + Radix + Recharts stack. Deploy to `console.nerava.network/intelligence`.

**Key views:**
1. **Dashboard** — Map of locations + summary cards (utilization, sessions, alerts)
2. **Location Detail** — Hourly bar chart, daily trend, week-over-week comparison
3. **Reports** — Download history, generate on-demand
4. **Alerts** — Configure thresholds, view alert history
5. **Billing** — Current plan, usage, invoices

**Effort:** 7-10 days | **Priority:** P1

---

## Gap 4: Historical Trend Analysis (P1)

**Current:** Raw 15-minute snapshots stored. No aggregation, no trends, no forecasting.

**Required:** Pre-computed analytics that turn raw snapshots into sellable insights.

### Analytics to Build

| Metric | Query | Compute |
|--------|-------|---------|
| **Daily utilization %** | AVG(occupied/total) per day | Nightly batch job |
| **Peak hours** | Hour with max avg occupancy | Per-location, per-week |
| **Week-over-week trend** | Compare current week vs prior | % change in avg utilization |
| **Busiest day of week** | DOW with highest avg occupancy | Rolling 4-week window |
| **Estimated daily sessions** | SUM(occupied port transitions) | Heuristic: occupied→available = 1 session |
| **Average dwell time** | Duration of continuous occupancy | Requires consecutive snapshot analysis |
| **Capacity forecast** | Linear projection of utilization growth | 4-week trend extrapolation |
| **Out-of-service rate** | AVG(out_of_service/total) | Reliability metric for operators |

### Implementation

- `IntelligenceAnalyticsService` with methods for each metric
- Nightly batch job aggregates daily/weekly rollups into `intelligence_daily_stats` table
- API endpoints return pre-computed stats (fast) with optional raw drill-down (slower)

**Effort:** 4-5 days | **Priority:** P1

---

## Gap 5: Configurable Alerts (P1)

**Current:** Field alert at 60% occupancy logged to application logs only. No subscriber notifications.

**Required:** Subscriber-configurable alerts delivered via email, push, or webhook.

### Alert Types

| Alert | Trigger | Use Case |
|-------|---------|----------|
| **High utilization** | Occupancy > X% for > Y minutes | "Chargers are full, drivers may leave" |
| **Low utilization** | Occupancy < X% during peak hours | "Your infrastructure is underused" |
| **Out of service** | OOS ports > 0 for > Z hours | "Charger needs maintenance" |
| **Utilization milestone** | Weekly avg crosses threshold | "Usage up 20% this month" |

### Implementation

```sql
intelligence_alert_rules
├── id, subscription_id, location_id
├── alert_type ('high_util', 'low_util', 'oos', 'milestone')
├── threshold_value (float)
├── duration_minutes (int)
├── delivery_method ('email', 'webhook', 'push')
├── is_active (bool)

intelligence_alert_history
├── id, rule_id, location_id
├── triggered_at, resolved_at
├── snapshot_value (what triggered it)
├── delivery_status ('sent', 'failed')
```

- Evaluate rules after each polling cycle in the availability collector
- Deduplication: don't re-alert for same condition within cooldown period
- Webhook delivery reuses existing `webhook_delivery_service.py`

**Effort:** 3-4 days | **Priority:** P1

---

## Gap 6: Data Export (P1)

**Current:** JSON endpoint for admin only. No CSV, no bulk download.

**Required:** Subscribers can export raw data for their locations.

- `GET /v1/intelligence/export/{location_id}?format=csv&from=2026-03-30&to=2026-04-06`
- Returns CSV with columns: timestamp, total_ports, available, occupied, out_of_service
- S3-backed for large exports (pre-generate, return signed URL)
- Rate-limited to prevent abuse

**Effort:** 2 days | **Priority:** P1

---

## Gap 7: On-Demand Location Activation (P0)

**Current:** To monitor a new location, you edit Python code and redeploy.

**Required:** "Subscriber pays → locations activate automatically" flow.

### Flow

1. Subscriber signs up at `intelligence.nerava.network`
2. Enters charger addresses or selects from NREL map
3. System resolves TomTom availability IDs
4. Stripe checkout ($200/location/month)
5. On payment confirmation → `intelligence_locations` record created
6. Next polling cycle picks up new location automatically
7. First report available within 24 hours (needs 96 data points minimum)

**The key technical piece:** Refactor `availability_collector.py` to load monitored stations from DB instead of hardcoded list. Keep the hardcoded list as fallback/global monitoring. This is ~2 hours of work.

**Effort:** Included in Gap 2 estimate | **Priority:** P0

---

## Gap 8: White-Label Reports (P2)

**Current:** Hardcoded "Nerava Intelligence" branding.

**Required for enterprise/operator deals:** Ability to generate reports with customer's logo, colors, and domain.

- `intelligence_subscriptions.branding_config` JSON column: `{logo_url, primary_color, company_name}`
- Jinja2 template reads branding config, applies to header/footer
- PDF filename: `{company_name}_charger_report_{date}.pdf`

**Effort:** 2 days | **Priority:** P2

---

## Gap 9: Multi-Source Data Enrichment (P2)

**Current:** TomTom only. Single data source.

**Required for premium tier:** Layer additional data sources to increase report value.

| Source | Data | Cost | Effort |
|--------|------|------|--------|
| **Google Places** | Nearby merchant density, ratings, foot traffic proxy | Already have API key | 2 days |
| **Census/ACS** | Demographics around charger (income, EV ownership proxy) | Free (census.gov API) | 2 days |
| **NREL** | Charger specs, network, connector types | Already seeded | 1 day |
| **Weather** | Correlation with utilization | Free (OpenWeather) | 1 day |
| **Utility rates** | Time-of-use pricing impact on charging patterns | Manual research | 3 days |

This turns a "$200/month utilization dashboard" into a "$500/month location intelligence platform" — richer data, higher price point, harder to replicate.

**Effort:** 5-8 days total | **Priority:** P2

---

## Gap 10: Free Tier Ceiling Management (P1)

**Current:** 2,500 free TomTom calls/day covers ~26 stations. You're at 25.

**Required:** Strategy for scaling past 26 stations without blowing up costs.

### Options

| Strategy | Max Stations | Monthly Cost |
|----------|-------------|-------------|
| Current (free tier, 15-min) | 26 | $0 |
| Paid tier, 15-min polling | Unlimited | $7.20/station/month |
| Free tier, 30-min polling | 52 | $0 |
| Hybrid: 15-min for paid subscribers, 30-min for free | 52 paid + 52 free | $0 until 52 paid |
| Batch + cache: poll each station hourly, interpolate | 260 | $0 |

**Recommended:** Hybrid approach. Paid subscribers get 15-min resolution. Global monitoring runs at 30-min. Switch to paid TomTom tier when subscription revenue exceeds $7.20/station threshold (at $200/month revenue, this is 3.6% COGS — trivial).

**Effort:** 1 day (config change) | **Priority:** P1

---

## Prioritized Build Sequence

### P0 — Required to Sell the First Subscription

| # | Item | Effort | Owner |
|---|------|--------|-------|
| 1 | Refactor collector to load locations from DB | 0.5 days | Engineer |
| 2 | Build `intelligence_subscriptions` + `intelligence_locations` tables | 1 day | Engineer |
| 3 | Build subscriber signup + Stripe checkout flow | 2 days | Engineer |
| 4 | Add WeasyPrint PDF generation + Jinja2 report template | 3 days | Engineer |
| 5 | Build API endpoints: generate report, download PDF, list locations | 2 days | Engineer |
| 6 | TomTom ID resolution service (NREL → TomTom mapping) | 1 day | Engineer |

**P0 total: ~10 days to a sellable product**

### P1 — Required Before 10th Subscriber

| # | Item | Effort |
|---|------|--------|
| 7 | Subscriber portal (Intelligence section in console app) | 7-10 days |
| 8 | Historical trend analytics + nightly batch job | 4-5 days |
| 9 | Configurable alerts (email/webhook) | 3-4 days |
| 10 | CSV data export | 2 days |
| 11 | Free tier ceiling management (hybrid polling) | 1 day |

**P1 total: ~18-22 days**

### P2 — Required Before Enterprise/Operator Deals

| # | Item | Effort |
|---|------|--------|
| 12 | White-label report branding | 2 days |
| 13 | Multi-source data enrichment | 5-8 days |
| 14 | Capacity forecasting / predictive analytics | 3-4 days |

**P2 total: ~10-14 days**

---

## Revenue Model

| Tier | Price | Includes |
|------|-------|----------|
| **Basic** | $200/location/month | Weekly PDF report, real-time dashboard, 15-min data, email alerts |
| **Pro** | $500/location/month | All Basic + CSV export, custom alerts, API access, trend analytics |
| **Enterprise** | Custom | All Pro + white-label, SLA, dedicated support, multi-source enrichment |

At 10 locations on Basic: **$2,000/month recurring** with ~$0/month in TomTom costs (free tier).
At 100 locations on Basic: **$20,000/month** with ~$720/month TomTom costs (96.4% gross margin).

---

## What You Can Sell Tomorrow With What Exists Today

You don't need to build any of this to close the first deal. Here's what works right now:

1. **The HTML report I built today** — save as PDF, email to prospect
2. **"We'll add your locations to our monitoring network"** — you manually add their charger IDs to the collector (10-minute task)
3. **Weekly email reports** — already running via `weekly_merchant_report.py`
4. **Invoice via Stripe** — create a manual subscription

The P0 engineering work makes this self-serve and scalable. But the first 3-5 subscribers can be onboarded manually while you build the automation in parallel.

**Start selling first. Build the product around the first paying customers.**
