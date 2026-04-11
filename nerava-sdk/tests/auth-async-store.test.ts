// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Locks in the behavior documented on `AuthManagerConfig.driverToken`:
// when a custom async `TokenStore` is supplied, the constructor fires
// a void-discarded promise to set the initial token. Callers who need
// deterministic behavior against async stores must either await
// `setDriverToken()` after construction, or ensure their store's
// initial `set()` resolves before any driver-scope request.
//
// This test proves the race window exists so partners reading the
// test suite can see the exact failure mode the JSDoc warning is
// describing — they can then decide how to handle it.

import { describe, expect, it, vi } from "vitest";

import { AuthManager, type TokenStore } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import { NeravaError } from "../src/errors.js";

const TEST_API_KEY = "nrv_pk_asyncstoretest12345";
const TEST_BASE_URL = "http://localhost:3001";

describe("AuthManager constructor driverToken + async TokenStore race window", () => {
  it("throws NO_DRIVER_TOKEN when the async store.set() hasn't resolved yet", async () => {
    // Craft an async store whose `set()` returns a never-resolving promise
    // until we manually resolve it. Before that resolution, `get()`
    // returns `null` — simulating a real database or Redis roundtrip
    // that hasn't completed when the first driver-scope request fires.
    let resolveSet: () => void = () => undefined;
    const deferredSet = new Promise<void>((resolve) => {
      resolveSet = resolve;
    });

    const asyncStore: TokenStore = {
      get: vi.fn<() => Promise<string | null>>().mockResolvedValue(null),
      set: vi.fn<(t: string | null) => Promise<void>>().mockImplementation(
        () => deferredSet,
      ),
    };

    // Constructor fires `void this.#tokenStore.set(config.driverToken)`.
    // The set() call above returns `deferredSet`, which is pending,
    // so the token is not yet persisted.
    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: "eyJ.should.be.set.but.isnt.yet",
      tokenStore: asyncStore,
    });

    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: vi.fn<typeof fetch>().mockImplementation(async () => {
        // This fetch mock should NEVER be called — the guard fires
        // first because getDriverToken returns null (the async set
        // hasn't completed, so get() still returns null).
        return new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    });

    // This is the race failure mode the JSDoc warns about:
    // `get()` returns null → #buildHeaders throws NO_DRIVER_TOKEN
    // → request() rejects BEFORE ever hitting fetch.
    try {
      await client.request({ auth: "driver", path: "/v1/wallet/balance" });
      expect.fail("should have thrown NO_DRIVER_TOKEN");
    } catch (err) {
      expect(err).toBeInstanceOf(NeravaError);
      expect((err as NeravaError).code).toBe("NO_DRIVER_TOKEN");
    }

    // Unblock the deferred set() so Vitest can garbage-collect the
    // pending promise — keeps the test deterministic and avoids a
    // post-test warning about pending promises.
    resolveSet();
    await deferredSet;
  });

  it("awaiting setDriverToken() explicitly avoids the race", async () => {
    // The documented workaround: call setDriverToken() after
    // construction and `await` it. This test proves the workaround works.
    let storedToken: string | null = null;
    const asyncStore: TokenStore = {
      get: async () => storedToken,
      set: async (token: string | null) => {
        storedToken = token;
      },
    };

    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      tokenStore: asyncStore,
    });

    // Construct without driverToken; explicitly set + await.
    await auth.setDriverToken("eyJ.explicit.set");

    expect(await auth.getDriverToken()).toBe("eyJ.explicit.set");

    // Now a driver-scope request should succeed without the race.
    const mockFetch = vi.fn<typeof fetch>().mockImplementation(async () => {
      return new Response(
        JSON.stringify({
          driver_id: "drv_1",
          balance: { amount_cents: 100, currency: "USD" },
          pending_balance: { amount_cents: 0, currency: "USD" },
          lifetime_earned: { amount_cents: 500, currency: "USD" },
          nova_balance: 0,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    const client = new NeravaClient({
      auth,
      baseUrl: TEST_BASE_URL,
      fetch: mockFetch,
    });
    await expect(
      client.request({ auth: "driver", path: "/v1/wallet/balance" }),
    ).resolves.toBeDefined();
    expect(mockFetch).toHaveBeenCalledOnce();
    const headers = (mockFetch.mock.calls[0]?.[1]?.headers ?? {}) as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer eyJ.explicit.set");
  });
});
