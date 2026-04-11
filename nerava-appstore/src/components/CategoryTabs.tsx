/**
 * `CategoryTabs` — horizontal pill-style tabs for filtering the app
 * grid. The active tab is highlighted with the Nerava electric-blue
 * accent color; inactive tabs are subdued.
 *
 * Tabs animate on selection via CSS transitions on background and
 * text color.
 */

import { APP_CATEGORIES, type AppCategory } from "../data/apps.js";
import type { CategoryFilter } from "../state/store.js";

interface CategoryTabsProps {
  readonly selected: CategoryFilter;
  readonly onSelect: (category: CategoryFilter) => void;
}

const ALL_TABS: readonly CategoryFilter[] = ["All", ...APP_CATEGORIES] as const;

export function CategoryTabs({
  selected,
  onSelect,
}: CategoryTabsProps): React.JSX.Element {
  return (
    <nav
      className="flex gap-2 overflow-x-auto px-4 py-2 [&::-webkit-scrollbar]:hidden"
      aria-label="Filter by category"
    >
      {ALL_TABS.map((category) => {
        const isActive = category === selected;
        return (
          <button
            key={category}
            type="button"
            onClick={(): void => onSelect(category)}
            aria-pressed={isActive}
            className={
              "whitespace-nowrap rounded-full px-4 py-1.5 text-xs font-semibold transition " +
              (isActive
                ? "bg-nerava-blue text-white shadow-[0_2px_6px_rgba(19,163,226,0.35)]"
                : "bg-white text-nerava-ink/70 ring-1 ring-black/5 hover:bg-nerava-bg")
            }
          >
            {categoryLabel(category)}
          </button>
        );
      })}
    </nav>
  );
}

function categoryLabel(category: CategoryFilter): string {
  return category === "All" ? "All" : (category satisfies AppCategory);
}
