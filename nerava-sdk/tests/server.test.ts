// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Smoke tests for the mock HTTP server. Spawns an in-process server on
// an OS-assigned port (port 0), exercises one route from each major
// group through the real SDK primitives, and tears down cleanly.
// Proves the full wire path (URL construction, auth header, request
// body serialization, response body parsing + case conversion) works
// against a real HTTP server, not just vi.fn() mocks.

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { startMockServer } from "../mock/server.js";
import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import { CampaignsModule } from "../src/modules/campaigns.js";
import { OffersModule } from "../src/modules/offers.js";
import { SessionsModule } from "../src/modules/sessions.js";
import { WalletModule } from "../src/modules/wallet.js";

const TEST_API_KEY = "nrv_pk_smoketest1234567890ab";
const TEST_DRIVER_JWT = "eyJ.smoke.test.jwt";

describe("mock server smoke tests", () => {
  let stop: () => Promise<void>;
  let baseUrl: string;
  let sessions: SessionsModule;
  let wallet: WalletModule;
  let campaigns: CampaignsModule;
  let offers: OffersModule;

  beforeAll(async () => {
    const spawned = await startMockServer(0); // 0 = OS-assigned port
    stop = spawned.stop;
    baseUrl = `http://localhost:${spawned.port}`;

    const auth = new AuthManager({
      apiKey: TEST_API_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    const client = new NeravaClient({ auth, baseUrl });
    sessions = new SessionsModule(client);
    wallet = new WalletModule(client);
    campaigns = new CampaignsModule(client);
    offers = new OffersModule(client);
  });

  afterAll(async () => {
    await stop();
  });

  it("POST /v1/partners/sessions (sessions.submit) round-trips through the server", async () => {
    const session = await sessions.submit({
      vehicleId: "v_smoke",
      chargerId: "c_smoke",
      lat: 31.0824,
      lng: -97.6492,
    });
    // camelCase conversion check — session.vehicleId must exist.
    expect(session.vehicleId).toBeTruthy();
    expect(session.id).toBeTruthy();
  });

  it("GET /v1/wallet/balance (wallet.getBalance) uses driver JWT", async () => {
    const balance = await wallet.getBalance("drv_smoke");
    expect(balance.driverId).toBeTruthy();
    expect(balance.balance.amountCents).toBeGreaterThanOrEqual(0);
    // Nested Money conversion check.
    expect(balance.balance.currency).toBe("USD");
  });

  it("GET /v1/partners/campaigns (campaigns.getAvailable) returns an array", async () => {
    const available = await campaigns.getAvailable({
      lat: 31.0824,
      lng: -97.6492,
    });
    expect(Array.isArray(available)).toBe(true);
    expect(available.length).toBeGreaterThan(0);
  });

  it("POST /v1/partners/offers/activate (offers.activate) returns activated shape", async () => {
    const activated = await offers.activate({
      sessionId: "sess_smoke",
      offerId: "offer_smoke",
    });
    expect(activated.status).toBe("activated");
    expect(activated.activatedAt).not.toBeNull();
  });
});
