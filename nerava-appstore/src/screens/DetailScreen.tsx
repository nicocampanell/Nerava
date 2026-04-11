/**
 * App detail screen — back button, app icon, name, developer, category
 * badge, description, permissions list, and enable/disable toggle with
 * a 1-second loading animation on state change.
 *
 * The loading animation exists because partner developers often evaluate
 * apps by clicking enable/disable repeatedly. Without a brief loading
 * state, the toggle feels too snappy and users can't tell it actually
 * registered. 1 second is the minimum that still feels responsive.
 */

import { useEffect, useRef, useState } from "react";

import type { AppEntry } from "../data/apps.js";
import type { AppStoreState } from "../state/store.js";

interface DetailScreenProps {
  readonly store: AppStoreState;
  readonly app: AppEntry;
}

export function DetailScreen({ store, app }: DetailScreenProps): React.JSX.Element {
  const enabled = store.enabledApps.has(app.id);
  const [loading, setLoading] = useState(false);

  // Track the pending setTimeout so we can cancel it on unmount. Without
  // this, tapping Back during the 1-second loading animation would
  // `setLoading(false)` after the component is gone, which React flags
  // as a "state update on an unmounted component" dev warning.
  const toggleTimeoutRef = useRef<number | null>(null);
  useEffect(() => {
    return (): void => {
      if (toggleTimeoutRef.current !== null) {
        window.clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = null;
      }
    };
  }, []);

  const handleToggle = (): void => {
    if (loading) return;
    setLoading(true);
    // 1-second loading animation before the state flips. Matches the
    // prompt's "enable toggle with 1-second loading animation" spec.
    toggleTimeoutRef.current = window.setTimeout(() => {
      store.toggleApp(app.id);
      setLoading(false);
      toggleTimeoutRef.current = null;
    }, 1000);
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto pb-24">
      {/* Header with back button */}
      <header className="flex items-center gap-3 px-5 pt-8 pb-4">
        <button
          type="button"
          onClick={store.goBackToHome}
          className="flex h-9 w-9 items-center justify-center rounded-full bg-white text-lg text-nerava-ink shadow-[0_1px_3px_rgba(11,26,60,0.08)] ring-1 ring-black/5 transition active:scale-95"
          aria-label="Back to app store"
        >
          ‹
        </button>
        <div className="text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
          App Detail
        </div>
      </header>

      {/* App identity block */}
      <section className="flex gap-4 px-5 pb-5">
        <div
          className={`flex h-20 w-20 flex-none items-center justify-center rounded-3xl bg-gradient-to-br ${app.iconGradient} text-3xl font-bold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.2),0_8px_16px_rgba(11,26,60,0.15)]`}
          aria-hidden="true"
        >
          {app.iconLabel}
        </div>
        <div className="flex min-w-0 flex-1 flex-col justify-center">
          <h2 className="text-xl font-bold leading-tight text-nerava-ink">
            {app.name}
          </h2>
          <p className="text-xs text-nerava-ink/60">{app.developer}</p>
          <div className="mt-2 flex items-center gap-2">
            <span className="rounded-full bg-nerava-bg px-2.5 py-0.5 text-[10px] font-semibold text-nerava-ink/70">
              {app.category}
            </span>
            <span className="text-xs text-nerava-ink/50">★ {app.rating.toFixed(1)}</span>
          </div>
        </div>
      </section>

      {/* Enable toggle */}
      <section className="px-5 pb-4">
        <button
          type="button"
          onClick={handleToggle}
          disabled={loading}
          aria-pressed={enabled}
          className={
            "flex h-12 w-full items-center justify-center rounded-2xl text-sm font-semibold transition active:scale-[0.99] " +
            (loading
              ? "bg-nerava-bg text-nerava-ink/40"
              : enabled
                ? "bg-white text-nerava-ink ring-1 ring-black/10 hover:bg-nerava-bg"
                : "bg-nerava-blue text-white shadow-[0_4px_12px_rgba(19,163,226,0.35)] hover:shadow-[0_6px_16px_rgba(19,163,226,0.45)]")
          }
        >
          {loading ? (
            <span className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-nerava-ink/30 border-t-nerava-ink/70"
              />
              {enabled ? "Disabling…" : "Enabling…"}
            </span>
          ) : enabled ? (
            "Disable"
          ) : (
            "Enable"
          )}
        </button>
      </section>

      {/* Description */}
      <section className="px-5 pb-4">
        <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
          About
        </h3>
        <p className="text-sm leading-relaxed text-nerava-ink/80">{app.description}</p>
      </section>

      {/* Permissions */}
      <section className="px-5 pb-6">
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
          Permissions
        </h3>
        <ul className="space-y-1.5">
          {app.permissions.map((permission) => (
            <li
              key={permission}
              className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-xs text-nerava-ink/80 ring-1 ring-black/5"
            >
              <span aria-hidden="true" className="text-nerava-blue">
                ◆
              </span>
              {permission}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
