/**
 * HTTP client for the Nerava SDK.
 *
 * `NeravaClient` is the single transport layer used by every module
 * (sessions, wallet, campaigns, offers, intelligence). It owns:
 *
 * - URL construction (base URL + path + query string)
 * - Header injection (driven by `AuthContext` on each request)
 * - JSON body serialization
 * - Response parsing + HTTP-level error mapping
 *
 * The client is deliberately thin. Module-level method signatures live in
 * `src/modules/*` and call into `client.request()` with the appropriate
 * auth context per endpoint. Developers using the SDK never construct or
 * touch `NeravaClient` directly — the top-level `Nerava` facade in Step 11
 * will hide it.
 *
 * Error handling note: this step (Step 2) throws plain `Error` on HTTP
 * failures. Step 4 will introduce `NeravaError` with `status`, `code`, and
 * `message` fields and the structured error-code enum, and this file will
 * be updated to throw those instead. Search for `TODO(step-4)` markers.
 */

import type { AuthManager } from "./auth.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Auth scope for a single request. Every module method must pick exactly
 * one — there is no "mixed" mode.
 *
 * - `"partner"` — sends `X-Partner-Key`. Used by sessions, campaigns,
 *   offers, intelligence (all B2B integration endpoints).
 * - `"driver"` — sends `Authorization: Bearer <jwt>`. Used by wallet
 *   operations that identify a specific driver.
 */
export type AuthContext = "partner" | "driver";

/**
 * HTTP method. Intentionally a narrow union of the ones the Nerava API
 * actually exposes — we don't accept `HEAD`, `OPTIONS`, etc. at the SDK
 * surface.
 */
export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

/**
 * Query string value type. URLSearchParams coerces all three to strings
 * consistently so the caller doesn't have to pre-stringify integers or
 * booleans.
 */
export type QueryValue = string | number | boolean;

/**
 * Options for a single `NeravaClient.request()` call.
 */
export interface RequestOptions {
  /**
   * Which auth credential to send. Required — no implicit default, because
   * picking the wrong one is a security-sensitive mistake and we want the
   * call site to make the choice explicitly.
   */
  auth: AuthContext;

  /**
   * Path relative to the base URL (e.g. `/v1/partners/sessions`). Leading
   * slash is optional; the client normalizes either form.
   */
  path: string;

  /**
   * HTTP method. Defaults to `"GET"`.
   */
  method?: HttpMethod;

  /**
   * Optional query string parameters. Values are stringified via
   * `String(value)` — booleans become `"true"`/`"false"`, numbers become
   * their decimal form.
   */
  query?: Record<string, QueryValue>;

  /**
   * Optional JSON-serializable request body. The client will:
   *   1. JSON.stringify it
   *   2. add `Content-Type: application/json`
   *
   * Passing `undefined` (or omitting) means no body. Do not pass `null` —
   * that would serialize as the string "null" and confuse the backend.
   */
  body?: unknown;

  /**
   * Optional extra headers. These are merged AFTER the auth headers, so a
   * caller could in theory overwrite `X-Partner-Key` or `Authorization` —
   * we deliberately allow this so tests can inspect behavior, but modules
   * must not use it for anything security-sensitive.
   */
  headers?: Record<string, string>;
}

/**
 * Constructor configuration for `NeravaClient`.
 */
export interface NeravaClientConfig {
  /**
   * The `AuthManager` instance that holds the partner key and driver JWT.
   */
  auth: AuthManager;

  /**
   * Base URL for the Nerava API. Defaults to production. Pass the mock
   * server URL (`http://localhost:3001`) when developing locally against
   * the fixtures in `mock/`.
   */
  baseUrl?: string;

  /**
   * Optional `fetch` implementation override. Defaults to the global
   * `fetch` from Node 18.17+. Tests inject a mock here. Production code
   * should leave this undefined.
   */
  fetch?: typeof fetch;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/**
 * Production Nerava API base URL. Matches the App Runner service at
 * `api.nerava.network`.
 */
export const DEFAULT_BASE_URL = "https://api.nerava.network";

// ---------------------------------------------------------------------------
// NeravaClient
// ---------------------------------------------------------------------------

/**
 * Low-level HTTP transport. Constructed once per SDK instance and reused
 * for every request. Stateless except for the `AuthManager`, base URL, and
 * `fetch` implementation held at construction.
 */
export class NeravaClient {
  readonly #auth: AuthManager;
  readonly #baseUrl: string;
  readonly #fetch: typeof fetch;

  constructor(config: NeravaClientConfig) {
    this.#auth = config.auth;
    // Trim trailing slash so we don't produce `/v1//partners/sessions`.
    const trimmed = (config.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    // Validate eagerly at construction so a misconfigured base URL surfaces
    // with a clear "NeravaClient: invalid baseUrl" message — rather than
    // throwing a cryptic TypeError deep inside `request()` when a consumer
    // makes their first API call and we try to `new URL(...)` on a malformed
    // string. Callers are much more likely to debug a constructor error
    // correctly than a deferred runtime error.
    try {
      // Only constructed for validation; the parsed URL is discarded.
      new URL(trimmed);
    } catch {
      throw new Error(
        `NeravaClient: invalid baseUrl "${config.baseUrl ?? DEFAULT_BASE_URL}" — expected an absolute URL like https://api.nerava.network`,
      );
    }
    this.#baseUrl = trimmed;
    this.#fetch = config.fetch ?? fetch;
  }

  /**
   * Issues an HTTP request against the Nerava API. Called by every module
   * method. Returns the parsed JSON response as `T`.
   *
   * Throws a plain `Error` on network failures and non-2xx responses.
   * TODO(step-4): replace with structured `NeravaError` once Step 4 ships.
   */
  async request<T>(options: RequestOptions): Promise<T> {
    const method: HttpMethod = options.method ?? "GET";
    const hasBody = options.body !== undefined;

    const url = this.#buildUrl(options.path, options.query);
    const headers = await this.#buildHeaders(options.auth, options.headers, hasBody);

    const init: RequestInit = {
      method,
      headers,
    };
    if (hasBody) {
      init.body = JSON.stringify(options.body);
    }

    let response: Response;
    try {
      response = await this.#fetch(url, init);
    } catch (networkErr) {
      // TODO(step-4): throw NeravaError with code 'NETWORK_ERROR'.
      const message =
        networkErr instanceof Error ? networkErr.message : String(networkErr);
      throw new Error(
        `NeravaClient: network error calling ${method} ${options.path}: ${message}`,
      );
    }

    if (!response.ok) {
      // TODO(step-4): parse the error body into a NeravaError with the
      // correct `code` from the backend's error envelope, and throw that.
      // For Step 2 we surface the raw status so tests can assert on it.
      let body = "";
      try {
        body = await response.text();
      } catch {
        // Body read failed — surface the status line only.
      }
      throw new Error(
        `NeravaClient: HTTP ${response.status} ${response.statusText} calling ${method} ${options.path}${body ? ` — ${body}` : ""}`,
      );
    }

    // Some endpoints (e.g. DELETE) return 204 No Content. Don't try to
    // parse an empty body as JSON — return undefined cast to T. Callers
    // that expect void responses will type their generic as `void`.
    if (response.status === 204) {
      return undefined as T;
    }

    return (await response.json()) as T;
  }

  // -------------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------------

  #buildUrl(path: string, query: Record<string, QueryValue> | undefined): string {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    const url = new URL(`${this.#baseUrl}${normalizedPath}`);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        url.searchParams.set(key, String(value));
      }
    }
    return url.toString();
  }

  async #buildHeaders(
    auth: AuthContext,
    extra: Record<string, string> | undefined,
    hasBody: boolean,
  ): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
      Accept: "application/json",
    };
    if (hasBody) {
      headers["Content-Type"] = "application/json";
    }

    if (auth === "partner") {
      headers["X-Partner-Key"] = this.#auth.getPartnerKey();
    } else {
      // Driver context.
      const token = await this.#auth.getDriverToken();
      if (!token) {
        // TODO(step-4): throw NeravaError with code 'NO_DRIVER_TOKEN'.
        throw new Error(
          "NeravaClient: driver-scope request requires a driver JWT — call auth.setDriverToken() or pass driverToken at construction first",
        );
      }
      headers["Authorization"] = `Bearer ${token}`;
    }

    if (extra) {
      Object.assign(headers, extra);
    }
    return headers;
  }
}
