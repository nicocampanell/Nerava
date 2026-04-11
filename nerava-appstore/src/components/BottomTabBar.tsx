/**
 * `BottomTabBar` — three-tab navigation fixed at the bottom of the
 * phone frame. Tabs: Discover / My Apps / Settings.
 *
 * The tab bar is purely visual navigation — it calls `onSelect(screen)`
 * with a top-level screen id. The parent `<App>` handles the actual
 * screen swap via `navigateTo()`.
 */

import type { Screen } from "../state/store.js";

interface BottomTabBarProps {
  readonly active: Screen;
  readonly onSelect: (screen: Screen) => void;
}

interface TabEntry {
  readonly id: Screen;
  readonly label: string;
  readonly icon: string;
}

const TABS: readonly TabEntry[] = [
  { id: "home", label: "Discover", icon: "◎" },
  { id: "myApps", label: "My Apps", icon: "▤" },
  { id: "settings", label: "Settings", icon: "⚙" },
] as const;

export function BottomTabBar({
  active,
  onSelect,
}: BottomTabBarProps): React.JSX.Element {
  // `active === "detail"` still highlights the Discover tab — the detail
  // screen is conceptually a sub-screen of Home, not its own tab.
  const effective: Screen = active === "detail" ? "home" : active;

  return (
    <nav
      className="absolute inset-x-0 bottom-0 border-t border-black/5 bg-white/95 backdrop-blur"
      aria-label="Main navigation"
    >
      <div className="flex items-stretch justify-around px-2 pt-2 pb-5">
        {TABS.map((tab) => {
          const isActive = tab.id === effective;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={(): void => onSelect(tab.id)}
              aria-pressed={isActive}
              className="flex flex-1 flex-col items-center gap-0.5 py-1 transition active:scale-95"
            >
              <span
                aria-hidden="true"
                className={
                  "text-xl leading-none " +
                  (isActive ? "text-nerava-blue" : "text-nerava-ink/40")
                }
              >
                {tab.icon}
              </span>
              <span
                className={
                  "text-[10px] font-semibold " +
                  (isActive ? "text-nerava-blue" : "text-nerava-ink/50")
                }
              >
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
