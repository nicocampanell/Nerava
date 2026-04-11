/**
 * Settings screen — placeholder per the Step 12 prompt spec.
 *
 * Shows a few cosmetic settings rows (notifications, privacy, about)
 * that don't actually do anything yet. When the Nerava backend adds
 * real settings these rows will wire up to actual state.
 */

export function SettingsScreen(): React.JSX.Element {
  return (
    <div className="flex h-full flex-col overflow-y-auto pb-24">
      <header className="px-5 pt-8 pb-3">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
          EVject
        </div>
        <h1 className="text-xl font-bold text-nerava-ink">Settings</h1>
      </header>

      <section className="flex flex-col gap-2 px-4">
        <SettingsGroup title="Preferences">
          <SettingsRow label="Notifications" value="On" />
          <SettingsRow label="Default category" value="All" />
          <SettingsRow label="Currency" value="USD" />
        </SettingsGroup>

        <SettingsGroup title="Privacy">
          <SettingsRow label="Share anonymized telemetry" value="On" />
          <SettingsRow label="Show data requests" value="On" />
        </SettingsGroup>

        <SettingsGroup title="About">
          <SettingsRow label="Version" value="0.1.0" />
          <SettingsRow label="Powered by" value="Nerava" />
        </SettingsGroup>
      </section>
    </div>
  );
}

function SettingsGroup({
  title,
  children,
}: {
  readonly title: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="mt-2">
      <h2 className="mb-1.5 px-2 text-[10px] font-semibold uppercase tracking-wider text-nerava-ink/50">
        {title}
      </h2>
      <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-black/5">
        {children}
      </div>
    </div>
  );
}

function SettingsRow({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between px-4 py-3 text-sm [&:not(:last-child)]:border-b [&:not(:last-child)]:border-black/5">
      <span className="text-nerava-ink">{label}</span>
      <span className="text-nerava-ink/50">{value}</span>
    </div>
  );
}
