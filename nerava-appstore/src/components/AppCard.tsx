/**
 * `AppCard` — the tile rendered for each app on the home-screen grid.
 * Shows the app icon, name, developer, category badge, rating, and an
 * enabled-checkmark indicator when the app is already enabled.
 *
 * Clicking anywhere on the card opens the app's detail screen.
 */

import type { AppEntry } from "../data/apps.js";

interface AppCardProps {
  readonly app: AppEntry;
  readonly enabled: boolean;
  readonly onClick: () => void;
}

export function AppCard({ app, enabled, onClick }: AppCardProps): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      className="relative flex w-full items-center gap-3 rounded-2xl bg-white p-3 text-left shadow-[0_1px_3px_rgba(11,26,60,0.08)] ring-1 ring-black/5 transition active:scale-[0.98] hover:shadow-[0_4px_12px_rgba(11,26,60,0.12)]"
    >
      <div
        className={`flex h-14 w-14 flex-none items-center justify-center rounded-2xl bg-gradient-to-br ${app.iconGradient} text-xl font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.2)]`}
        aria-hidden="true"
      >
        {app.iconLabel}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <h3 className="truncate text-sm font-semibold text-nerava-ink">
            {app.name}
          </h3>
          {enabled ? (
            <span
              className="flex h-4 w-4 flex-none items-center justify-center rounded-full bg-green-500 text-[10px] font-bold text-white"
              aria-label="Enabled"
              title="Enabled"
            >
              ✓
            </span>
          ) : null}
        </div>
        <p className="truncate text-xs text-nerava-ink/60">{app.developer}</p>
        <div className="mt-1 flex items-center gap-2">
          <span className="rounded-full bg-nerava-bg px-2 py-0.5 text-[10px] font-medium text-nerava-ink/70">
            {app.category}
          </span>
          <span className="text-[11px] text-nerava-ink/50">★ {app.rating.toFixed(1)}</span>
        </div>
      </div>
    </button>
  );
}
