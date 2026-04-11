/**
 * App-wide state management.
 *
 * Pure React state — no localStorage, no Redux, no Zustand. The prompt
 * explicitly bans localStorage ("No localStorage anywhere. React state
 * only.") so state is held in a single top-level `<App>` component and
 * passed down via context/props.
 *
 * Four pieces of state:
 *
 *   1. `enabledApps` — Set<string> of app ids the user has enabled.
 *      Seeded from `APPS[].defaultEnabled` on first mount.
 *
 *   2. `selectedCategory` — which category tab is active on Home.
 *      `"All"` shows everything.
 *
 *   3. `activeScreen` — which top-level screen is showing. One of
 *      `"home" | "detail" | "myApps" | "settings"`.
 *
 *   4. `selectedAppId` — which app's detail is being shown when
 *      `activeScreen === "detail"`. `null` otherwise.
 */

import { useMemo, useState } from "react";

import { APPS, type AppCategory, type AppEntry } from "../data/apps.js";

export type CategoryFilter = "All" | AppCategory;

export type Screen = "home" | "detail" | "myApps" | "settings";

/**
 * Seed the enabled-apps set from the static catalog. Run ONCE on the
 * initial state, not on every render — `useState` with a factory
 * function ensures this.
 */
function seedEnabledApps(): ReadonlySet<string> {
  const initial = new Set<string>();
  for (const app of APPS) {
    if (app.defaultEnabled) {
      initial.add(app.id);
    }
  }
  return initial;
}

export interface AppStoreState {
  readonly enabledApps: ReadonlySet<string>;
  readonly selectedCategory: CategoryFilter;
  readonly activeScreen: Screen;
  readonly selectedAppId: string | null;

  readonly toggleApp: (appId: string) => void;
  readonly setSelectedCategory: (category: CategoryFilter) => void;
  readonly navigateTo: (screen: Screen, appId?: string) => void;
  readonly openAppDetail: (appId: string) => void;
  readonly goBackToHome: () => void;

  /** Derived: catalog filtered by `selectedCategory`. */
  readonly visibleApps: readonly AppEntry[];
  /** Derived: enabled apps only. Used by the My Apps screen. */
  readonly enabledAppEntries: readonly AppEntry[];
  /** Derived: the app entry for `selectedAppId`, or `null`. */
  readonly selectedApp: AppEntry | null;
}

export function useAppStoreState(): AppStoreState {
  const [enabledApps, setEnabledApps] = useState<ReadonlySet<string>>(seedEnabledApps);
  const [selectedCategory, setSelectedCategory] = useState<CategoryFilter>("All");
  const [activeScreen, setActiveScreen] = useState<Screen>("home");
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);

  const toggleApp = (appId: string): void => {
    setEnabledApps((prev) => {
      const next = new Set(prev);
      if (next.has(appId)) {
        next.delete(appId);
      } else {
        next.add(appId);
      }
      return next;
    });
  };

  const navigateTo = (screen: Screen, appId?: string): void => {
    setActiveScreen(screen);
    if (appId !== undefined) {
      setSelectedAppId(appId);
    } else if (screen !== "detail") {
      setSelectedAppId(null);
    }
  };

  const openAppDetail = (appId: string): void => {
    setSelectedAppId(appId);
    setActiveScreen("detail");
  };

  const goBackToHome = (): void => {
    setActiveScreen("home");
    setSelectedAppId(null);
  };

  const visibleApps = useMemo(
    () =>
      selectedCategory === "All"
        ? APPS
        : APPS.filter((app) => app.category === selectedCategory),
    [selectedCategory],
  );

  const enabledAppEntries = useMemo(
    () => APPS.filter((app) => enabledApps.has(app.id)),
    [enabledApps],
  );

  const selectedApp = useMemo(
    () => (selectedAppId !== null ? (APPS.find((a) => a.id === selectedAppId) ?? null) : null),
    [selectedAppId],
  );

  return {
    enabledApps,
    selectedCategory,
    activeScreen,
    selectedAppId,
    toggleApp,
    setSelectedCategory,
    navigateTo,
    openAppDetail,
    goBackToHome,
    visibleApps,
    enabledAppEntries,
    selectedApp,
  };
}
