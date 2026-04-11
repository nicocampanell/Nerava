/**
 * Sessions module — partner-scope charging session ingest + retrieval.
 *
 * Auth context: `partner` (X-Partner-Key header). All methods here
 * identify the partner, not a specific driver.
 *
 * Backend reference: `backend/app/routers/partner_api.py`, endpoints
 * `/v1/partners/sessions{,/{id}}`. The SDK's request/response types
 * mirror the public partner API surface, not the Pydantic schemas
 * directly — see types.ts for the rationale.
 *
 * Case-conversion contract: the backend uses snake_case on the wire
 * (e.g. `vehicle_id`, `started_at`). The SDK exposes camelCase to
 * consumers (e.g. `vehicleId`, `startedAt`). Conversion happens in
 * BOTH directions inside this module:
 *
 *   - Request bodies: camelCase → snake_case, explicit per field
 *     (see each method's body assembly).
 *   - Response bodies: snake_case → camelCase via `camelCaseKeys()`
 *     from src/internal/case.ts, applied to the raw client response
 *     before the final cast to `SessionResponse`.
 *
 * The two-direction translation lives in the module (not the client)
 * because the client is transport-only and doesn't know which fields
 * need what treatment. Each module owns its own wire contract.
 */

import type { NeravaClient } from "../client.js";
import { camelCaseKeys } from "../internal/case.js";
import type { PaginatedResponse, PaginationParams, VehicleType } from "../types.js";

// ---------------------------------------------------------------------------
// Module-specific types (inline per the hybrid scope decision)
// ---------------------------------------------------------------------------

/**
 * Request body for `sessions.submit()`. All fields except `lat`/`lng`
 * are the partner's opaque identifiers — the SDK does not validate the
 * shape of `vehicleId` or `chargerId` because they come from the partner's
 * own systems and formats vary.
 */
export interface SubmitSessionRequest {
  /** Opaque vehicle identifier from the partner's system. */
  readonly vehicleId: string;
  /** Opaque charger identifier from the partner's system. */
  readonly chargerId: string;
  /**
   * Vehicle latitude at session start. Not validated here — use the
   * `latLng()` helper from `types.ts` at the call site if you want
   * range validation before the network round-trip.
   */
  readonly lat: number;
  /** Vehicle longitude at session start. See `lat` note. */
  readonly lng: number;
  /**
   * Optional partner-side idempotency key. When set, repeated submissions
   * with the same key + source return the same SessionResponse rather
   * than creating duplicates. Recommended for at-least-once ingest.
   */
  readonly idempotencyKey?: string;
}

/**
 * Session status lifecycle from the backend.
 */
export type SessionStatus =
  | "open"
  | "completed"
  | "expired"
  | "rejected"
  | "partner_managed";

/**
 * Response envelope for a single session. Mirrors the partner API shape;
 * NOT a Pydantic schema copy. If the backend adds fields, the SDK is not
 * required to surface them until an explicit version bump.
 */
export interface SessionResponse {
  readonly id: string;
  readonly status: SessionStatus;
  readonly vehicleId: string;
  readonly chargerId: string;
  readonly startedAt: string;
  readonly endedAt: string | null;
  readonly durationSeconds: number | null;
  readonly kwhDelivered: number | null;
  readonly lat: number;
  readonly lng: number;
  readonly partnerId: string;
  readonly driverId: string | null;
}

/**
 * Filter parameters for `sessions.list()`. Combines pagination with
 * optional time/status/vehicle filters — all optional, all sent as
 * query params.
 */
export interface SessionListFilters extends PaginationParams {
  readonly status?: SessionStatus;
  readonly vehicleId?: string;
  readonly since?: string;
  readonly until?: string;
  readonly vehicleType?: VehicleType;
}

// ---------------------------------------------------------------------------
// Module class
// ---------------------------------------------------------------------------

/**
 * Sessions module surface. Constructed by the top-level `Nerava` facade
 * in Step 11 and attached as `nerava.sessions`. Partners never construct
 * this directly.
 */
export class SessionsModule {
  readonly #client: NeravaClient;

  constructor(client: NeravaClient) {
    this.#client = client;
  }

  /**
   * Submit a new charging session. Idempotent via `idempotencyKey`.
   *
   * ```ts
   * const session = await nerava.sessions.submit({
   *   vehicleId: "v_123",
   *   chargerId: "c_456",
   *   lat: 31.0824,
   *   lng: -97.6492,
   *   idempotencyKey: "partner-trace-id",
   * });
   * ```
   */
  async submit(request: SubmitSessionRequest): Promise<SessionResponse> {
    const body: Record<string, string | number> = {
      vehicle_id: request.vehicleId,
      charger_id: request.chargerId,
      lat: request.lat,
      lng: request.lng,
    };
    if (request.idempotencyKey !== undefined) {
      body["idempotency_key"] = request.idempotencyKey;
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      method: "POST",
      path: "/v1/partners/sessions",
      body,
    });
    return camelCaseKeys(raw) as SessionResponse;
  }

  /**
   * Get a single session by id. Throws NeravaError with code
   * `SESSION_NOT_FOUND` if the id does not belong to the calling partner.
   */
  async get(sessionId: string): Promise<SessionResponse> {
    if (!sessionId) {
      throw new Error("sessions.get(): sessionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: `/v1/partners/sessions/${encodeURIComponent(sessionId)}`,
    });
    return camelCaseKeys(raw) as SessionResponse;
  }

  /**
   * List the partner's sessions with optional filters + pagination.
   * Returns a `PaginatedResponse<SessionResponse>` — loop on
   * `page.nextCursor !== null` to walk all pages.
   *
   * The `limit` filter is capped at 200 server-side. Passing anything
   * above that will result in a backend `VALIDATION_ERROR`.
   */
  async list(
    filters: SessionListFilters = {},
  ): Promise<PaginatedResponse<SessionResponse>> {
    if (filters.limit !== undefined && filters.limit > 200) {
      throw new Error(
        `sessions.list(): limit must be ≤ 200 (got ${filters.limit}). Backend will reject anything larger.`,
      );
    }
    const query: Record<string, string | number | boolean> = {};
    if (filters.cursor !== undefined) query["cursor"] = filters.cursor;
    if (filters.limit !== undefined) query["limit"] = filters.limit;
    if (filters.status !== undefined) query["status"] = filters.status;
    if (filters.vehicleId !== undefined) query["vehicle_id"] = filters.vehicleId;
    if (filters.since !== undefined) query["since"] = filters.since;
    if (filters.until !== undefined) query["until"] = filters.until;
    if (filters.vehicleType !== undefined) query["vehicle_type"] = filters.vehicleType;

    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: "/v1/partners/sessions",
      query,
    });
    return camelCaseKeys(raw) as PaginatedResponse<SessionResponse>;
  }

  /**
   * Mark a session as completed. Corresponds to the backend's
   * `PATCH /v1/partners/sessions/{id}` with `status: "completed"`.
   *
   * Idempotent — calling this on an already-completed session returns
   * the same response without an error.
   */
  async complete(sessionId: string): Promise<SessionResponse> {
    if (!sessionId) {
      throw new Error("sessions.complete(): sessionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      method: "PATCH",
      path: `/v1/partners/sessions/${encodeURIComponent(sessionId)}`,
      body: { status: "completed" },
    });
    return camelCaseKeys(raw) as SessionResponse;
  }
}
