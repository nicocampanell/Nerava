/**
 * Auth context management for the Nerava SDK.
 *
 * Two auth surfaces, both owned by AuthManager:
 *
 * 1. Partner key (`nrv_pk_*`) — a long-lived server-side credential that
 *    identifies the partner. Carried on partner-scope requests as the
 *    `X-Partner-Key` header. Required at construction, immutable afterwards.
 *
 * 2. Driver JWT — a short-lived token that identifies a specific driver.
 *    Carried on driver-scope requests as `Authorization: Bearer <jwt>`.
 *    Optional. The SDK does NOT own the driver login flow — partners mint
 *    the JWT server-side through whatever Nerava auth endpoint they prefer
 *    and hand it to the SDK either at construction time or later via
 *    `setDriverToken()`.
 *
 * Persistence is delegated to a pluggable `TokenStore` adapter. The default
 * is in-memory (lost on process restart). Multi-tenant servers should
 * implement a `TokenStore` keyed by their own driver identifier (e.g. Redis,
 * a database row) and inject it at construction time. The SDK intentionally
 * does NOT ship a file-based store — file persistence is dangerous for
 * multi-process servers and should be written by the consumer if needed.
 */

// ---------------------------------------------------------------------------
// TokenStore adapter interface
// ---------------------------------------------------------------------------

/**
 * Pluggable driver-token storage adapter.
 *
 * Implementations may be synchronous (e.g. in-memory) or asynchronous (e.g.
 * Redis / database). All methods may return either a raw value or a Promise.
 * AuthManager awaits every call so either style works.
 */
export interface TokenStore {
  /**
   * Returns the currently stored driver JWT, or `null` if nothing is stored.
   * Implementations must return `null` (not `undefined`) for the empty state
   * to keep the contract unambiguous.
   */
  get(): Promise<string | null> | string | null;

  /**
   * Persists the given driver JWT. Passing `null` clears the stored token.
   * Implementations should be idempotent — setting the same value twice must
   * not throw.
   */
  set(token: string | null): Promise<void> | void;
}

/**
 * Default in-memory `TokenStore`. Holds the driver JWT in a single private
 * field for the lifetime of the process. Cleared on process restart.
 *
 * Suitable for single-process applications and tests. NOT suitable for
 * multi-tenant servers that need to serve multiple drivers from one SDK
 * instance — those should supply their own `TokenStore`.
 */
export class InMemoryTokenStore implements TokenStore {
  #token: string | null = null;

  get(): string | null {
    return this.#token;
  }

  set(token: string | null): void {
    this.#token = token;
  }
}

// ---------------------------------------------------------------------------
// AuthManager
// ---------------------------------------------------------------------------

/**
 * Public partner-key format. Matches the existing backend contract in
 * `backend/app/dependencies/partner_auth.py` — partner keys begin with
 * `nrv_pk_` followed by the hex key material. Validation is format-only
 * here; actual key authorization happens server-side.
 */
const PARTNER_KEY_PATTERN = /^nrv_pk_[A-Za-z0-9]{8,}$/;

/**
 * Constructor configuration for `AuthManager`.
 */
export interface AuthManagerConfig {
  /**
   * Partner API key in `nrv_pk_*` format. Required. Immutable after
   * construction — the field is stored in a private slot and never exposed
   * to callers except indirectly via `getPartnerKey()`.
   */
  apiKey: string;

  /**
   * Optional pre-minted driver JWT. If provided, it is stored in the
   * configured `TokenStore` during construction. Partners who use a custom
   * async `tokenStore` should prefer calling `setDriverToken()` explicitly
   * after construction so they can `await` the write — the constructor can
   * only fire a void-discarded promise, which is fine for the default
   * in-memory store but races against rapid `getDriverToken()` calls when
   * an async store is in use.
   */
  driverToken?: string;

  /**
   * Pluggable token store. Defaults to a new `InMemoryTokenStore`.
   */
  tokenStore?: TokenStore;
}

/**
 * Owns both auth credentials for a single SDK instance.
 *
 * Instances are safe to share across multiple `NeravaClient` request calls
 * but are NOT safe to share across unrelated driver sessions when using the
 * in-memory store — a custom multi-tenant `TokenStore` should be supplied
 * in that case.
 */
export class AuthManager {
  readonly #apiKey: string;
  readonly #tokenStore: TokenStore;

  constructor(config: AuthManagerConfig) {
    if (!config.apiKey) {
      throw new Error("AuthManager: apiKey is required");
    }
    if (!PARTNER_KEY_PATTERN.test(config.apiKey)) {
      throw new Error(
        "AuthManager: invalid apiKey format (expected nrv_pk_* with at least 8 key characters)",
      );
    }
    this.#apiKey = config.apiKey;
    this.#tokenStore = config.tokenStore ?? new InMemoryTokenStore();

    if (config.driverToken !== undefined) {
      // Fire-and-forget for the async case. For the default InMemoryTokenStore
      // this completes synchronously. For custom async stores see the
      // docstring on `driverToken` above.
      void Promise.resolve(this.#tokenStore.set(config.driverToken));
    }
  }

  /**
   * Returns the partner API key for use in the `X-Partner-Key` header on
   * partner-scope requests. Never logs the key.
   */
  getPartnerKey(): string {
    return this.#apiKey;
  }

  /**
   * Returns the current driver JWT from the configured `TokenStore`, or
   * `null` if none is set. Awaits async stores.
   */
  async getDriverToken(): Promise<string | null> {
    return await this.#tokenStore.get();
  }

  /**
   * Persists a new driver JWT to the configured `TokenStore`. Use this to
   * update the token after a refresh, or to set it after construction when
   * the partner backend has obtained a fresh JWT from Nerava.
   */
  async setDriverToken(token: string): Promise<void> {
    if (!token) {
      throw new Error("AuthManager: setDriverToken requires a non-empty token");
    }
    await this.#tokenStore.set(token);
  }

  /**
   * Clears the driver JWT from the configured `TokenStore`. Subsequent
   * driver-scope requests will fail until a new token is set.
   */
  async clearDriverToken(): Promise<void> {
    await this.#tokenStore.set(null);
  }
}
