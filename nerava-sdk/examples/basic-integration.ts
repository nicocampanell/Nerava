/**
 * End-to-end integration example for @nerava/sdk.
 *
 * Spawns the mock server in-process, exercises every public method on
 * every module, and confirms the full lifecycle works against canned
 * fixtures. Safe to run without any backend credentials.
 *
 * Usage:
 *
 *     npm run example
 *
 * Or manually:
 *
 *     npx tsx examples/basic-integration.ts
 *
 * What this script demonstrates:
 *
 *   1. Construct an AuthManager + NeravaClient pointed at the mock
 *   2. Submit a session, list sessions, fetch one, complete it
 *   3. Discover campaigns by lat/lng + vehicle type
 *   4. Look up campaigns + offers for a session
 *   5. Activate an offer, complete it with a POS transaction id
 *   6. Driver wallet: balance, transactions, credit, debit, payout
 *   7. Intelligence (PENDING backend, mock-only)
 *   8. Error handling: typed NeravaError with code + status + requestId
 *
 * The script prints each step's outcome and exits 0 on success.
 */

import { startMockServer } from "../mock/server.js";
import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import { SessionsModule } from "../src/modules/sessions.js";
import { WalletModule } from "../src/modules/wallet.js";
import { CampaignsModule } from "../src/modules/campaigns.js";
import { OffersModule } from "../src/modules/offers.js";
import { IntelligenceModule } from "../src/modules/intelligence.js";
import { usd, latLng } from "../src/types.js";
import { NeravaError } from "../src/errors.js";

const TEST_PARTNER_KEY = "nrv_pk_exampleIntegrationKey1234";
const TEST_DRIVER_JWT = "eyJ.example.driver.jwt";

/**
 * Minimal logger that groups output by step so the script output is
 * easy to read. Uses console.log intentionally — this is an example
 * script that partners will read as documentation.
 */
function log(step: string, message: string, detail?: unknown): void {
  const prefix = `[${step}]`;
  if (detail !== undefined) {
    // eslint-disable-next-line no-console -- example CLI output
    console.log(prefix, message, "→", JSON.stringify(detail));
  } else {
    // eslint-disable-next-line no-console -- example CLI output
    console.log(prefix, message);
  }
}

async function main(): Promise<void> {
  // ---- Start the mock server ----
  const { port, stop } = await startMockServer(0); // port 0 = OS-assigned
  log("setup", `Mock server listening on http://localhost:${port}`);

  try {
    // ---- Construct SDK primitives ----
    const auth = new AuthManager({
      apiKey: TEST_PARTNER_KEY,
      driverToken: TEST_DRIVER_JWT,
    });
    const client = new NeravaClient({
      auth,
      baseUrl: `http://localhost:${port}`,
    });
    const sessions = new SessionsModule(client);
    const wallet = new WalletModule(client);
    const campaigns = new CampaignsModule(client);
    const offers = new OffersModule(client);
    const intelligence = new IntelligenceModule(client);
    log("setup", "AuthManager + NeravaClient + all modules constructed");

    // ---- Sessions flow ----
    const submitted = await sessions.submit({
      vehicleId: "v_example_1",
      chargerId: "c_example_heights",
      ...latLng(31.0824, -97.6492),
      idempotencyKey: "example-trace-001",
    });
    log("sessions.submit", "created session", { id: submitted.id, status: submitted.status });

    const page = await sessions.list({ limit: 10 });
    log("sessions.list", `got ${page.items.length} sessions`, { nextCursor: page.nextCursor });

    const fetched = await sessions.get(submitted.id);
    log("sessions.get", "round-tripped session by id", { id: fetched.id });

    const completed = await sessions.complete(submitted.id);
    log("sessions.complete", "marked session completed", {
      id: completed.id,
      status: completed.status,
      durationSeconds: completed.durationSeconds,
    });

    // ---- Campaigns flow ----
    const available = await campaigns.getAvailable({
      lat: 31.0824,
      lng: -97.6492,
      vehicleType: "tesla",
    });
    log("campaigns.getAvailable", `${available.length} campaigns match`, {
      first: available[0]?.name,
    });

    const sessionCampaigns = await campaigns.getForSession(submitted.id);
    log("campaigns.getForSession", `${sessionCampaigns.length} campaigns matched this session`);

    // ---- Offers flow ----
    const sessionOffers = await offers.getForSession(submitted.id);
    log("offers.getForSession", `${sessionOffers.length} offers available`, {
      first: sessionOffers[0]?.title,
    });

    const activated = await offers.activate({
      sessionId: submitted.id,
      offerId: sessionOffers[0]?.id ?? "offer_example",
    });
    log("offers.activate", "offer activated", {
      id: activated.id,
      status: activated.status,
      activatedAt: activated.activatedAt,
    });

    const completedOffer = await offers.complete({
      sessionId: submitted.id,
      offerId: activated.id,
      transactionId: "txn_example_pos_99",
    });
    log("offers.complete", "offer completed", {
      status: completedOffer.status,
      transactionId: completedOffer.transactionId,
    });

    // ---- Wallet flow (driver scope) ----
    const balance = await wallet.getBalance("drv_example_1");
    log("wallet.getBalance", "fetched driver balance", {
      balance: balance.balance,
      nova: balance.novaBalance,
    });

    const transactions = await wallet.getTransactions("drv_example_1", { limit: 5 });
    log("wallet.getTransactions", `${transactions.items.length} transactions`);

    const credited = await wallet.credit({
      driverId: "drv_example_1",
      amount: usd(500),
      campaignId: "camp_mock_heights_pizza",
      description: "Example integration credit",
    });
    log("wallet.credit", "credit posted", { id: credited.id });

    const debited = await wallet.debit({
      driverId: "drv_example_1",
      amount: usd(400),
      merchantId: "merch_mock_heights",
      description: "Pizza redemption",
    });
    log("wallet.debit", "debit posted", { id: debited.id });

    const payout = await wallet.requestPayout("drv_example_1");
    log("wallet.requestPayout", "payout requested", {
      id: payout.id,
      amount: payout.amount,
      status: payout.status,
    });

    // ---- Intelligence (PENDING backend, mock-only) ----
    const intel = await intelligence.getSessionData(submitted.id);
    log("intelligence.getSessionData", "fetched session intel (MOCK ONLY)", {
      qualityScore: intel.qualityScore,
      qualityBucket: intel.qualityBucket,
      matchedGrants: intel.matchedGrants.length,
    });

    // ---- Error handling demonstration ----
    // Point the client at a non-existent path to show the NeravaError shape.
    try {
      await client.request({ auth: "partner", path: "/v1/partners/does-not-exist" });
    } catch (err) {
      if (err instanceof NeravaError) {
        log("error-handling", "caught NeravaError as expected", {
          code: err.code,
          status: err.status,
          requestId: err.requestId,
        });
      } else {
        throw err;
      }
    }

    log("done", "full SDK surface exercised successfully against the mock server");
  } finally {
    await stop();
    log("teardown", "mock server stopped");
  }
}

main().catch((err: unknown) => {
  // eslint-disable-next-line no-console -- example CLI error output
  console.error("[example] failed:", err);
  process.exitCode = 1;
});
