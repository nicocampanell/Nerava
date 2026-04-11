# Referral Program Gap Analysis

**Date:** April 5, 2026
**Trigger:** Parker tested referral link in browser — loaded the app normally with no referral context. System is non-functional end-to-end.

---

## Current State

### What's Built

| Component | Status | Location |
|-----------|--------|----------|
| ReferralCode model | Built | `backend/app/models/referral.py` |
| ReferralRedemption model | Built | `backend/app/models/referral.py` |
| DB migration (094) | Applied | `alembic/versions/094_add_referrals_and_user_preferences.py` |
| Code generation (`NERAVA-XXXXXX`) | Built | `backend/app/services/referral_service.py` lines 17-44 |
| Redeem endpoint | Built | `POST /v1/referrals/redeem` |
| Stats endpoint | Built | `GET /v1/referrals/stats` |
| Code endpoint | Built | `GET /v1/referrals/code` |
| Share UI (QR + copy + native share) | Built | `apps/driver/src/components/Account/ShareNerava.tsx` |
| `/join?ref=CODE` route handler | Built | `apps/driver/src/App.tsx` lines 47-58 |
| Reward logic ($5 mutual credit) | Built | `referral_service.py` `grant_referral_rewards()` lines 75-101 |

### What's Broken

**The entire referral flow is broken in 3 places:**

1. **No landing page.** Visiting `app.nerava.network/join?ref=NERAVA-ABCDEF` stores the code in `sessionStorage` and immediately redirects to home. The referred user sees the normal app — no indication they arrived via referral, no prompt to sign up, no referral context displayed. This is what Parker saw.

2. **Code is never redeemed.** The code sits in `sessionStorage` but nothing reads it back. No frontend code calls `POST /v1/referrals/redeem`. The `redeemReferralCode()` API function exists in `api.ts` but is never invoked. If the user refreshes the page, the code is lost entirely (sessionStorage is ephemeral).

3. **Rewards are never granted.** `grant_referral_rewards()` exists in `referral_service.py` but is **never called from anywhere in the codebase**. Even if redemption worked, the $5 credit would never hit either wallet. The function needs to be called after the referred user's first verified charging session.

**Net result:** Referral links do nothing. No user has ever received a referral reward.

---

## What Needs to Be Built

### Phase 1: Fix the Broken Flow (P0)

#### 1.1 Referral Landing Page

**Current:** `/join?ref=CODE` → store in sessionStorage → redirect to `/` (home)

**Required:** A dedicated landing page at `/join` that:
- Displays referral context: "You've been invited by a friend"
- Shows two CTAs: **"Join as Driver"** and **"Join as Merchant Partner"**
- Captures the referral code in `localStorage` (not sessionStorage — survives refresh)
- Driver CTA → opens login modal with referral context banner
- Merchant CTA → redirects to `merchant.nerava.network/claim?ref=CODE`

**Design:**
```
┌────────────────────────────────┐
│  [Nerava Logo]                 │
│                                │
│  You've been invited to Nerava │
│  Earn rewards every time you   │
│  charge your EV                │
│                                │
│  ┌──────────────────────────┐  │
│  │  ⚡ Join as Driver       │  │
│  │  Earn cash + Nova points │  │
│  │  on every charge         │  │
│  └──────────────────────────┘  │
│                                │
│  ┌──────────────────────────┐  │
│  │  🏪 Join as Merchant     │  │
│  │  Get EV drivers to your  │  │
│  │  business for free       │  │
│  └──────────────────────────┘  │
│                                │
│  Both of you earn $5 when     │
│  you complete your first       │
│  charging session              │
└────────────────────────────────┘
```

**Effort:** 2-3 days (frontend)

#### 1.2 Auto-Redeem on Login

**Current:** Code stored but never submitted

**Required:**
- After successful OTP verify / Apple / Google sign-in, check `localStorage` for `nerava_referral_code`
- If present, call `POST /v1/referrals/redeem` with the code
- Show toast: "Referral applied! You'll both earn $5 after your first charge"
- Clear the code from localStorage
- Handle edge cases: self-referral, already-redeemed, invalid code

**Effort:** 1 day (frontend + backend wiring)

#### 1.3 Grant Rewards on First Session

**Current:** `grant_referral_rewards()` never called

**Required:**
- In `session_event_service.py`, after `IncentiveEngine.evaluate_session()` on session end:
  - Check if this is the user's first completed session (session count == 1)
  - If yes, call `grant_referral_rewards(db, user_id)`
  - This credits $5 to both the referrer and the referred user
- Send push notification to referrer: "Your friend just completed their first charge! $5 credited to your wallet"

**Effort:** 0.5 days (backend)

#### 1.4 Persist Code in localStorage (Not sessionStorage)

**Current:** `sessionStorage.setItem('nerava_referral_code', ref)` — lost on page close

**Required:** Switch to `localStorage` so the code survives browser refresh and app relaunch. Add 7-day expiry check.

**Effort:** 0.5 hours

---

### Phase 2: Dual Referral Types (P1)

#### 2.1 Merchant Referral Flow

**Current:** ShareNerava UI says "Merchant referral: Free month premium" but no merchant referral system exists.

**Required:**
- One referral link works for both audiences (driver and merchant)
- Landing page routes to the correct flow based on user choice
- Merchant referral: referred merchant claims business via portal, referrer gets reward when merchant's first deal goes live
- Merchant reward: first month free (waive subscription/platform fee)
- Driver reward for merchant referral: $10 credit (higher than driver-to-driver)

**Backend changes:**
- Add `referral_type` enum to `ReferralRedemption`: `driver` | `merchant`
- Add `merchant_grant_referral_rewards()` triggered on first deal going live
- Track merchant referrals in `ReferralRedemption` with FK to `domain_merchants`

**Effort:** 3-4 days

#### 2.2 Referral Stats in Wallet Page

**Current:** Wallet shows balance and withdraw flow only. No referral visibility.

**Required:** Add a "Referrals" section to the wallet page:
- "Your Referrals" card showing:
  - Total referred (drivers + merchants)
  - Total earned from referrals
  - Pending rewards (referred users who haven't completed first session)
- Link to share referral code
- List of recent referrals with status (pending / rewarded)

**Effort:** 2 days (frontend)

---

### Phase 3: Leaderboard & Social Proof (P1)

#### 3.1 Earnings Leaderboard

**Current:** Does not exist anywhere in the codebase.

**Required:** A leaderboard component in the wallet/earnings section showing:
- Top 10 earners on Nerava (anonymized: "Driver #1", "Driver #2" or first name only)
- Current user's rank highlighted
- Total sessions and total earned for each
- Weekly/monthly/all-time toggle
- Opt-in: users must consent to appear on leaderboard

**Backend:**
- `GET /v1/leaderboard` endpoint returning top earners
- Query: aggregate `wallet_ledger` credits by user, sorted desc, limit 10
- Privacy: return `display_name` (first name only) or anonymized identifier
- Cache: 1-hour TTL (leaderboard doesn't need real-time updates)

**Frontend:**
- `LeaderboardCard` component in wallet page
- Rank badge for top 3 (gold/silver/bronze)
- Current user highlighted with "You" indicator
- Tap to expand shows session count and tier

**Effort:** 3-4 days (1 day backend, 2-3 days frontend)

#### 3.2 Social Proof in Referral Share

**Current:** Share screen shows QR code and referral code only.

**Required:** Add social proof to the share screen:
- "X drivers have earned $Y on Nerava" (aggregate stat)
- User's personal stats: "You've earned $Z across N sessions"
- "Join [First Name]'s network" framing on the landing page

**Effort:** 1 day

---

## Priority Summary

| Item | Priority | Effort | Blocks |
|------|----------|--------|--------|
| Fix referral landing page | **P0** | 2-3 days | Every referral |
| Auto-redeem on login | **P0** | 1 day | Every referral |
| Grant rewards on first session | **P0** | 0.5 days | Every referral reward |
| Switch sessionStorage → localStorage | **P0** | 0.5 hours | Code persistence |
| Referral stats in wallet | **P1** | 2 days | Referral visibility |
| Merchant referral flow | **P1** | 3-4 days | Merchant growth |
| Earnings leaderboard | **P1** | 3-4 days | Social proof / retention |
| Social proof in share screen | **P1** | 1 day | Share conversion |

**Total P0 effort: ~4 days**
**Total P1 effort: ~10 days**

---

## Data Model Changes

### New: `referral_type` on ReferralRedemption
```sql
ALTER TABLE referral_redemptions
ADD COLUMN referral_type VARCHAR(20) DEFAULT 'driver';
-- Values: 'driver', 'merchant'
```

### New: Leaderboard view (or materialized query)
```sql
-- Leaderboard query (cache result for 1 hour)
SELECT u.id, u.display_name,
       COUNT(se.id) as total_sessions,
       COALESCE(SUM(wl.amount_cents), 0) as total_earned_cents
FROM users u
JOIN session_events se ON se.driver_user_id = u.id
LEFT JOIN wallet_ledger wl ON wl.driver_id = u.id AND wl.transaction_type = 'credit'
WHERE u.is_active = true
GROUP BY u.id, u.display_name
ORDER BY total_earned_cents DESC
LIMIT 10;
```

---

## Testing Checklist

- [ ] Visit `app.nerava.network/join?ref=NERAVA-TEST1` — see landing page with two CTAs
- [ ] Click "Join as Driver" — login modal opens with referral banner
- [ ] Complete OTP login — referral auto-redeemed, toast shown
- [ ] Complete first charging session — both wallets credited $5
- [ ] Check referrer's wallet — $5 credit with "Referral reward" description
- [ ] Check referred user's wallet — $5 credit with "Welcome bonus" description
- [ ] Refresh page before login — referral code still in localStorage
- [ ] Click "Join as Merchant" — redirects to merchant portal with ref code
- [ ] Share screen — shows personal stats and network-wide social proof
- [ ] Wallet page — shows referral stats card with pending/earned counts
- [ ] Leaderboard — shows top 10 earners with current user highlighted
