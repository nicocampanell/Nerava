/**
 * Home screen — Nerava / EVject branding header, "Get Paid to Charge"
 * featured banner, category tabs, and the filtered app grid.
 *
 * The featured banner is static content but uses the Nerava color system
 * (navy background, gold title accent, electric-blue highlight).
 */

import { AppCard } from "../components/AppCard.js";
import { CategoryTabs } from "../components/CategoryTabs.js";
import type { AppStoreState } from "../state/store.js";

interface HomeScreenProps {
  readonly store: AppStoreState;
}

export function HomeScreen({ store }: HomeScreenProps): React.JSX.Element {
  return (
    <div className="flex h-full flex-col overflow-y-auto pb-24">
      {/* Header */}
      <header className="px-5 pt-8 pb-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
              EVject
            </div>
            <h1 className="text-xl font-bold text-nerava-ink">App Store</h1>
          </div>
          <div className="rounded-full bg-nerava-bg px-3 py-1 text-[10px] font-semibold text-nerava-ink/60">
            Powered by Nerava
          </div>
        </div>
      </header>

      {/* Featured banner */}
      <section className="px-5 pb-3">
        <div className="relative overflow-hidden rounded-3xl bg-nerava-navy p-5 text-white shadow-[0_8px_24px_rgba(11,26,60,0.25)]">
          <div className="relative z-10">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-nerava-blue">
              Featured
            </div>
            <h2 className="mt-1 text-2xl font-bold leading-tight text-nerava-gold">
              Get Paid to Charge
            </h2>
            <p className="mt-1 text-xs text-white/80">
              Install Nerava Wallet and start earning credits at every session.
            </p>
          </div>
          {/* Decorative gradient blob */}
          <div
            aria-hidden="true"
            className="absolute -right-12 -top-12 h-48 w-48 rounded-full bg-nerava-blue/30 blur-3xl"
          />
          <div
            aria-hidden="true"
            className="absolute -left-10 -bottom-16 h-40 w-40 rounded-full bg-nerava-gold/20 blur-3xl"
          />
        </div>
      </section>

      {/* Category tabs */}
      <CategoryTabs
        selected={store.selectedCategory}
        onSelect={store.setSelectedCategory}
      />

      {/* App grid — single column at 390px, tiles stack vertically */}
      <section className="flex flex-col gap-2 px-4 pt-1">
        {store.visibleApps.map((app) => (
          <AppCard
            key={app.id}
            app={app}
            enabled={store.enabledApps.has(app.id)}
            onClick={(): void => store.openAppDetail(app.id)}
          />
        ))}
        {store.visibleApps.length === 0 ? (
          <div className="rounded-2xl bg-white p-6 text-center text-xs text-nerava-ink/50 ring-1 ring-black/5">
            No apps in this category yet.
          </div>
        ) : null}
      </section>
    </div>
  );
}
