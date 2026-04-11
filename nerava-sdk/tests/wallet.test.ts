// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 6 acceptance tests. Exercises WalletModule against a mock fetch
// and verifies driver auth context, camelCase→snake_case body conversion,
// Money field flattening (amountCents + currency at the wire), the
// limit>200 guard, and empty-id guards.

import { beforeEach, describe, expect, it, vi, type MockedFunction } from "vitest";

import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import { NeravaError } from "../src/errors.js";
import {
  WalletModule,
  type PayoutResponse,
  type WalletBalance,
  type WalletTransaction,
} from "../src/modules/wallet.js";
import { usd } from "../src/types.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
const TEST_DRIVER_JWT = "eyJ.test.jwt";
const TEST_BASE_URL = "http://localhost:3001";

type MockFetch = MockedFunction<typeof fetch>;

function makeMockFetch(responseBody: unknown): MockFetch {
  return vi.fn<typeof fetch>().mockImplementation(async () => {
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function buildWalletModule(
  mockFetch: MockFetch,
  opts: { withDriverToken?: boolean } = { withDriverToken: true },
): WalletModule {
  const auth = new AuthManager({
    apiKey: TEST_API_KEY,
    ...(opts.withDriverToken ? { driverToken: TEST_DRIVER_JWT } : {}),
  });
  const client = new NeravaClient({
    auth,
    baseUrl: TEST_BASE_URL,
    fetch: mockFetch,
  });
  return new WalletModule(client);
}

function firstCall(mockFetch: MockFetch): { url: URL; init: RequestInit } {
  const call = mockFetch.mock.calls[0];
  if (!call) throw new Error("mockFetch was not called");
  return { url: new URL(String(call[0])), init: call[1] ?? {} };
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

// Backend-shaped (snake_case) fixtures — mirror FastAPI wire output.
// The SDK converts these to camelCase via `camelCaseKeys()` inside each
// module method, so consumer assertions use the `FIXTURE_*` camelCase
// constants below.

const FIXTURE_BALANCE_WIRE = {
  driver_id: "drv_abc",
  balance: { amount_cents: 2500, currency: "USD" },
  pending_balance: { amount_cents: 0, currency: "USD" },
  lifetime_earned: { amount_cents: 15000, currency: "USD" },
  nova_balance: 140,
};

const FIXTURE_BALANCE: WalletBalance = {
  driverId: "drv_abc",
  balance: { amountCents: 2500, currency: "USD" },
  pendingBalance: { amountCents: 0, currency: "USD" },
  lifetimeEarned: { amountCents: 15000, currency: "USD" },
  novaBalance: 140,
};

const FIXTURE_TRANSACTION_WIRE = {
  id: "txn_1",
  driver_id: "drv_abc",
  type: "credit",
  amount: { amount_cents: 500, currency: "USD" },
  balance_after: { amount_cents: 3000, currency: "USD" },
  reference_type: "campaign_grant",
  reference_id: "camp_1",
  description: null,
  created_at: "2026-04-11T04:30:00Z",
};

const FIXTURE_TRANSACTION: WalletTransaction = {
  id: "txn_1",
  driverId: "drv_abc",
  type: "credit",
  amount: { amountCents: 500, currency: "USD" },
  balanceAfter: { amountCents: 3000, currency: "USD" },
  referenceType: "campaign_grant",
  referenceId: "camp_1",
  description: null,
  createdAt: "2026-04-11T04:30:00Z",
};

const FIXTURE_PAYOUT_WIRE = {
  id: "pay_1",
  driver_id: "drv_abc",
  amount: { amount_cents: 2500, currency: "USD" },
  fee: { amount_cents: 25, currency: "USD" },
  status: "pending",
  provider_reference: null,
  created_at: "2026-04-11T04:30:00Z",
};

const FIXTURE_PAYOUT: PayoutResponse = {
  id: "pay_1",
  driverId: "drv_abc",
  amount: { amountCents: 2500, currency: "USD" },
  fee: { amountCents: 25, currency: "USD" },
  status: "pending",
  providerReference: null,
  createdAt: "2026-04-11T04:30:00Z",
};

// ===========================================================================
// getBalance
// ===========================================================================

describe("wallet.getBalance", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(FIXTURE_BALANCE_WIRE);
  });

  it("GETs /v1/wallet/balance with driver JWT and converts snake→camel on response", async () => {
    const wallet = buildWalletModule(mockFetch);
    const balance = await wallet.getBalance("drv_abc");

    // Backend returned snake_case wire fixture; SDK converts to camelCase.
    expect(balance).toEqual(FIXTURE_BALANCE);
    // Spot-check nested Money field conversion: `balance.amount_cents` →
    // `balance.amountCents`, recursively through `camelCaseKeys()`.
    expect(balance.balance.amountCents).toBe(2500);
    expect(balance.lifetimeEarned.amountCents).toBe(15000);
    expect(balance.pendingBalance.amountCents).toBe(0);
    expect(balance.novaBalance).toBe(140);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/wallet/balance");
    expect(url.searchParams.get("driver_id")).toBe("drv_abc");
    expect(headersOf(init)["Authorization"]).toBe(`Bearer ${TEST_DRIVER_JWT}`);
    expect(headersOf(init)["X-Partner-Key"]).toBeUndefined();
  });

  it("throws on empty driverId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(wallet.getBalance("")).rejects.toThrow(/driverId is required/);
  });

  it("throws NO_DRIVER_TOKEN when no driver JWT is set", async () => {
    const wallet = buildWalletModule(mockFetch, { withDriverToken: false });
    try {
      await wallet.getBalance("drv_abc");
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(NeravaError);
      expect((err as NeravaError).code).toBe("NO_DRIVER_TOKEN");
    }
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// getTransactions
// ===========================================================================

describe("wallet.getTransactions", () => {
  const PAGE_FIXTURE_WIRE = {
    items: [FIXTURE_TRANSACTION_WIRE],
    next_cursor: "c_next",
  };

  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(PAGE_FIXTURE_WIRE);
  });

  it("GETs /v1/wallet/transactions with driver auth and converts snake→camel on response", async () => {
    const wallet = buildWalletModule(mockFetch);
    const page = await wallet.getTransactions("drv_abc");

    expect(page.items).toHaveLength(1);
    expect(page.nextCursor).toBe("c_next");
    expect(page.items[0]).toEqual(FIXTURE_TRANSACTION);
    expect(page.items[0]?.driverId).toBe("drv_abc");
    expect(page.items[0]?.amount.amountCents).toBe(500);

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/wallet/transactions");
    expect(url.searchParams.get("driver_id")).toBe("drv_abc");
    expect(headersOf(init)["Authorization"]).toBe(`Bearer ${TEST_DRIVER_JWT}`);
  });

  it("serializes full filter set as snake_case query params", async () => {
    const wallet = buildWalletModule(mockFetch);
    await wallet.getTransactions("drv_abc", {
      cursor: "c_xyz",
      limit: 100,
      type: "debit",
      since: "2026-04-01T00:00:00Z",
      until: "2026-04-11T00:00:00Z",
    });

    const { url } = firstCall(mockFetch);
    expect(url.searchParams.get("driver_id")).toBe("drv_abc");
    expect(url.searchParams.get("cursor")).toBe("c_xyz");
    expect(url.searchParams.get("limit")).toBe("100");
    expect(url.searchParams.get("type")).toBe("debit");
    expect(url.searchParams.get("since")).toBe("2026-04-01T00:00:00Z");
    expect(url.searchParams.get("until")).toBe("2026-04-11T00:00:00Z");
  });

  it("guards against limit > 200", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(wallet.getTransactions("drv_abc", { limit: 201 })).rejects.toThrow(
      /limit must be ≤ 200/,
    );
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("accepts limit of exactly 200 (boundary)", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(
      wallet.getTransactions("drv_abc", { limit: 200 }),
    ).resolves.toBeDefined();
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("throws on empty driverId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(wallet.getTransactions("")).rejects.toThrow(/driverId is required/);
  });
});

// ===========================================================================
// credit
// ===========================================================================

describe("wallet.credit", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(FIXTURE_TRANSACTION_WIRE);
  });

  it("POSTs /v1/wallet/credit with flattened Money fields and driver auth", async () => {
    const wallet = buildWalletModule(mockFetch);
    const result = await wallet.credit({
      driverId: "drv_abc",
      amount: usd(500),
      campaignId: "camp_1",
      description: "session bonus",
    });

    expect(result).toEqual(FIXTURE_TRANSACTION);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/wallet/credit");
    expect(init.method).toBe("POST");
    expect(headersOf(init)["Authorization"]).toBe(`Bearer ${TEST_DRIVER_JWT}`);

    // Money is flattened into amount_cents + currency at the wire — NOT
    // sent as a nested `{amount: {amountCents, currency}}` object. This
    // matches the backend's schema which has separate columns.
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      driver_id: "drv_abc",
      amount_cents: 500,
      currency: "USD",
      campaign_id: "camp_1",
      description: "session bonus",
    });
  });

  it("omits optional referenceId and description when not provided", async () => {
    const wallet = buildWalletModule(mockFetch);
    await wallet.credit({
      driverId: "drv_abc",
      amount: usd(1000),
      campaignId: "camp_2",
    });

    const body = JSON.parse(String(firstCall(mockFetch).init.body));
    expect(body).not.toHaveProperty("reference_id");
    expect(body).not.toHaveProperty("description");
  });

  it("throws on empty driverId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(
      wallet.credit({ driverId: "", amount: usd(100), campaignId: "c_1" }),
    ).rejects.toThrow(/driverId is required/);
  });

  it("throws on empty campaignId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(
      wallet.credit({ driverId: "drv_1", amount: usd(100), campaignId: "" }),
    ).rejects.toThrow(/campaignId is required/);
  });
});

// ===========================================================================
// debit
// ===========================================================================

describe("wallet.debit", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch({ ...FIXTURE_TRANSACTION_WIRE, type: "debit" });
  });

  it("POSTs /v1/wallet/debit with merchant reference and driver auth", async () => {
    const wallet = buildWalletModule(mockFetch);
    await wallet.debit({
      driverId: "drv_abc",
      amount: usd(750),
      merchantId: "merch_1",
    });

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/wallet/debit");
    expect(init.method).toBe("POST");

    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      driver_id: "drv_abc",
      amount_cents: 750,
      currency: "USD",
      merchant_id: "merch_1",
    });
  });

  it("throws on empty merchantId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(
      wallet.debit({ driverId: "drv_1", amount: usd(100), merchantId: "" }),
    ).rejects.toThrow(/merchantId is required/);
  });

  it("surfaces backend INSUFFICIENT_BALANCE as NeravaError", async () => {
    const failFetch = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ code: "INSUFFICIENT_BALANCE", message: "balance too low" }),
        { status: 400, headers: { "content-type": "application/json" } },
      ),
    );
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    const client = new NeravaClient({ auth, baseUrl: TEST_BASE_URL, fetch: failFetch });
    const wallet = new WalletModule(client);

    try {
      await wallet.debit({
        driverId: "drv_1",
        amount: usd(9999),
        merchantId: "merch_1",
      });
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(NeravaError);
      expect((err as NeravaError).code).toBe("INSUFFICIENT_BALANCE");
      expect((err as NeravaError).status).toBe(400);
    }
  });
});

// ===========================================================================
// requestPayout
// ===========================================================================

describe("wallet.requestPayout", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(FIXTURE_PAYOUT_WIRE);
  });

  it("POSTs /v1/wallet/payout with driver_id only (amount is server-determined)", async () => {
    const wallet = buildWalletModule(mockFetch);
    const result = await wallet.requestPayout("drv_abc");

    expect(result).toEqual(FIXTURE_PAYOUT);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/wallet/payout");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ driver_id: "drv_abc" });
    expect(headersOf(init)["Authorization"]).toBe(`Bearer ${TEST_DRIVER_JWT}`);
  });

  it("throws on empty driverId", async () => {
    const wallet = buildWalletModule(mockFetch);
    await expect(wallet.requestPayout("")).rejects.toThrow(/driverId is required/);
  });
});
