/**
 * Mock fixtures for the campaigns endpoints. Snake_case to mirror
 * the real backend wire format.
 */

export const mockCampaigns: readonly Record<string, unknown>[] = [
  {
    id: "camp_mock_heights_pizza",
    name: "Harker Heights Free Pizza",
    status: "active",
    reward_amount: { amount_cents: 500, currency: "USD" },
    max_per_driver: 3,
    expires_at: "2026-12-31T23:59:59Z",
    sponsor_name: "The Heights Pizzeria",
  },
  {
    id: "camp_mock_evject_discount",
    name: "EVject Adapter Discount",
    status: "active",
    reward_amount: { amount_cents: 1000, currency: "USD" },
    max_per_driver: 1,
    expires_at: null,
    sponsor_name: "EVject",
  },
];
