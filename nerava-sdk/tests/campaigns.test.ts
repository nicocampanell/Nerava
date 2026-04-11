// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 7 acceptance tests. Exercises CampaignsModule and OffersModule
// against a mock fetch and verifies URL construction, snake↔camel
// conversion in both directions, partner auth context, and empty-id
// guards.

import { beforeEach, describe, expect, it, vi, type MockedFunction } from "vitest";

import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import {
  CampaignsModule,
  type CampaignSummary,
} from "../src/modules/campaigns.js";
import {
  OffersModule,
  type OfferResponse,
  type OfferSummary,
} from "../src/modules/offers.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
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

function buildCampaigns(mockFetch: MockFetch): CampaignsModule {
  const auth = new AuthManager({ apiKey: TEST_API_KEY });
  const client = new NeravaClient({ auth, baseUrl: TEST_BASE_URL, fetch: mockFetch });
  return new CampaignsModule(client);
}

function buildOffers(mockFetch: MockFetch): OffersModule {
  const auth = new AuthManager({ apiKey: TEST_API_KEY });
  const client = new NeravaClient({ auth, baseUrl: TEST_BASE_URL, fetch: mockFetch });
  return new OffersModule(client);
}

function firstCall(mockFetch: MockFetch): { url: URL; init: RequestInit } {
  const call = mockFetch.mock.calls[0];
  if (!call) throw new Error("mockFetch was not called");
  return { url: new URL(String(call[0])), init: call[1] ?? {} };
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

// ===========================================================================
// Campaigns fixtures
// ===========================================================================

const CAMPAIGN_WIRE = {
  id: "camp_1",
  name: "Harker Heights Free Pizza",
  status: "active",
  reward_amount: { amount_cents: 500, currency: "USD" },
  max_per_driver: 3,
  expires_at: "2026-12-31T23:59:59Z",
  sponsor_name: "The Heights Pizzeria",
};

const CAMPAIGN_CAMEL: CampaignSummary = {
  id: "camp_1",
  name: "Harker Heights Free Pizza",
  status: "active",
  rewardAmount: { amountCents: 500, currency: "USD" },
  maxPerDriver: 3,
  expiresAt: "2026-12-31T23:59:59Z",
  sponsorName: "The Heights Pizzeria",
};

// ===========================================================================
// campaigns.getAvailable
// ===========================================================================

describe("campaigns.getAvailable", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch([CAMPAIGN_WIRE]);
  });

  it("GETs /v1/partners/campaigns with lat/lng query params and partner auth", async () => {
    const campaigns = buildCampaigns(mockFetch);
    const result = await campaigns.getAvailable({ lat: 31.0824, lng: -97.6492 });

    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(CAMPAIGN_CAMEL);

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/campaigns");
    expect(url.searchParams.get("lat")).toBe("31.0824");
    expect(url.searchParams.get("lng")).toBe("-97.6492");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
  });

  it("passes vehicle_type query param when vehicleType is supplied", async () => {
    const campaigns = buildCampaigns(mockFetch);
    await campaigns.getAvailable({
      lat: 31.0824,
      lng: -97.6492,
      vehicleType: "tesla",
    });
    const { url } = firstCall(mockFetch);
    expect(url.searchParams.get("vehicle_type")).toBe("tesla");
  });

  it("omits vehicle_type when not supplied", async () => {
    const campaigns = buildCampaigns(mockFetch);
    await campaigns.getAvailable({ lat: 31, lng: -97 });
    const { url } = firstCall(mockFetch);
    expect(url.searchParams.has("vehicle_type")).toBe(false);
  });

  it("converts nested Money fields in the response", async () => {
    const campaigns = buildCampaigns(mockFetch);
    const [first] = await campaigns.getAvailable({ lat: 0, lng: 0 });
    expect(first?.rewardAmount.amountCents).toBe(500);
    expect(first?.rewardAmount.currency).toBe("USD");
  });
});

// ===========================================================================
// campaigns.getForSession
// ===========================================================================

describe("campaigns.getForSession", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch([CAMPAIGN_WIRE]);
  });

  it("GETs /v1/partners/sessions/{id}/campaigns with partner auth", async () => {
    const campaigns = buildCampaigns(mockFetch);
    const result = await campaigns.getForSession("sess_abc");

    expect(result[0]).toEqual(CAMPAIGN_CAMEL);
    const { url } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions/sess_abc/campaigns");
  });

  it("URL-encodes session ids with reserved characters", async () => {
    const campaigns = buildCampaigns(mockFetch);
    await campaigns.getForSession("sess/with spaces");
    expect(firstCall(mockFetch).url.pathname).toBe(
      `/v1/partners/sessions/${encodeURIComponent("sess/with spaces")}/campaigns`,
    );
  });

  it("throws on empty sessionId", async () => {
    const campaigns = buildCampaigns(mockFetch);
    await expect(campaigns.getForSession("")).rejects.toThrow(/sessionId is required/);
  });
});

// ===========================================================================
// Offers fixtures
// ===========================================================================

const OFFER_WIRE = {
  id: "offer_1",
  merchant_id: "merch_1",
  merchant_name: "The Heights Pizzeria",
  title: "Free Garlic Knots",
  description: "With any pizza order",
  reward_amount: { amount_cents: 400, currency: "USD" },
  distance_meters: 30,
  walk_minutes: 1,
  status: "available",
  expires_at: "2026-04-11T06:00:00Z",
};

const OFFER_CAMEL: OfferSummary = {
  id: "offer_1",
  merchantId: "merch_1",
  merchantName: "The Heights Pizzeria",
  title: "Free Garlic Knots",
  description: "With any pizza order",
  rewardAmount: { amountCents: 400, currency: "USD" },
  distanceMeters: 30,
  walkMinutes: 1,
  status: "available",
  expiresAt: "2026-04-11T06:00:00Z",
};

const OFFER_RESPONSE_WIRE = {
  ...OFFER_WIRE,
  status: "activated",
  activated_at: "2026-04-11T04:30:00Z",
  completed_at: null,
  transaction_id: null,
};

// ===========================================================================
// offers.getForSession
// ===========================================================================

describe("offers.getForSession", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch([OFFER_WIRE]);
  });

  it("GETs /v1/partners/sessions/{id}/offers with partner auth", async () => {
    const offers = buildOffers(mockFetch);
    const result = await offers.getForSession("sess_abc");

    expect(result[0]).toEqual(OFFER_CAMEL);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions/sess_abc/offers");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
  });

  it("throws on empty sessionId", async () => {
    const offers = buildOffers(mockFetch);
    await expect(offers.getForSession("")).rejects.toThrow(/sessionId is required/);
  });
});

// ===========================================================================
// offers.activate
// ===========================================================================

describe("offers.activate", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(OFFER_RESPONSE_WIRE);
  });

  it("POSTs /v1/partners/offers/activate with snake_case body and partner auth", async () => {
    const offers = buildOffers(mockFetch);
    const result = await offers.activate({
      sessionId: "sess_abc",
      offerId: "offer_1",
    });

    const expected: OfferResponse = {
      ...OFFER_CAMEL,
      status: "activated",
      activatedAt: "2026-04-11T04:30:00Z",
      completedAt: null,
      transactionId: null,
    };
    expect(result).toEqual(expected);

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/offers/activate");
    expect(init.method).toBe("POST");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
    expect(JSON.parse(String(init.body))).toEqual({
      session_id: "sess_abc",
      offer_id: "offer_1",
    });
  });

  it("throws on empty sessionId", async () => {
    const offers = buildOffers(mockFetch);
    await expect(
      offers.activate({ sessionId: "", offerId: "offer_1" }),
    ).rejects.toThrow(/sessionId is required/);
  });

  it("throws on empty offerId", async () => {
    const offers = buildOffers(mockFetch);
    await expect(
      offers.activate({ sessionId: "sess_abc", offerId: "" }),
    ).rejects.toThrow(/offerId is required/);
  });
});

// ===========================================================================
// offers.complete
// ===========================================================================

describe("offers.complete", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch({
      ...OFFER_RESPONSE_WIRE,
      status: "completed",
      completed_at: "2026-04-11T04:45:00Z",
      transaction_id: "txn_pos_99",
    });
  });

  it("POSTs /v1/partners/offers/complete with transaction_id in body", async () => {
    const offers = buildOffers(mockFetch);
    const result = await offers.complete({
      sessionId: "sess_abc",
      offerId: "offer_1",
      transactionId: "txn_pos_99",
    });

    expect(result.status).toBe("completed");
    expect(result.completedAt).toBe("2026-04-11T04:45:00Z");
    expect(result.transactionId).toBe("txn_pos_99");

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/offers/complete");
    expect(JSON.parse(String(init.body))).toEqual({
      session_id: "sess_abc",
      offer_id: "offer_1",
      transaction_id: "txn_pos_99",
    });
  });

  it("throws on any empty required field", async () => {
    const offers = buildOffers(mockFetch);
    await expect(
      offers.complete({ sessionId: "", offerId: "o", transactionId: "t" }),
    ).rejects.toThrow(/sessionId is required/);
    await expect(
      offers.complete({ sessionId: "s", offerId: "", transactionId: "t" }),
    ).rejects.toThrow(/offerId is required/);
    await expect(
      offers.complete({ sessionId: "s", offerId: "o", transactionId: "" }),
    ).rejects.toThrow(/transactionId is required/);
  });
});
