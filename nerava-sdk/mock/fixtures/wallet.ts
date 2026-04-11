/**
 * Mock fixtures for the wallet endpoints. Snake_case to mirror the
 * real backend wire format.
 */

export const mockWalletBalance = {
  driver_id: "drv_mock_1",
  balance: { amount_cents: 2500, currency: "USD" },
  pending_balance: { amount_cents: 0, currency: "USD" },
  lifetime_earned: { amount_cents: 15000, currency: "USD" },
  nova_balance: 140,
};

export const mockWalletTransactions: readonly Record<string, unknown>[] = [
  {
    id: "txn_mock_1",
    driver_id: "drv_mock_1",
    type: "credit",
    amount: { amount_cents: 500, currency: "USD" },
    balance_after: { amount_cents: 2500, currency: "USD" },
    reference_type: "campaign_grant",
    reference_id: "camp_mock_1",
    description: "session bonus",
    created_at: "2026-04-11T04:30:00Z",
  },
  {
    id: "txn_mock_2",
    driver_id: "drv_mock_1",
    type: "debit",
    amount: { amount_cents: 400, currency: "USD" },
    balance_after: { amount_cents: 2000, currency: "USD" },
    reference_type: "merchant_redemption",
    reference_id: "merch_mock_1",
    description: "The Heights Pizzeria — free garlic knots",
    created_at: "2026-04-11T04:45:00Z",
  },
];

export const mockPayout = {
  id: "pay_mock_1",
  driver_id: "drv_mock_1",
  amount: { amount_cents: 2000, currency: "USD" },
  fee: { amount_cents: 25, currency: "USD" },
  status: "pending",
  provider_reference: null,
  created_at: "2026-04-11T04:50:00Z",
};
