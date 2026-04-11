/**
 * Intelligence module — partner-scope session intelligence retrieval.
 *
 * ⚠️ PENDING: The backend endpoint
 * `GET /v1/partners/sessions/{id}/intelligence` does NOT yet exist.
 * This module is served entirely from the mock server fixtures in
 * Step 9. Until the backend ships the real endpoint, do NOT point
 * this module at production — calls will hit a 404 and surface as
 * NeravaError with code `NOT_FOUND`.
 *
 * The SDK's type contract is nailed down NOW so that when the
 * backend endpoint lands, the SDK doesn't need a breaking-change
 * release to match it. The fields below are the agreed-on public
 * surface; the backend's eventual Pydantic schema will conform to
 * this shape via snake_case field names, NOT the other way around.
 *
 * Auth context: `partner` (X-Partner-Key header). Intelligence data
 * is partner-scoped and should never be routed through a driver JWT.
 *
 * Case-conversion contract: identical to other modules — request
 * bodies are camelCase→snake_case explicit; response bodies run
 * through `camelCaseKeys()` before casting to the typed shape.
 */

import type { NeravaClient } from "../client.js";
import { camelCaseKeys } from "../internal/case.js";

// ---------------------------------------------------------------------------
// Module-specific types (inline per the hybrid scope decision)
// ---------------------------------------------------------------------------

/**
 * Quality score bucket used to classify a session's verification
 * confidence. `verified` means all anti-fraud checks passed; `suspect`
 * means at least one heuristic flagged the session; `rejected` means
 * the session was quarantined and will not grant rewards.
 */
export type QualityBucket = "verified" | "suspect" | "rejected";

/**
 * Anti-fraud signals evaluated by the Nerava backend for a session.
 * Each signal is a boolean + an optional score contribution. The
 * actual scoring algorithm lives server-side; the SDK only surfaces
 * the result.
 */
export interface AntiFraudSignals {
  readonly locationConsistent: boolean;
  readonly telemetryConsistent: boolean;
  readonly vehicleAuthorized: boolean;
  readonly chargerWhitelisted: boolean;
  readonly withinExpectedWindow: boolean;
  readonly duplicateDetected: boolean;
}

/**
 * A single matched grant for a session, as seen by intelligence —
 * the SDK's grants surface lives in wallet.ts; this is the
 * intelligence-side view (which includes evaluation context, not
 * the underlying WalletTransaction fields).
 */
export interface IntelligenceGrant {
  readonly campaignId: string;
  readonly campaignName: string;
  readonly matchedAt: string;
  readonly priority: number;
  readonly evaluationNotes: string | null;
}

/**
 * Session intelligence response.
 *
 * Mirrors the eventual backend response shape. When the real endpoint
 * ships, field names can be snake_case on the wire (the SDK converts
 * via `camelCaseKeys()`) but the public type surface stays camelCase.
 */
export interface SessionIntelligenceResponse {
  readonly sessionId: string;
  readonly qualityScore: number;
  readonly qualityBucket: QualityBucket;
  readonly antiFraud: AntiFraudSignals;
  readonly matchedGrants: readonly IntelligenceGrant[];
  readonly evaluatedAt: string;
  readonly backendVersion: string;
}

// ---------------------------------------------------------------------------
// Module class
// ---------------------------------------------------------------------------

/**
 * Intelligence module surface.
 *
 * ⚠️ PENDING backend: `GET /v1/partners/sessions/{id}/intelligence` is
 * not yet implemented. Until it ships, this module only works against
 * the Step 9 mock server. Production calls will return HTTP 404.
 *
 * Once the backend endpoint lands, no SDK changes should be required
 * other than removing this PENDING notice — the response type above
 * is the contract both sides will conform to.
 */
export class IntelligenceModule {
  readonly #client: NeravaClient;

  constructor(client: NeravaClient) {
    this.#client = client;
  }

  /**
   * Fetch the intelligence envelope for a specific session.
   *
   * ⚠️ PENDING backend — see the class-level doc. Partners using this
   * method must point the SDK at the mock server
   * (`baseUrl: 'http://localhost:3001'`) until the backend endpoint
   * ships.
   */
  async getSessionData(sessionId: string): Promise<SessionIntelligenceResponse> {
    if (!sessionId) {
      throw new Error("intelligence.getSessionData(): sessionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: `/v1/partners/sessions/${encodeURIComponent(sessionId)}/intelligence`,
    });
    return camelCaseKeys(raw) as SessionIntelligenceResponse;
  }
}
