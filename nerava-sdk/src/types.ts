/**
 * Shared primitive types for the Nerava SDK.
 *
 * Scope policy (the "hybrid" approach agreed at Step 3):
 *
 *   - This file holds ONLY primitive types that are used by more than one
 *     module, or types that cross the network boundary (request bodies,
 *     response envelopes) and therefore need a single canonical shape.
 *
 *   - Module-specific request/response types (SubmitSessionRequest,
 *     WalletTransaction, CampaignSummary, etc.) live inline in their
 *     module file in src/modules/*. That keeps types.ts short enough to
 *     read end-to-end and gives partner developers a one-stop file for
 *     each module they actually use.
 *
 *   - The SDK's types mirror the PUBLIC API SURFACE, not the backend's
 *     Pydantic schemas. This keeps the dependency arrow pointed the right
 *     way: backend schema changes don't break the SDK's type contract
 *     until the SDK explicitly opts into the change. Traceability is
 *     provided by JSDoc comments pointing at the relevant backend schema
 *     file (e.g. `backend/app/schemas/partner.py`) — not by imports.
 *
 * Error codes and the NeravaError class are intentionally NOT here —
 * they live in src/errors.ts in Step 4.
 */

// ---------------------------------------------------------------------------
// JSON value — recursive type for serializable request/response bodies
// ---------------------------------------------------------------------------

/**
 * A primitive JSON value. `null` is included because the JSON spec allows it
 * and the backend occasionally returns null fields in response envelopes.
 */
export type JsonPrimitive = string | number | boolean | null;

/**
 * A JSON object with string keys and recursive JSON values. The index
 * signature is `readonly` so that consumers (and the SDK itself) cannot
 * accidentally mutate a deserialized response body, which would cause
 * confusing bugs with cached state.
 */
export interface JsonObject {
  readonly [key: string]: JsonValue;
}

/**
 * A JSON array. Marked `readonly` for the same reason as `JsonObject`.
 */
export type JsonArray = readonly JsonValue[];

/**
 * The complete recursive JSON value type. Used by `NeravaClient.request()`
 * to constrain request bodies at compile time — consumers passing a `Date`,
 * a class instance, or a function will get a type error instead of a
 * runtime surprise when `JSON.stringify` mangles the value.
 *
 * Consumers who need to send a `Date` should call `.toISOString()` first.
 */
export type JsonValue = JsonPrimitive | JsonObject | JsonArray;

// ---------------------------------------------------------------------------
// Money — integer-cents + currency-code representation
// ---------------------------------------------------------------------------

/**
 * Monetary value. ALWAYS integer cents, NEVER floating-point dollars.
 *
 * Why cents-integer-only:
 *
 * 1. Floating point can't represent most cent amounts exactly — `0.1 + 0.2`
 *    is famously `0.30000000000000004`. That rounds wrong over a million
 *    wallet credits and corrupts the ledger.
 *
 * 2. The Nerava backend wallet tables store `balance_cents` as integer
 *    columns. The SDK boundary preserves that invariant end-to-end.
 *
 * 3. Currency is REQUIRED (not inferred) because the SDK will eventually
 *    support non-USD markets. Hardcoding USD now makes internationalization
 *    a breaking change later.
 *
 * Backend reference: `backend/app/models/driver_wallet.py` — the
 * `DriverWallet.balance_cents` column is the canonical source.
 */
export interface Money {
  /**
   * Amount in the smallest indivisible unit of the currency. For USD/EUR/
   * GBP/etc., this is cents. Integer only — decimals are a bug. Negative
   * values are allowed only for debits and refunds, never for balances.
   */
  readonly amountCents: number;

  /**
   * ISO 4217 currency code, e.g. `"USD"`, `"EUR"`, `"CAD"`. Uppercase.
   * Currently only `"USD"` is live on the backend; the field is present
   * for forward compatibility.
   */
  readonly currency: string;
}

/**
 * Convenience constructor for USD `Money` values.
 *
 * ```ts
 * import { usd } from "@nerava/sdk";
 * const bonus = usd(150); // $1.50
 * ```
 *
 * Throws if `amountCents` is not a safe integer — accidentally passing
 * `1.50` (dollars) instead of `150` (cents) is a common bug and the
 * runtime guard catches it at the call site.
 */
export function usd(amountCents: number): Money {
  if (!Number.isSafeInteger(amountCents)) {
    throw new Error(
      `usd(): amountCents must be a safe integer (got ${String(amountCents)}). Did you pass dollars instead of cents?`,
    );
  }
  return { amountCents, currency: "USD" };
}

// ---------------------------------------------------------------------------
// Geographic — latitude / longitude pair
// ---------------------------------------------------------------------------

/**
 * A WGS-84 latitude/longitude pair. Both fields are required — there is
 * no such thing as a "half-GPS" coordinate that would be usable by the
 * Nerava backend's geo matching.
 *
 * Used by:
 *   - `sessions.submit({ vehicleId, chargerId, lat, lng })`    (Step 5)
 *   - `campaigns.getAvailable({ lat, lng, vehicleType? })`     (Step 7)
 *   - any future proximity-based discovery endpoints
 */
export interface LatLng {
  /** Latitude in decimal degrees (-90 .. +90). */
  readonly lat: number;
  /** Longitude in decimal degrees (-180 .. +180). */
  readonly lng: number;
}

/**
 * Convenience constructor for `LatLng` with range validation. Throws if
 * either value is outside the WGS-84 valid range — catches the common
 * bug class where a degrees/radians mixup or swapped lat/lng arguments
 * silently produce a coordinate the backend rejects with a cryptic
 * validation error far from the call site.
 *
 * ```ts
 * import { latLng } from "@nerava/sdk";
 * const here = latLng(31.0824, -97.6492); // Market Heights
 * ```
 */
export function latLng(lat: number, lng: number): LatLng {
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new Error(
      `latLng(): lat must be a finite number in [-90, 90] (got ${String(lat)}). Did you swap lat and lng?`,
    );
  }
  if (!Number.isFinite(lng) || lng < -180 || lng > 180) {
    throw new Error(
      `latLng(): lng must be a finite number in [-180, 180] (got ${String(lng)}). Did you swap lat and lng?`,
    );
  }
  return { lat, lng };
}

// ---------------------------------------------------------------------------
// Vehicle type — data-source classification
// ---------------------------------------------------------------------------

/**
 * Vehicle data-source classification for partner integrations.
 *
 * - `"tesla"` — vehicles verified via Tesla Fleet API
 * - `"smartcar"` — vehicles verified via Smartcar
 * - `"unknown"` — vehicles where the partner has no upstream verification
 *   and relies on the driver's phone-reported telemetry
 *
 * Used as an optional filter on `campaigns.getAvailable()` so a partner
 * can pre-filter to campaigns their fleet is actually eligible for.
 *
 * Backend reference: `backend/app/models/session_event.py` —
 * `SessionEvent.source` column uses these same string values.
 */
export type VehicleType = "tesla" | "smartcar" | "unknown";

// ---------------------------------------------------------------------------
// Pagination — cursor-based, shared across every list endpoint
// ---------------------------------------------------------------------------

/**
 * Query parameters for paginated list endpoints.
 *
 * Cursor-based rather than offset-based because the Nerava backend's list
 * endpoints are all backed by `ORDER BY created_at DESC` + `id > cursor`
 * queries, which are stable under concurrent writes (offset-based pagination
 * can skip or duplicate rows when new items are inserted mid-iteration).
 */
export interface PaginationParams {
  /**
   * Opaque cursor from the previous page's `nextCursor` field. Omit on the
   * first request.
   */
  readonly cursor?: string;

  /**
   * Maximum number of items to return. Backend default is 50, max is 200.
   * Omit to accept the backend default.
   */
  readonly limit?: number;
}

/**
 * Response envelope for paginated list endpoints.
 *
 * `nextCursor` is `null` (not `undefined`) on the final page — this matches
 * the backend's JSON response shape and gives partners a clean loop
 * termination condition:
 *
 * ```ts
 * let cursor: string | undefined;
 * do {
 *   const page = await nerava.sessions.list({ cursor });
 *   for (const s of page.items) handle(s);
 *   cursor = page.nextCursor ?? undefined;
 * } while (cursor !== undefined);
 * ```
 */
export interface PaginatedResponse<T> {
  /** The items on this page, in reverse-chronological order. */
  readonly items: readonly T[];
  /**
   * Opaque cursor for the next page, or `null` when this is the final page.
   */
  readonly nextCursor: string | null;
}
