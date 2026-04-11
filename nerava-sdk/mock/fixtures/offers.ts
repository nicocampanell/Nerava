/**
 * Mock fixtures for the offers endpoints. Snake_case to mirror the
 * real backend wire format.
 */

export const mockOffers: readonly Record<string, unknown>[] = [
  {
    id: "offer_mock_1",
    merchant_id: "merch_mock_heights",
    merchant_name: "The Heights Pizzeria",
    title: "Free Garlic Knots",
    description: "With any pizza order",
    reward_amount: { amount_cents: 400, currency: "USD" },
    distance_meters: 30,
    walk_minutes: 1,
    status: "available",
    expires_at: "2026-04-11T06:00:00Z",
  },
];

export const mockOfferActivated = {
  ...mockOffers[0],
  status: "activated",
  activated_at: "2026-04-11T04:30:00Z",
  completed_at: null,
  transaction_id: null,
};

export const mockOfferCompleted = {
  ...mockOffers[0],
  status: "completed",
  activated_at: "2026-04-11T04:30:00Z",
  completed_at: "2026-04-11T04:45:00Z",
  transaction_id: "txn_partner_pos_99",
};
