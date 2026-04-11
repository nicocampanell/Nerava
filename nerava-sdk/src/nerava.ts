/**
 * Top-level `Nerava` facade.
 *
 * This is the class partners actually instantiate. It composes
 * `AuthManager`, `NeravaClient`, and every module class from
 * `src/modules/*` behind a single `new Nerava({apiKey, ...})` entry
 * point that matches the README quickstart.
 *
 * Partners never construct `AuthManager` or `NeravaClient` directly —
 * they're available as exports for advanced use cases (e.g. custom
 * TokenStore implementations, in-process testing with a spawned mock
 * server) but the facade is the idiomatic entry point.
 *
 * Why a facade:
 *
 *   1. The README quickstart promises `new Nerava({ apiKey })` works.
 *      Without this class, partners would have to construct each
 *      module individually, which is tedious and error-prone.
 *
 *   2. The facade owns the client lifecycle — one `NeravaClient`
 *      instance shared by every module — so partners don't
 *      accidentally create multiple clients with divergent auth state.
 *
 *   3. It provides a single upgrade surface: adding a new module in
 *      a future step means updating this file once rather than
 *      touching every consumer.
 */

import { AuthManager, type TokenStore } from "./auth.js";
import { NeravaClient } from "./client.js";
import { CampaignsModule } from "./modules/campaigns.js";
import { IntelligenceModule } from "./modules/intelligence.js";
import { OffersModule } from "./modules/offers.js";
import { SessionsModule } from "./modules/sessions.js";
import { WalletModule } from "./modules/wallet.js";

/**
 * Constructor configuration for the top-level `Nerava` class.
 *
 * Only `apiKey` is required — everything else is optional with
 * production defaults that match the README quickstart.
 */
export interface NeravaConfig {
  /**
   * Partner API key in `nrv_pk_*` format. Required.
   *
   * Obtained from the Nerava partner portal. Treat as a secret —
   * never commit to source control, never log, never ship in
   * client-side bundles.
   */
  readonly apiKey: string;

  /**
   * Optional pre-minted driver JWT. When present, driver-scope
   * methods (`nerava.wallet.*`) work immediately after construction.
   * Your backend is responsible for obtaining the JWT through your
   * preferred auth flow — the SDK does NOT own driver login.
   *
   * If omitted, driver-scope methods throw `NeravaError` with code
   * `NO_DRIVER_TOKEN` until you call `nerava.auth.setDriverToken(token)`.
   */
  readonly driverToken?: string;

  /**
   * Optional custom `TokenStore` adapter for driver JWT persistence.
   * Defaults to an in-memory store (`InMemoryTokenStore`) that loses
   * state on process restart. Multi-tenant servers should implement
   * their own store keyed by driver id. See the README for a Redis
   * example.
   */
  readonly tokenStore?: TokenStore;

  /**
   * Optional API base URL override. Defaults to the production
   * Nerava API at `https://api.nerava.network`. Set to
   * `http://localhost:3001` (or the port returned by
   * `startMockServer`) to point the SDK at the bundled mock server.
   */
  readonly baseUrl?: string;

  /**
   * Optional `fetch` implementation override. Defaults to Node's
   * global `fetch` (18.17+). Tests inject mocks here.
   */
  readonly fetch?: typeof fetch;
}

/**
 * The Nerava SDK entry point.
 *
 * ```ts
 * import { Nerava, usd, latLng } from "@nerava/sdk";
 *
 * const nerava = new Nerava({ apiKey: "nrv_pk_yourPartnerKey1234" });
 *
 * const session = await nerava.sessions.submit({
 *   vehicleId: "v_1",
 *   chargerId: "c_1",
 *   ...latLng(31.0824, -97.6492),
 * });
 *
 * await nerava.wallet.credit({
 *   driverId: "drv_1",
 *   amount: usd(500),
 *   campaignId: "camp_1",
 * });
 * ```
 */
export class Nerava {
  /**
   * Auth manager — exposed so partners can call `setDriverToken()`,
   * `clearDriverToken()`, etc. after construction. Rarely needed
   * directly since the modules call through it transparently.
   */
  readonly auth: AuthManager;

  /** Sessions module (partner scope). */
  readonly sessions: SessionsModule;

  /** Wallet module (driver scope). */
  readonly wallet: WalletModule;

  /** Campaigns module (partner scope). */
  readonly campaigns: CampaignsModule;

  /** Offers module (partner scope). */
  readonly offers: OffersModule;

  /**
   * Intelligence module (partner scope).
   *
   * ⚠️ PENDING backend: the real endpoint does not exist yet. Use
   * the mock server until it ships. See the README intelligence
   * section.
   */
  readonly intelligence: IntelligenceModule;

  /**
   * Underlying HTTP client. Exposed for advanced use cases (custom
   * request paths, inspection) but most partners will never need it.
   */
  readonly client: NeravaClient;

  constructor(config: NeravaConfig) {
    this.auth = new AuthManager({
      apiKey: config.apiKey,
      ...(config.driverToken !== undefined ? { driverToken: config.driverToken } : {}),
      ...(config.tokenStore !== undefined ? { tokenStore: config.tokenStore } : {}),
    });
    this.client = new NeravaClient({
      auth: this.auth,
      ...(config.baseUrl !== undefined ? { baseUrl: config.baseUrl } : {}),
      ...(config.fetch !== undefined ? { fetch: config.fetch } : {}),
    });
    this.sessions = new SessionsModule(this.client);
    this.wallet = new WalletModule(this.client);
    this.campaigns = new CampaignsModule(this.client);
    this.offers = new OffersModule(this.client);
    this.intelligence = new IntelligenceModule(this.client);
  }
}
