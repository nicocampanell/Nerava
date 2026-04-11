# nerava-appstore

In-app store experience that renders inside the EVject driver shell. EVject is the shell; Nerava powers what is inside.

## Stack

- React 19 + TypeScript
- Vite 7 with `@vitejs/plugin-react`
- Tailwind CSS 4 via `@tailwindcss/vite`
- No routing library — pure React state swap between screens
- No `localStorage` — state lives in a single top-level `useAppStoreState()` hook

## Scripts

```bash
npm install        # one-time install
npm run dev        # start the dev server on http://localhost:5178
npm run build      # tsc -b && vite build → dist/
npm run preview    # preview the production build
npm run typecheck  # strict tsc --noEmit across all src
```

## Screens

- **Home (Discover)** — featured "Get Paid to Charge" banner, category tabs (All / Rewards / Fleet / Intelligence / Insurance), filtered app grid
- **App Detail** — back button, icon, name, developer, category badge, rating, description, permissions list, enable/disable toggle with 1-second loading animation
- **My Apps** — list of enabled apps with "last active" hints and a per-row disable button
- **Settings** — placeholder preferences / privacy / about sections
- **Bottom tab bar** — Discover / My Apps / Settings, fixed across every screen

## App catalog

Five seeded apps (see `src/data/apps.ts`):

1. **Nerava Wallet** (Rewards, enabled by default)
2. **Nerava Intelligence** (Intelligence)
3. **DriveShield Insurance** (Insurance)
4. **FleetSync** by Trident Chargers (Fleet)
5. **ChargeRewards** by EVgo (Rewards)

## Color system

Defined as Tailwind v4 `@theme` custom properties in `src/index.css`:

| Token | Hex | Use |
|---|---|---|
| `nerava-navy` | `#0B1A3C` | dark screens |
| `nerava-blue` | `#13A3E2` | accent |
| `nerava-gold` | `#EDBE20` | title only |
| `nerava-white` | `#F0F6FA` | primary light |
| `nerava-ink` | `#1A2744` | body text on light |
| `nerava-bg` | `#F4F7FB` | light background |

## Viewport

Rendered inside a 390px phone frame centered on screen. The frame is a plain CSS component (`.phone-frame` in `index.css`) — no native iOS / Android shell, just a shadow-and-border element that sets the dimensions.
