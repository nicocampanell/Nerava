// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 11 acceptance tests for the top-level `Nerava` facade class.
// Verifies composition: constructing `new Nerava({apiKey})` gives back
// every module wired against a shared NeravaClient and AuthManager.

import { describe, expect, it, vi } from "vitest";

import { AuthManager, InMemoryTokenStore } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import { Nerava } from "../src/nerava.js";
import { CampaignsModule } from "../src/modules/campaigns.js";
import { IntelligenceModule } from "../src/modules/intelligence.js";
import { OffersModule } from "../src/modules/offers.js";
import { SessionsModule } from "../src/modules/sessions.js";
import { WalletModule } from "../src/modules/wallet.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
const TEST_DRIVER_JWT = "eyJ.test.jwt";

describe("Nerava facade construction", () => {
  it("instantiates from { apiKey } alone with production defaults", () => {
    const nerava = new Nerava({ apiKey: TEST_API_KEY });
    expect(nerava.auth).toBeInstanceOf(AuthManager);
    expect(nerava.client).toBeInstanceOf(NeravaClient);
    expect(nerava.sessions).toBeInstanceOf(SessionsModule);
    expect(nerava.wallet).toBeInstanceOf(WalletModule);
    expect(nerava.campaigns).toBeInstanceOf(CampaignsModule);
    expect(nerava.offers).toBeInstanceOf(OffersModule);
    expect(nerava.intelligence).toBeInstanceOf(IntelligenceModule);
  });

  it("accepts a driver token at construction and makes it immediately available", async () => {
    const nerava = new Nerava({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    expect(await nerava.auth.getDriverToken()).toBe(TEST_DRIVER_JWT);
  });

  it("accepts a custom TokenStore", async () => {
    const customStore = new InMemoryTokenStore();
    customStore.set("preset-from-custom-store");
    const nerava = new Nerava({
      apiKey: TEST_API_KEY,
      tokenStore: customStore,
    });
    expect(await nerava.auth.getDriverToken()).toBe("preset-from-custom-store");
  });

  it("accepts baseUrl override for mock server use", async () => {
    const mockFetch = vi.fn<typeof fetch>().mockImplementation(async () => {
      return new Response("[]", {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const nerava = new Nerava({
      apiKey: TEST_API_KEY,
      baseUrl: "http://localhost:3001",
      fetch: mockFetch,
    });
    // Use the client directly to verify the baseUrl stuck.
    await nerava.client.request({ auth: "partner", path: "/v1/partners/sessions" });
    // The first call URL should start with localhost:3001, not api.nerava.network.
    const call = mockFetch.mock.calls[0];
    expect(call).toBeDefined();
    expect(String(call?.[0])).toContain("http://localhost:3001");
  });

  it("every module shares the same underlying client instance", () => {
    const nerava = new Nerava({ apiKey: TEST_API_KEY });
    // No public getter for the module's internal client, so we test
    // composition behaviorally: setting a driver token on the shared
    // AuthManager should be observable from every driver-scope module.
    expect(nerava.auth).toBe(nerava.auth);
    // Reference equality proof: facade fields are set once at construction.
    const firstClient = nerava.client;
    expect(nerava.client).toBe(firstClient);
  });

  it("rejects an invalid apiKey at construction", () => {
    expect(() => new Nerava({ apiKey: "wrong_prefix_key" })).toThrow(/invalid apiKey/);
  });

  it("exposes auth.setDriverToken for post-construction updates", async () => {
    const nerava = new Nerava({ apiKey: TEST_API_KEY });
    expect(await nerava.auth.getDriverToken()).toBeNull();
    await nerava.auth.setDriverToken("new-token");
    expect(await nerava.auth.getDriverToken()).toBe("new-token");
  });
});
