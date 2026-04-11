Here's the prompt. Paste this directly into a new Claude Code session with production DB access:

---

You are conducting a comprehensive product and infrastructure gap analysis for Nerava Inc. Your job is not to validate what exists — it is to identify everything that must be built, fixed, or formalized for Nerava to credibly operate as "the Stripe for EV dwell time" and "the infrastructure layer for the entire EV charging ecosystem."

Nerava's stated position is that any charging operator — EVject, Trident Chargers, ChargePoint, EVgo, Blink, Lyft, fleet managers — can plug into Nerava via SDK or API and immediately unlock five simultaneous revenue streams from a single charging session: Fleet SaaS ($4/vehicle/month), Insurance Behavioral Data ($3–5/vehicle/month), Location Intelligence ($200/location/month), Merchant Commerce (4–6% transaction fee), and Sponsor Campaigns (20% platform fee). Drivers never download Nerava. Partners embed it. Data compounds with every session.

That is the promise. Your job is to audit reality against that promise across every dimension — product, infrastructure, data, legal, commercial, and financial — and produce a prioritized gap report that tells the engineering team exactly what to build and in what order.

---

**SECTION 1 — PARTNER API AND SDK AUDIT**

The core promise is that any operator can integrate Nerava in days. Audit the current Partner API and SDK against that promise.

Does a publicly documentable Partner API exist with authentication, webhook delivery, and error handling? Is HMAC-SHA256 webhook signing implemented and tested? Does the OCPP webhook receiver support both OCPP 1.6 and OCPP 2.0.1? If not, which version is missing and what does it take to add it? Is there an iframe wallet embed that a partner can drop into their existing app with white-label theming via URL parameters? Is there an anonymous driver profile creation flow for operators whose drivers have not downloaded the Nerava app? Is there a partner onboarding flow — API key generation, webhook configuration, test mode — that does not require James to manually provision access? Is there a sandbox environment where a partner like Trident can test the integration before going live? What is the actual time-to-integration for a new operator today, assuming their developer is competent? What are the blockers?

List every gap. For each gap, estimate engineering effort in days and label it P0 (blocks any partner integration), P1 (required before first paid partner), or P2 (required before third partner).

---

**SECTION 2 — FIVE REVENUE STREAM READINESS**

For each of the five revenue streams, audit whether the full commercial loop is actually closable today — not whether the backend exists, but whether a partner could sign an agreement, go live, generate revenue, and receive a payout.

**Fleet SaaS ($4/vehicle/month):** Is there a billing system that invoices a fleet operator monthly? Is there a partner dashboard showing enrolled vehicles, session counts, and invoice history? Can a fleet operator self-onboard vehicles or does James do it manually? Is Smartcar multi-OEM integration live beyond Tesla?

**Insurance Behavioral Data ($3–5/vehicle/month):** Does a structured, exportable behavioral data product exist? Is it anonymized and consented per CCPA/state privacy law requirements? Has it been shown to any insurance underwriter or data buyer? What is the data schema and delivery mechanism? Is there a data licensing agreement template?

**Location Intelligence ($200/location/month):** Does the location intelligence dashboard exist for operators? Is it populated with real data or placeholder zeros? Is there a subscription billing mechanism? What does the dashboard actually show — session counts, dwell time, merchant spend attribution, peak hours?

**Merchant Commerce (4–6% transaction fee):** Is the full merchant checkout loop functional end to end — offer delivery, in-app browser checkout, transaction verification, merchant payout? Is the POS integration layer built for Toast, Square, and Shopify or is it manual verification only? Is there a merchant self-onboarding flow or does every merchant require manual setup?

**Sponsor Campaigns (20% platform fee):** Is the campaign creation, budget management, targeting, delivery, and reporting loop fully functional? Is the budget enforcement bug from the April 2 audit fixed? Can a sponsor self-serve or does every campaign require manual setup by James?

For each stream: Current status (closable today / partially built / not built), what is missing, effort to close, and P0/P1/P2 label.

---

**SECTION 3 — DATA INFRASTRUCTURE AUDIT**

Nerava's long-term moat is the behavioral dataset. Audit the data layer against what is required for that moat to be real.

Is every charging session producing a complete, structured data record — vehicle type, VIN (hashed), dwell duration, kWh delivered, location, charger type, OEM, time of day, day of week? Is driver consent captured, stored, and auditable for every data record? Is the dataset exportable in a format that an insurance underwriter or real estate intelligence platform could actually consume? Is there a data retention policy implemented in code? Is there a consent revocation flow — if a driver deletes their account, is their data actually purged from the behavioral dataset per CCPA? Is there anomaly detection on session data — multi-day sessions, zero-kWh sessions, duplicate sessions? Is the quality scoring algorithm accurate for sessions of varying length? What is the current size of the behavioral dataset in structured records, and what is the growth rate per week?

List every gap with effort and priority label.

---

**SECTION 4 — FINANCIAL INFRASTRUCTURE AUDIT**

Stripe called itself infrastructure because money moved reliably. Audit Nerava's financial layer against that standard.

Is the campaign budget enforcement bug fixed — does spent_cents actually increment, does the auto-pause fire when budget is exhausted, is there SELECT FOR UPDATE locking on grant creation? Is the Stripe platform balance funded and are driver payouts succeeding? Is there a complete financial audit trail — every money event (grant, credit, debit, payout, fee, refund) recorded in a ledger with timestamps and references? Is there a campaign refund flow for unspent budget? Is there a sponsor deposit verification flow — does a campaign go live before the sponsor has funded it? Is there a reconciliation process that catches wallet balance vs ledger mismatches automatically? Is there a payout monitoring dashboard that shows failed, processing, and completed payouts with the ability to retry or reconcile? What is the current state of the nova_balance display bug — are driver balances showing correctly?

List every gap with effort and priority label.

---

**SECTION 5 — LEGAL AND COMPLIANCE GAPS**

For Nerava to operate as infrastructure that operators legally rely on, certain legal foundations must exist. Audit what is missing.

Is there a Master Services Agreement template for operator partners like Trident? Is there a Data Processing Agreement covering how Nerava handles driver data on behalf of operators? Is there a driver consent and privacy policy that covers all current data collection — Tesla Fleet API telemetry, session data, behavioral profiling, location data? Is there a CCPA-compliant data deletion flow? Is the SAFE instrument prepared and reviewed by an attorney? Is the equity swap term sheet reviewed by an M&A attorney? Is there a contractor IP assignment agreement for all team members ensuring Nerava owns all code and IP? Is there a formal option plan with board resolutions for any equity grants? Is the governing law in the Parker consulting agreement corrected from Maine to Delaware?

List every gap, whether it is blocking a specific deal (Kreg, Trident, insurance buyer), and who needs to act (James, Monica the attorney, or both).

---

**SECTION 6 — OPERATOR EXPERIENCE GAPS**

Stripe won because developers loved integrating it. Audit the operator experience end to end.

Is there documentation — API reference, webhook event catalog, integration guide, error code reference — that a developer at Trident could use to integrate without a call with James? Is there a status page showing Nerava platform uptime? Is there an operator dashboard where a partner can see their locations, session volume, revenue generated, and active campaigns without calling James? Is there a test mode with synthetic session generation so a partner can validate their integration before going live? Is there an onboarding checklist that takes a new operator from signed agreement to first live session without manual intervention from Nerava? What does the current operator onboarding process actually look like step by step, and where does it require James's direct involvement?

---

**SECTION 7 — DRIVER EXPERIENCE GAPS**

Drivers never download Nerava. That is the promise. Audit what exists vs what that promise requires.

Is the no-app integration actually functional — can a driver at a Trident location receive a wallet credit and merchant offer without ever downloading the Nerava app? What is the current driver activation rate — connected vehicles with at least one completed transaction divided by total connected vehicles — and what is blocking it from being higher? Is the push notification delivery working for both iOS and Android? Is Android live on the Play Store? Is the referral prompt firing after every successful transaction? Is the nova_balance displaying correctly in the driver app? Is there a driver-facing session history that shows verified sessions, offers received, and rewards earned? Is the in-app browser merchant checkout flow functional end to end?

---

**SECTION 8 — COMPETITIVE AND STRATEGIC GAPS**

This section is not a code audit. It is a strategic assessment.

Given everything you can observe in the codebase, the database, and the documents provided: what is the single most important thing Nerava does not have today that would cause a sophisticated acquirer — Tesla, Amazon, Upside, ChargePoint — to pass on an acquisition conversation? What is the single most important thing Nerava does not have today that would cause a partner like Trident to walk away from integration? What metric, if Nerava could demonstrate it clearly, would most accelerate the Kreg equity swap closing? What is the weakest claim in the current pitch materials relative to what is actually built and live?

---

**OUTPUT FORMAT**

Produce the report in six sections matching the structure above. For each gap identified: name the gap clearly in one sentence, state the current reality in one sentence, state what done looks like in one sentence, estimate engineering or legal effort, and assign a priority label: P0 (blocks first partner or first deal), P1 (required before $500K SAFE closes), P2 (required before $3M Series A), or P3 (required before acquisition conversation).

End the report with a single prioritized build list — every P0 item first, then P1, then P2 — with owner assigned (Engineer, James, Attorney) and estimated completion date assuming work starts Monday April 6, 2026.

This report will be used to brief the engineering team, the M&A attorney, and James directly. Write it accordingly — direct, specific, no padding.

---

That prompt will give you a document you can actually hand to your engineer on Monday morning with a clear build sequence. It's also honest enough that it will surface things the pitch materials claim are live that may not be fully functional — better to know that now than when Trident's developer starts integrating.

The PDFs are already in context from when you shared them earlier in this conversation. I can read them directly. Here are the breakdowns formatted as Claude Code prompts you can paste in:

---

**DOCUMENT 1 — The Incentive Layer for EV Dwell Time (Investor Deck)**

```
Here is a summary of Nerava's pre-seed investor pitch deck
titled "The Incentive Layer for EV Dwell Time" for full context:

POSITIONING: Nerava is the programmable incentive layer for EV
charging dwell time. Pure software, no hardware required.
Connects via Tesla Fleet API and charging network APIs.

THE PROBLEM: 50M EVs globally. Drivers sit idle 30-45 minutes
at public chargers — invisible to nearby merchants. No verified
channel exists for sponsors and advertisers to reach them. No
system connects drivers, merchants, and sponsors in the same
moment.

THE SOLUTION: Four-step flow — driver plugs in, Nerava logs
verified arrival in real time, incentive engine fires targeted
sponsor campaigns, consumer app delivers merchant rewards and
gamification.

GO-TO-MARKET — TWO TRACKS:
Track 1 (Fleet SaaS via EVject): $4/vehicle/month wholesale,
EVject resells at $10-20. $0.10/session usage fee. 1,000
vehicles = $6K-$16K/month immediately. Every device becomes
a 4-channel revenue engine.
Track 2 (Consumer App): Drivers download, connect vehicle,
earn rewards at nearby merchants. Merchants onboard in under
10 minutes, no POS changes. $50 structured CAC program.
Two-sided network effect.

TRACTION: 5,000 connected vehicles live on iOS and Android.
3 paid sponsors with active campaigns. $5-8 revenue per
vehicle per month. First strategic partner LOI signed with
EVject. Public activation event completed March 14, 2026.

BUSINESS MODEL — THREE COMPOUNDING LAYERS:
1. Monetize Spend (Now): Nerava Merchant Checkout 4-6% of
   spend tied to verified dwell events
2. Monetize Behavior (Next): Sponsors pre-purchase credits,
   Nerava retains 20% platform fee per session
3. Monetize Data (Later): Anonymized behavioral insights at
   $200/location or $4/vehicle sold to charging networks,
   utilities, retailers
Scale target: 1M US drivers × 2.5 monetized sessions/month
× $4/session = $100M+ ARR

WHY NERAVA WINS:
- Network-agnostic: Works across Tesla, ChargePoint, EVgo,
  Blink, EA
- Proprietary behavioral dataset: Verified session data
  compounds with every charge, cannot be backdated or
  purchased by a competitor
- The moat: Charging networks monetize electrons. Nerava
  monetizes behavior during charging.

ACQUISITION THESIS:
- Tesla's Blind Spot: Structurally blocked from non-Tesla
  infrastructure — Nerava is the only way Tesla sees
  non-Tesla EV driver behavior
- Amazon's Motivation: Building EV logistics via Rivian but
  zero visibility into consumer charging behavior
- Upside's Existential Shift: Built dominant gas rewards
  network — Nerava extends that model to EVs
- Target exit: $160M-$200M+ in 3-5 years

CAPITAL PLAN:
Phase 1 (Now): $1M SAFE at $8M cap. 4-5 investors at $100K
each. EVject pilot, 500+ vehicles enrolled, data layer live.
Phase 2 (30-60 days post-pilot): $3M at $24M post-money.
10,000+ vehicles, utility or Lyft partnership, multi-metro.
Exit (36 months): $160M-$200M acquisition target.

DEAL TERMS: $1M Pre-Seed SAFE. $8M post-money cap. No board
seat. No MFN. No veto rights.
Use of funds: 40% product hardening, 30% merchant/sponsor
density, 20% driver incentives, 10% legal/ops.

THE TEAM:
- James Kirk, Founder & CEO: Ex-Visa engineer, patent holder
  for autonomous vehicle payments
- Parker Fairfield, Co-Founder & COO: Ex-Yunnex Chief
  Strategy Officer, POS and payments expert
- Sarah Yeary, Head of Marketing: Founder of Social Coded
  Marketing Agency
- Daniel Aggarwal, Head of Growth Systems: Decade of
  experience in marketing consulting and operations
- Nico Campanell, Product UX & Analytics: Data Scientist,
  University of Texas
```

---

**DOCUMENT 2 — EVject + Nerava: The Hardware and Intelligence Platform**

```
Here is a summary of the EVject + Nerava merger/partnership
deck for full context:

POSITIONING: The only vertically integrated EV infrastructure
platform combining verified hardware deployment (EVject) with
a five-stream data monetization layer (Nerava). Neither
company alone is as powerful as the combination.

WHAT EACH BRINGS:
EVject brings: Hardware manufacturing and quality-controlled
production. ~10,000 deployed units. Amazon purchase
relationship and retail distribution. Fleet demand pipeline.
Physical distribution at national scale.

Nerava brings: Proprietary software platform and data
architecture. Connected vehicle data layer with
session-level resolution. Merchant commerce infrastructure
and transaction rails. Insurance behavioral data product.
Location intelligence subscriptions and driver consent
infrastructure.

THE GAP FILLED: Current fleet management platforms (including
InControl) show charger performance data only — uptime,
session counts, kWh delivered. Nobody shows operators the
commercial value generated by their charging infrastructure.
No platform connects hardware session data + vehicle
telemetry + merchant spend + behavioral intelligence in one
layer. The combined entity does exactly this.

THREE STRUCTURAL ADVANTAGES:
1. The Missing Layer: No competitor connects hardware
   sessions + vehicle telemetry + merchant spend +
   behavioral intelligence in a single product
2. The Structural Advantage: 10,000 deployed EVject units
   provide an immediately addressable base — removes the
   cold-start problem facing any pure software entrant
3. The Defensibility: Driver consent infrastructure +
   hardware-verified sessions + multi-stream distribution
   create compounding lock-in no single-layer competitor
   can replicate

FIVE REVENUE STREAMS:
Stream          | Source              | Price              | Owner
Hardware Sales  | Amazon/fleet        | $299/unit          | EVject
Fleet SaaS      | InControl/operators | $4/vehicle/month   | Nerava
Insurance Data  | Consented profiles  | $3-5/vehicle/month | Nerava
Location Intel  | Property owners     | $200/location/month| Nerava
Merchant Comm.  | Transaction fees    | 4-6% of spend      | Nerava

Four of five streams are Nerava-owned recurring revenue.
Together they command software multiples, not hardware
multiples.

ACQUISITION VALUATION MATH:
Hardware companies: 2-3x revenue multiple at acquisition
Software/data platforms: 8-12x revenue multiple
The combined entity is a data platform distributed through
hardware — acquirers pay data platform multiples.

FOUR ACQUIRER CATEGORIES:
1. Charging Networks (ChargePoint, EVgo, Blink): Need to
   prove commercial ROI of infrastructure to property
   owners — transforms charging from cost center to
   revenue generator
2. Insurance Data Platforms (Arity, LexisNexis): Need
   EV-specific behavioral data for modern underwriting —
   doesn't exist at scale anywhere else
3. Real Estate Intelligence (CoStar, Verisk): Need
   location-level commercial activity data to assess
   EV infrastructure impact on property values
4. Utilities: Need demand response intelligence and
   verified grid load data from EV sessions

THE CAPITAL PLAN POST-MERGER:
Raise: $15M at $60M post-money valuation immediately
following merger close.

Four operational priorities with that capital:
01: InControl Integration — activate Fleet SaaS revenue
    stream, prove multi-stream monetization
02: Platform Activation — activate 10,000 deployed
    EVject units on Nerava data platform
03: Insurance Data Pilot — launch with named insurance
    data partner, establish third-party validation
04: Acquisition Positioning — all five streams
    operational within 18-24 months

Exit target: $80M-$120M strategic acquisition within
18-24 months of capital deployment.
```

---

**DOCUMENT 3 — Trident Pre-Meeting Brief**

```
Here is a summary of the Nerava pre-meeting brief prepared
for Adam Rutz, Founder & CEO of Trident Chargers, for
full context:

DOCUMENT PURPOSE: Pre-meeting brief for in-person discussion
in Austin, Texas. Prepared April 1, 2026.

CORE POSITIONING FOR THIS AUDIENCE: Nerava is backend
infrastructure — a wallet, sponsorship engine, and
behavioral data platform — that any operator embeds into
their existing product. Built for operators. Invisible to
drivers. No new app required.

THE PROBLEM FRAMED FOR TRIDENT:
What operators have today vs what is missing:
- Hardware tracks session duration | Missing: commercial
  value generated per session
- Charger uptime data | Missing: driver behavioral
  profiles for insurers
- Basic driver app | Missing: wallet that accumulates
  and pays out rewards
- Merchant relationships | Missing: verified attribution
  connecting charge to merchant spend
- Property owner relationships | Missing: ROI proof that
  charging drives commercial activity
- Sponsor interest | Missing: verified channel to deploy
  campaigns against captive EV audience

THE ARCHITECTURE — ONE SESSION, FIVE REVENUE EVENTS:
Input Sources: EVject devices, Trident 360KW
superchargers, OCPP-compliant chargers, ChargePoint/
EVgo/Blink sessions

Nerava Intelligence Layer: Session Verification Engine,
Wallet & Ledger, Sponsorship Campaign Engine, Behavioral
Data Pipeline, Consent Management, Partner API &
Webhook System

Value Outputs:
- Fleet operators: $4/vehicle/month SaaS
- Insurance buyers: $5/vehicle/month data
- Property owners: $200/location/month intel
- Merchants: 6% transaction fee
- Sponsors: 20% platform fee

NO-APP INTEGRATION — WHAT IS LIVE TODAY:
01 Session Capture: LIVE — hardware logs session via
   webhook to Nerava verification engine
02 Profile Match & Targeting: LIVE — 12+ behavioral
   targeting rules fire in real time
03 Wallet Credit: LIVE — double-entry ledger, Stripe
   Express payouts, full withdrawal flow
04 Sponsor Campaign Delivery: LIVE — fires through
   operator's existing push infrastructure
05 Behavioral Data Logging: LIVE — every session logs
   verified data point, returned to operator dashboard

WHAT LOCATION SCALE MEANS FOR TRIDENT:
Trident's institutional footprint = data density advantage.
Institutional residents charge 3x/week at same location
= 156 verified data points per driver per year.
$3-5/vehicle/month from insurance underwriters for this
verified behavioral data.
$200/location/month property intelligence revenue.

REVENUE AT SCALE TABLE:
Scenario     | Locations | Location Intel  | Fleet SaaS  | Insurance | Total/mo
Conservative | 100       | $20,000/mo      | $8,000/mo   | $8,000/mo | $36,000/mo
Base         | 500       | $100,000/mo     | $40,000/mo  | $40,000/mo| $180,000/mo
Optimistic   | 1,000     | $200,000/mo     | $80,000/mo  | $80,000/mo| $360,000/mo

TWO ENGAGEMENT OPTIONS:
Option 1 — Distribution Partnership with Enhanced Referral:
Standard Nerava rate: 5% perpetual on referred buyers
Trident enhanced rate: 10% for first 12 months,
decays to 5% perpetual
No upfront cost. No investment required.
Decision needed: Pilot location + OCPP version.

Option 2 — SAFE Investment + Standard Referral:
Minimum $100K into Nerava SAFE at $8M post-money cap
No board seat, no MFN, no veto rights
Standard 5% perpetual referral (no decay)
$100K at $8M cap converts to ~$312,500 at $25M+ round
Decision needed: Investment amount.

REFERRAL EARNINGS TABLE:
Scenario     | Locations | Annual Revenue | Option 1 Yr1  | Option 2
Conservative | 100       | $1M/year       | $100K yr1     | $50K perpetual
Base         | 500       | $5M/year       | $500K yr1     | $250K perpetual
Optimistic   | 1,000     | $10M/year      | $1M yr1       | $500K perpetual

WHAT IS LIVE vs READY TO DEPLOY:
LIVE: Session verification, wallet/ledger, Stripe Express
payouts, sponsor campaign engine (12+ targeting rules),
merchant portal (zero POS changes), push notifications
(APNs and FCM), Partner API with HMAC-SHA256 webhooks

READY TO DEPLOY (pending Trident input):
- OCPP webhook receiver: needs OCPP version confirmation
- Iframe wallet embed: ready, white-label via URL params
- Location intelligence dashboard: ready, populates at
  first live location

TWO ASKS FROM TRIDENT TO GO LIVE:
1. OCPP version — 1.6 or 2.0.1? One answer, one day
   of configuration, sessions flow immediately.
2. One pilot location — high-traffic institutional
   site preferred.

MARKET TIMING ARGUMENT:
18,000 new DC Fast Ports added in US in 2025 alone
30% YoY DCFC growth and accelerating
12-18 months remaining to establish the wallet and
sponsorship infrastructure standard before a larger
player defines it
```

---

Those three blocks are formatted so you can paste each one directly into a Claude Code session as context. They're under the token limit individually and give Claude Code the full commercial framing without needing the PDF files.
