/**
 * My Apps screen — lists only the apps the user has enabled, with the
 * name, category, a "last active" placeholder timestamp (purely cosmetic
 * — no real backend data yet), and a disable button.
 *
 * Empty state shows a helpful message directing the user back to
 * Discover.
 */

import type { AppEntry } from "../data/apps.js";
import type { AppStoreState } from "../state/store.js";

interface MyAppsScreenProps {
  readonly store: AppStoreState;
}

/**
 * Cosmetic "last active" strings so the list feels like a real app
 * store's "Recently Used" shelf. These are hardcoded because there's
 * no backend telemetry yet.
 */
const LAST_ACTIVE_HINTS: Record<string, string> = {
  "nerava-wallet": "Active now",
  "nerava-intelligence": "Today, 10:24 AM",
  "driveshield-insurance": "Yesterday",
  "fleetsync-trident": "3 days ago",
  "evgo-chargerewards": "Last week",
};

export function MyAppsScreen({ store }: MyAppsScreenProps): React.JSX.Element {
  return (
    <div className="flex h-full flex-col overflow-y-auto pb-24">
      <header className="px-5 pt-8 pb-3">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
          EVject
        </div>
        <h1 className="text-xl font-bold text-nerava-ink">My Apps</h1>
      </header>

      {store.enabledAppEntries.length === 0 ? (
        <EmptyState onDiscover={(): void => store.navigateTo("home")} />
      ) : (
        <section className="flex flex-col gap-2 px-4">
          {store.enabledAppEntries.map((app) => (
            <EnabledAppRow
              key={app.id}
              app={app}
              onOpen={(): void => store.openAppDetail(app.id)}
              onDisable={(): void => store.toggleApp(app.id)}
            />
          ))}
        </section>
      )}
    </div>
  );
}

interface EnabledAppRowProps {
  readonly app: AppEntry;
  readonly onOpen: () => void;
  readonly onDisable: () => void;
}

function EnabledAppRow({ app, onOpen, onDisable }: EnabledAppRowProps): React.JSX.Element {
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-white p-3 shadow-[0_1px_3px_rgba(11,26,60,0.08)] ring-1 ring-black/5">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open ${app.name}`}
        className={`flex h-12 w-12 flex-none items-center justify-center rounded-2xl bg-gradient-to-br ${app.iconGradient} text-lg font-bold text-white`}
      >
        {app.iconLabel}
      </button>
      <button
        type="button"
        onClick={onOpen}
        className="min-w-0 flex-1 text-left"
      >
        <div className="flex items-center gap-1.5">
          <h3 className="truncate text-sm font-semibold text-nerava-ink">{app.name}</h3>
          <span
            className="flex h-4 w-4 flex-none items-center justify-center rounded-full bg-green-500 text-[10px] font-bold text-white"
            aria-label="Enabled"
          >
            ✓
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-nerava-ink/50">
          <span className="rounded bg-nerava-bg px-1.5 py-0.5 font-medium">
            {app.category}
          </span>
          <span>·</span>
          <span>{LAST_ACTIVE_HINTS[app.id] ?? "Active"}</span>
        </div>
      </button>
      <button
        type="button"
        onClick={onDisable}
        className="flex-none rounded-full bg-nerava-bg px-3 py-1.5 text-[11px] font-semibold text-nerava-ink/70 transition active:scale-95 hover:bg-nerava-ink/10"
      >
        Disable
      </button>
    </div>
  );
}

function EmptyState({ onDiscover }: { readonly onDiscover: () => void }): React.JSX.Element {
  return (
    <div className="mx-4 mt-8 rounded-2xl bg-white p-6 text-center ring-1 ring-black/5">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-nerava-bg text-2xl text-nerava-ink/40">
        ▤
      </div>
      <p className="mt-3 text-sm font-semibold text-nerava-ink">No apps enabled yet</p>
      <p className="mt-1 text-xs text-nerava-ink/60">
        Head to Discover to find apps that match your charging habits.
      </p>
      <button
        type="button"
        onClick={onDiscover}
        className="mt-4 rounded-full bg-nerava-blue px-5 py-2 text-xs font-semibold text-white shadow-[0_2px_6px_rgba(19,163,226,0.35)] transition active:scale-95"
      >
        Browse apps
      </button>
    </div>
  );
}
