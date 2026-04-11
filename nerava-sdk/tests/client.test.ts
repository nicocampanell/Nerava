// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 2 acceptance test. Confirms that `NeravaClient.request()` builds the
// correct URL and injects the correct headers based on the `auth` context
// (`partner` vs `driver`), and that the `AuthManager` + `TokenStore`
// plumbing around it works the way the contract in docs says it does.

import { beforeEach, describe, expect, it, vi, type MockedFunction } from "vitest";

import {
  AuthManager,
  InMemoryTokenStore,
  type TokenStore,
} from "../src/auth.js";
import { NeravaClient } from "../src/client.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
const TEST_DRIVER_JWT = "eyJ.test.jwt.payload";
const TEST_BASE_URL = "http://localhost:3001";

/** Narrow type alias for a mock typed against the native `fetch` signature. */
type MockFetch = MockedFunction<typeof fetch>;

/**
 * Builds a mock fetch that returns a canned 200 JSON response. A fresh
 * `Response` is built on every call — `Response.body` can only be consumed
 * once per instance per the Fetch spec, so `mockResolvedValue(staticResponse)`
 * breaks as soon as a test invokes the client twice. `mockImplementation`
 * avoids that by constructing a new Response each call.
 */
function makeMockFetch(responseBody: unknown = { ok: true }): MockFetch {
  return vi.fn<typeof fetch>().mockImplementation(async () => {
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

/**
 * Helper: pull the headers object out of the N-th fetch call. Cast via the
 * narrowed type so tests don't have to litter `as any`.
 */
function headersOf(mockFetch: MockFetch, callIndex: number): Record<string, string> {
  const call = mockFetch.mock.calls[callIndex];
  if (!call) {
    throw new Error(`headersOf: no fetch call at index ${callIndex}`);
  }
  const init = call[1];
  if (!init?.headers) {
    throw new Error(`headersOf: call ${callIndex} had no headers`);
  }
  return init.headers as Record<string, string>;
}

function urlOf(mockFetch: MockFetch, callIndex: number): string {
  const call = mockFetch.mock.calls[callIndex];
  if (!call) {
    throw new Error(`urlOf: no fetch call at index ${callIndex}`);
  }
  return String(call[0]);
}

// ===========================================================================
// AuthManager construction
// ===========================================================================

describe("AuthManager construction", () => {
  it("accepts a valid nrv_pk_* key", () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    expect(auth.getPartnerKey()).toBe(TEST_API_KEY);
  });

  it("rejects an empty apiKey", () => {
    expect(() => new AuthManager({ apiKey: "" })).toThrow(/required/);
  });

  it("rejects an apiKey with the wrong prefix", () => {
    expect(() => new AuthManager({ apiKey: "sk_test_abcdef1234" })).toThrow(
      /invalid apiKey format/,
    );
  });

  it("rejects an apiKey that is too short after the prefix", () => {
    expect(() => new AuthManager({ apiKey: "nrv_pk_short" })).toThrow(
      /invalid apiKey format/,
    );
  });

  it("accepts an optional driverToken at construction and seeds the default store", async () => {
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    // InMemoryTokenStore.set is synchronous, so the token is available on
    // the very next tick.
    expect(await auth.getDriverToken()).toBe(TEST_DRIVER_JWT);
  });

  it("uses an injected custom TokenStore when provided", async () => {
    const customStore = new InMemoryTokenStore();
    customStore.set("preset-from-custom-store");
    const auth = new AuthManager({ apiKey: TEST_API_KEY, tokenStore: customStore });
    expect(await auth.getDriverToken()).toBe("preset-from-custom-store");
  });
});

// ===========================================================================
// AuthManager token lifecycle
// ===========================================================================

describe("AuthManager token lifecycle", () => {
  it("setDriverToken persists through getDriverToken", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    expect(await auth.getDriverToken()).toBeNull();

    await auth.setDriverToken("first-token");
    expect(await auth.getDriverToken()).toBe("first-token");

    await auth.setDriverToken("second-token");
    expect(await auth.getDriverToken()).toBe("second-token");
  });

  it("setDriverToken rejects empty strings", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    await expect(auth.setDriverToken("")).rejects.toThrow(/non-empty/);
  });

  it("clearDriverToken removes a previously-set token", async () => {
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    expect(await auth.getDriverToken()).toBe(TEST_DRIVER_JWT);

    await auth.clearDriverToken();
    expect(await auth.getDriverToken()).toBeNull();
  });
});

// ===========================================================================
// NeravaClient header injection — the core Step 2 acceptance test
// ===========================================================================

describe("NeravaClient header injection by auth context", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch();
  });

  it("partner-scope request carries X-Partner-Key and no Authorization", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request<{ ok: true }>({
      auth: "partner",
      path: "/v1/partners/sessions",
    });

    expect(mockFetch).toHaveBeenCalledOnce();
    const headers = headersOf(mockFetch, 0);
    expect(headers["X-Partner-Key"]).toBe(TEST_API_KEY);
    expect(headers["Authorization"]).toBeUndefined();
    expect(headers["Accept"]).toBe("application/json");
  });

  it("driver-scope request carries Authorization: Bearer and no X-Partner-Key", async () => {
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request<{ ok: true }>({
      auth: "driver",
      path: "/v1/wallet/balance",
    });

    expect(mockFetch).toHaveBeenCalledOnce();
    const headers = headersOf(mockFetch, 0);
    expect(headers["Authorization"]).toBe(`Bearer ${TEST_DRIVER_JWT}`);
    expect(headers["X-Partner-Key"]).toBeUndefined();
  });

  it("driver-scope request without a token throws a clear error", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await expect(
      client.request({ auth: "driver", path: "/v1/wallet/balance" }),
    ).rejects.toThrow(/requires a driver JWT/);

    // Importantly: the client must NOT have called fetch at all. The guard
    // fires before any network traffic.
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("driver requests see updated tokens after setDriverToken", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    await auth.setDriverToken("token-A");
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request({ auth: "driver", path: "/v1/wallet/balance" });
    expect(headersOf(mockFetch, 0)["Authorization"]).toBe("Bearer token-A");

    await auth.setDriverToken("token-B");
    await client.request({ auth: "driver", path: "/v1/wallet/balance" });
    expect(headersOf(mockFetch, 1)["Authorization"]).toBe("Bearer token-B");
  });

  it("a custom async TokenStore is awaited per request", async () => {
    const asyncStore: TokenStore = {
      get: vi
        .fn<() => Promise<string | null>>()
        .mockResolvedValue("async-store-token"),
      set: vi.fn<(t: string | null) => Promise<void>>().mockResolvedValue(undefined),
    };
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      tokenStore: asyncStore,
    });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request({ auth: "driver", path: "/v1/wallet/balance" });
    expect(asyncStore.get).toHaveBeenCalledOnce();
    expect(headersOf(mockFetch, 0)["Authorization"]).toBe(
      "Bearer async-store-token",
    );
  });
});

// ===========================================================================
// NeravaClient URL + method + body + query behavior
// ===========================================================================

describe("NeravaClient URL and body behavior", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch();
  });

  it("normalizes the base URL and path to avoid double slashes", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    // Base URL with trailing slash, path without leading slash — both should
    // normalize to exactly one slash between them.
    const client = new NeravaClient({
      auth,
      baseUrl: "http://localhost:3001/",
      fetch: mockFetch,
    });

    await client.request({ auth: "partner", path: "v1/partners/sessions" });
    expect(urlOf(mockFetch, 0)).toBe("http://localhost:3001/v1/partners/sessions");
  });

  it("appends query parameters, stringifying booleans and numbers", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request({
      auth: "partner",
      path: "/v1/partners/campaigns",
      query: { lat: 31.0824, lng: -97.6492, active: true },
    });

    const url = new URL(urlOf(mockFetch, 0));
    expect(url.pathname).toBe("/v1/partners/campaigns");
    expect(url.searchParams.get("lat")).toBe("31.0824");
    expect(url.searchParams.get("lng")).toBe("-97.6492");
    expect(url.searchParams.get("active")).toBe("true");
  });

  it("POST with a body sets Content-Type and serializes JSON", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request({
      auth: "partner",
      method: "POST",
      path: "/v1/partners/sessions",
      body: { vehicleId: "v123", chargerId: "c456", lat: 31.08, lng: -97.65 },
    });

    const call = mockFetch.mock.calls[0]!;
    const init = call[1] as RequestInit;
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe(
      JSON.stringify({ vehicleId: "v123", chargerId: "c456", lat: 31.08, lng: -97.65 }),
    );
  });

  it("GET without a body omits Content-Type", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await client.request({ auth: "partner", path: "/v1/partners/sessions" });
    const headers = headersOf(mockFetch, 0);
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("204 No Content response resolves to undefined without parsing", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    mockFetch = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204, statusText: "No Content" }));
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    const result = await client.request<void>({
      auth: "partner",
      method: "DELETE",
      path: "/v1/partners/sessions/s1",
    });
    expect(result).toBeUndefined();
  });

  it("throws a descriptive error on non-2xx responses", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    mockFetch = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ code: "SESSION_NOT_FOUND", message: "nope" }), {
        status: 404,
        statusText: "Not Found",
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await expect(
      client.request({ auth: "partner", path: "/v1/partners/sessions/bad" }),
    ).rejects.toThrow(/HTTP 404/);
  });

  it("throws a descriptive error on network failures", async () => {
    const auth = new AuthManager({ apiKey: TEST_API_KEY });
    mockFetch = vi.fn<typeof fetch>().mockRejectedValue(new Error("ECONNREFUSED"));
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });

    await expect(
      client.request({ auth: "partner", path: "/v1/partners/sessions" }),
    ).rejects.toThrow(/network error/);
  });
});
