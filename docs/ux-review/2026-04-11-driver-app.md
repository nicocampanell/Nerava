# Driver App UX Review — 2026-04-11

**Scope:** Logged-out state of the three top-level tabs (Stations, Wallet, Account) as captured in `driver-home-initial.png`, `driver-wallet.png`, `driver-account.png` at repo root (desktop viewport, production `app.nerava.network`).
**Reviewer:** Claude (shape-1 manual pass — no Playwright, no logged-in states).
**Context:** Kreg investor demo is Wednesday 2026-04-15. Fundraising window is live. Stability + demo-readiness is the priority until April 16.
**Companion files:** none yet — this review is the seed for a reusable rubric (section 1) that a future `scripts/ux-review.ts` will apply automatically.

---

## 1. Rubric (the reusable asset)

Every finding below is tagged against one of these dimensions. The rubric is deliberately short so it can be applied consistently across reviews.

| # | Dimension | What I checked for |
|---|---|---|
| R1 | **Visual hierarchy** | Does the eye land on the one thing the screen wants you to do? Is the primary CTA visually dominant? |
| R2 | **Empty states** | When a screen has no data, does it teach, motivate, or preview — or does it just say "nothing here"? |
| R3 | **Information density** | Is the fold filled with value? Or is there dead whitespace, especially below the primary CTA? |
| R4 | **Touch targets & affordance** | Are interactive elements ≥ 44×44pt? Do they look tappable? Are icons labeled? |
| R5 | **Consistency** | Same token/spacing/type scale across screens? Or is each screen its own design system? |
| R6 | **Onboarding & first-run** | Does a first-time logged-out user understand what the app does and how to start? |
| R7 | **Demo-narrative fit** | Does the UI surface the story we're telling investors (verified charging, AOV-based billing, Supercharger intelligence)? |
| R8 | **Copy clarity** | Is the language specific and memorable, or generic? Does it differentiate Nerava from a generic rewards app? |

**Severity levels:**

- **P0** — Blocks Wednesday's demo. Must be addressed before the presentation.
- **P1** — Real UX debt. Should land in the Apr 16+ cleanup sprint.
- **P2** — Polish. Nice-to-have; queue for the next design pass.

---

## 2. Demo-readiness summary (Wednesday Apr 15)

| Screen | Verdict | Blockers |
|---|---|---|
| Stations (map) | 🔴 **Not demo-ready** | Pin-label repetition (all 8 say "ChargePoint Network"), default center is downtown Austin not the I-35 corridor, no Supercharger story on-screen |
| Wallet (logged out) | 🔴 **Not demo-ready** | ~80% vertical whitespace below the Sign In button; makes the product look empty |
| Account (logged out) | 🟡 **Demo-risky** | Welcome card itself is fine; Favorites/Share Nerava sections below leak logged-out emptiness |

### Fastest-path demo mitigation (no code changes)

1. **Sign in to a demo account before the presentation.** Avoids all three logged-out empty states at once. This is the single most effective fix and costs nothing.
2. **Pre-pan the map** to Harker Heights / I-35 corridor before screen-sharing. Aligns the first frame with the Tesla Supercharger intelligence story.
3. **Show `reports/nerava-intelligence-tesla-superchargers.pdf` on a second monitor / browser tab** for the Supercharger moat narrative, rather than trying to make the map tell that story.

### Small code changes worth making before Wednesday (scoped, low-risk)

- **W-1** (P0, ~10 min): Add one more line of value-prop copy + a muted "Drivers earned $X last month" subtitle to the wallet logged-out state (`DriverHome.tsx:1302-1314`). Fills fold, tells the story, zero risk.
- **S-1** (P0, ~20 min): Change the default map center when geolocation is unavailable from Austin downtown → Harker Heights (30.9876°N, -97.6492°W). One constant swap. Aligns with the Tesla Supercharger coverage story.
- **S-2** (P0, ~30 min): When the visible pin set has ≥ 3 of the same network, collapse the pin label to an icon-only bubble so the cluster doesn't read as "8 copies of ChargePoint". Or show a badge count: "ChargePoint ×8". Addresses the repetition-misread.

Everything else waits until after April 15.

---

## 3. Cross-cutting findings

### C-1 — Three locked doors instead of one onboarding — **P0** — *R6, R2*
**Evidence:** `driver-home-initial.png` (map shows pins but no "start a session" affordance), `driver-wallet.png` (Sign In card), `driver-account.png` (Welcome Sign In card).
**Problem:** A first-time user hits three different "sign in" prompts — one per tab — with three different layouts and three different pitches. It reads as three failed attempts instead of one guided onboarding.
**Suggested fix:** A single modal/sheet on first open (or on any tap of a locked surface) that introduces Nerava once and returns the user to where they tapped. Use `LoginModal` (already exists at `apps/driver/src/components/Account/LoginModal.tsx`) with richer pre-auth context.
**Cost:** ~2-3 hours to wire up, not for this week.

### C-2 — Two blue ramps, one design system — **P1** — *R5*
**Evidence:**
- `apps/driver/src/components/Account/AccountPage.tsx:450` uses the brand blue `#1877F2` (Facebook blue) in `bg-gradient-to-br from-[#1877F2] to-[#0d5bbf]`.
- `apps/driver/src/components/DriverHome/DriverHome.tsx:1310` uses Tailwind's `bg-blue-600` (which is `#2563eb`) for the Sign In button in the wallet empty state.
- `App.tsx:41` also uses `bg-[#1877F2]` for the 404 CTA.
**Problem:** Two different primary blues render side-by-side if you tab between Wallet and Account. It's not visible in a single screenshot but will be visible in a Figma recreation or side-by-side demo.
**Suggested fix:** Pick one blue, promote it to a Tailwind theme token (`theme.extend.colors.brand`), and use `bg-brand` everywhere. Never use raw hex or Tailwind palette blues in CTA code going forward.

### C-3 — Typography scale is inconsistent across empty states — **P1** — *R5*
**Evidence:**
- Wallet empty state title: `text-lg font-semibold` (`DriverHome.tsx:1306`).
- Account welcome title: `text-xl font-bold` (`AccountPage.tsx:456`).
**Problem:** Different hierarchy rules per tab. These are sibling screens serving the same "sign in" purpose; they should have identical type hierarchy.
**Suggested fix:** Define a shared `<EmptyState>` component with a fixed `text-xl font-bold` title, `text-sm text-gray-500` description, and a single CTA slot. Use it on both tabs.

### C-4 — Logged-out chrome leaks logged-in affordances — **P1** — *R2, R6*
**Evidence:** `AccountPage.tsx:582-611` — Favorites and Share Nerava cards render even when `isAuthenticated === false`. See the "Favorites: 0 saved" and "Share Nerava / Earn rewards for referrals" cards in `driver-account.png`.
**Problem:** A logged-out user can't actually share a referral code they don't have, and can't save favorites that get persisted to an account. Showing these cards in a disabled-looking state creates visual clutter and implies the product has less real value than it does.
**Suggested fix:** Gate `Favorites`, `Share Nerava`, `Connected Vehicles`, and `Charging Activity` cards behind `isAuthenticated`. Logged-out Account tab should be *only* the Welcome card + Preferences section.

### C-5 — Primary charging action is buried — **P1** — *R1, R7*
**Evidence:** All three tabs. Nothing in the top-level chrome says "start charging" or "claim an offer". The entire primary value path runs through map → pin tap → charger detail sheet → drive to charger → app detects session.
**Problem:** For a demo, the primary action should be 1 tap away from the first frame. Right now the investor sees a map and has to infer the product.
**Suggested fix (post-demo):** Persistent top banner when a session is active ("You're charging at Market Heights Supercharger — tap to view"). Already partially implemented via `ActiveSessionBanner.tsx` but not surfaced in the logged-out flow.

---

## 4. Stations tab findings (`driver-home-initial.png`)

### S-1 — Map defaults to Austin downtown, story lives on I-35 — **P0** — *R7*
**Evidence:** The screenshot shows 8 pins clustered around Congress Ave / Lady Bird Lake. The Tesla Supercharger intelligence report tracks 6 sites (Temple, Harker Heights, Jarrell, Georgetown, Lorena, Round Rock — 144 stalls total) on the I-35 corridor north of Austin. None of those pins are visible in the default frame.
**Problem:** The first frame an investor sees doesn't align with the most differentiated story Nerava has (real-time Tesla Supercharger availability from the April 11 expansion drive). They see a generic "charger map of Austin" instead of "live intelligence on 144 Tesla stalls we monitor every 15 min."
**Suggested fix:** See "Fastest-path demo mitigation" above — either pre-pan manually (Wednesday) or change the default center constant (before Wednesday). Long-term, add a "Tesla Supercharger" layer toggle that's highlighted for new users.

### S-2 — Eight "ChargePoint Network" labels read as one network — **P0** — *R1, R8*
**Evidence:** The clustered pins in `driver-home-initial.png` all carry a visible "ChargePoint Network" text label. The cluster looks like 8 copies of the same network rather than a multi-network map.
**Problem:** Nerava's positioning is network-agnostic coverage. Showing only one label 8 times flips the narrative: it looks like a single-network reseller.
**Suggested fix:** Hide the text label on clustered pins entirely; show only the colored pin circle. Reveal network name on hover/tap. Alternatively, show a collapsed badge "ChargePoint ×8" when N of the same network are in-cluster. Both are small changes in `ChargerMap.tsx` / `DiscoveryView`.

### S-3 — Top-right map controls are unlabeled and stacked — **P1** — *R4*
**Evidence:** Three small square buttons in the top-right of the map (search, filter, re-center based on icon shapes). No tooltips visible, no labels. Very small relative to touch targets.
**Problem:** First-time users won't know which is which. Filter button in particular is ambiguous — filter what? Networks? Distance? Connector type?
**Suggested fix:** Add `aria-label` + `title` attributes for mouse hover, increase touch target to 44×44, consider adding tiny text labels below each icon for first-time users.

### S-4 — Leaflet attribution reads as UI — **P2** — *R1*
**Evidence:** "Leaflet | © OpenStreetMap" bar at the bottom right of the map, just above the tab bar.
**Problem:** It visually looks like another UI control rather than legal attribution. Not a blocker, but worth styling as muted text.
**Suggested fix:** Tailwind `text-[10px] text-gray-400 opacity-60` on the attribution bar. Still legal-compliant, less visually competitive.

### S-5 — No on-screen pitch or value copy — **P1** — *R6, R7*
**Evidence:** A first-time user opens the app and sees... a map of chargers. No headline, no subtitle, no explanation of what Nerava does.
**Problem:** The map is beautiful but mute. An investor who sees just this screen for 2 seconds has zero narrative.
**Suggested fix:** For the logged-out state only, overlay a dismissible one-line headline near the top: "Verified EV charging rewards. $X earned by drivers this month." Appears for logged-out users, disappears after first sign-in.

### S-6 — Tab-bar icons are very small — **P2** — *R4, R5*
**Evidence:** Bottom tab bar in all three screenshots. Icon + label group appears < 30pt tall; labels look ~10-11px.
**Problem:** Touch target might clear 44pt because of generous tap area padding, but visual weight is too light. Hard to see on older devices or in sunlight.
**Suggested fix:** Verify touch-target size in a Playwright mobile capture (deferred). Bump label size to 12px and increase icon size to 24px.

---

## 5. Wallet tab findings (`driver-wallet.png`)

### W-1 — 80% of the fold is empty whitespace — **P0** — *R2, R3, R7*
**Evidence:** `DriverHome.tsx:1302-1314`. The logged-out empty state is: small $ icon, one-line headline, one-line description, Sign In button — then nothing. The rest of the viewport is pure white.
**Problem:** This is the #1 demo risk CLAUDE.md already flagged. An investor opening the Wallet tab sees an empty product. Worse, the empty fold suggests there's nothing compelling hidden behind sign-in.
**Suggested fix (for Wednesday):** Add below the Sign In button, in muted text:

```text
Drivers have earned $X,XXX this month across Y,YYY verified charging sessions.
Charge at supported stations, get paid on session end.
```

Pull `$X` and `Y` from `GET /v1/admin/stats/public` (or hardcode last-known values if that endpoint doesn't exist — per CLAUDE.md "Production Stats" memory: 68 sessions, 8 drivers, 625.5 kWh delivered). Even hardcoded beats empty whitespace.

**Suggested fix (post-demo):** Replace the empty state with a preview card: a faded-out mockup of the signed-in wallet with real-ish numbers ("$24.50 balance • 3 pending rewards") overlayed with a semi-transparent Sign In CTA. Pattern: "tease, don't block."

### W-2 — Generic copy fails to differentiate — **P1** — *R8*
**Evidence:** "Earn rewards from charging sessions and withdraw to your bank." (`DriverHome.tsx:1307`).
**Problem:** Could describe any rewards app. Doesn't mention the specific Nerava differentiators: verified charging (Tesla Fleet API), 4%-of-AOV merchant payouts, prepaid campaign credits, instant Stripe Express payouts.
**Suggested fix:** "Get paid when you charge at supported stations. Verified by Tesla Fleet API. Withdraw to your bank any time via Stripe."

### W-3 — Dead-end: no secondary path — **P2** — *R6*
**Evidence:** Only action is "Sign In". No "How it works", no "See demo", no "Learn more".
**Problem:** Users who aren't ready to commit have nowhere to go except back to the map. Wallet becomes a roadblock instead of a funnel step.
**Suggested fix:** Secondary button below Sign In: "How it works →" linking to a short explainer. Or a small inline "See a sample wallet" that opens a read-only preview with fake data.

### W-4 — Icon is generic — **P2** — *R8*
**Evidence:** Dollar-sign icon in a blue circle — universal "money" pattern.
**Problem:** Unmemorable. Any rewards app has this icon.
**Suggested fix:** Swap for a custom Nerava icon (lightning bolt + dollar? gauge + dollar?). Low priority; brand identity exercise.

---

## 6. Account tab findings (`driver-account.png`)

### A-1 — Welcome card is the one thing working well — **✅**
The logged-out Welcome card at `AccountPage.tsx:450-490` is the most polished logged-out surface in the app. Blue gradient, 3-item value prop, clear CTA. This is the template the other two tabs should match.

### A-2 — Logged-out Favorites card is zero-value — **P1** — *R2, R6*
**Evidence:** `AccountPage.tsx:582-595`. Shows "Favorites / 0 saved" below the Welcome card.
**Problem:** A logged-out user can't save favorites to any account. Shows an empty useless state that visually competes with the primary CTA (Welcome card).
**Suggested fix:** See C-4. Gate behind `isAuthenticated`.

### A-3 — Logged-out Share Nerava card is zero-value — **P1** — *R2, R6*
**Evidence:** `AccountPage.tsx:597-611`. Shows "Share Nerava / Earn rewards for referrals" below the Welcome card.
**Problem:** A logged-out user has no referral code to share. Tapping this likely either no-ops, shows an error, or funnels back to sign-in — none of which serve the user. And tapping it from a logged-out state is adversely gamified: "promise reward, withhold reward."
**Suggested fix:** See C-4. Gate behind `isAuthenticated`.

### A-4 — Scroll affordance unclear — **P2** — *R1*
**Evidence:** "PREFERENCES" header is cut off at the bottom of `driver-account.png`.
**Problem:** No visual indication that the page scrolls. User might not know more content is below.
**Suggested fix:** Add a subtle gradient fade at the bottom edge, or nudge the "PREFERENCES" header fully into frame at the initial scroll position.

### A-5 — Value-prop list is visually dense — **P2** — *R1*
**Evidence:** `AccountPage.tsx:461-480`. Three checkmark items stacked tightly with small (w-4 h-4) icons.
**Problem:** Items blur together at a glance. Checkmark icons are the same size as the text x-height.
**Suggested fix:** Increase icon size to w-5 h-5, add `space-y-3` instead of current spacing. Minor.

---

## 7. What this review did NOT cover

Explicit scope cuts — document them so a later pass knows where to start:

- **Mobile viewport (375×812)**. Everything above is a desktop viewport. Touch targets, tap area, mobile keyboard behavior, safe-area insets — all unverified.
- **Logged-in states**. Active session banner, wallet with real balance, energy reputation card, exclusive active view, charger detail sheet, claim confirmation modal — none reviewed. These are the highest-value screens for the Figma mockup and the next UX review pass.
- **Modals**. `LoginModal`, `WalletModal` body, `ChargerDetailSheet`, `ClaimConfirmModal`, `MerchantActionSheet`, `ActiveVisitTracker` — none captured.
- **Accessibility**. No axe/Lighthouse pass. Color contrast, screen-reader landmarks, focus order — unverified.
- **Error states**. Offline banner, session expired modal, native bridge error banner, inline error — none tested.
- **Other apps**. Merchant portal, admin dashboard, sponsor console — all out of scope.

---

## 8. Next pass (after Wednesday)

Rank-ordered, cheapest to most expensive:

1. **Capture logged-in states via Playwright** at 375×812 (needs a demo account). Re-run this rubric against them. Probably surfaces more findings than the logged-out pass since that's where most of the product lives.
2. **Codify the section-1 rubric into `scripts/ux-review.ts`.** Pipes each screenshot through the Claude API with the rubric as a system prompt, outputs a timestamped `docs/ux-review/YYYY-MM-DD-*.md`. Then this review becomes a one-command operation.
3. **Add an axe/Lighthouse pass** to the same script for objective a11y findings. Complements judgment calls with hard facts.
4. **Wire the script into CI** as a PR-comment bot when a preview deploy target exists. (Blocked on preview deploys — currently only deploy to prod S3.)

---

## 9. File:line index (for fix PRs)

Findings referenced these exact source locations. Use this as the edit list:

| Finding | File | Lines |
|---|---|---|
| W-1 (wallet empty fold) | `apps/driver/src/components/DriverHome/DriverHome.tsx` | 1302-1314 |
| W-2 (wallet copy) | `apps/driver/src/components/DriverHome/DriverHome.tsx` | 1307 |
| S-1 (default map center) | TBD — search for `Austin` or the hardcoded lat/lng in `ChargerMap.tsx` or `DiscoveryView` | — |
| S-2 (pin label repetition) | TBD — `ChargerMap.tsx` marker label rendering | — |
| A-2 (logged-out Favorites) | `apps/driver/src/components/Account/AccountPage.tsx` | 582-595 |
| A-3 (logged-out Share Nerava) | `apps/driver/src/components/Account/AccountPage.tsx` | 597-611 |
| C-2 (blue ramp drift) | `apps/driver/src/components/Account/AccountPage.tsx` + `DriverHome.tsx:1310` + `App.tsx:41` | — |
| C-3 (type scale) | `DriverHome.tsx:1306` + `AccountPage.tsx:456` | — |
| C-4 (logged-out chrome leaks) | `apps/driver/src/components/Account/AccountPage.tsx` | 510-611 |

---

**Review ends. Total findings: 18 (4 P0, 9 P1, 5 P2).**
