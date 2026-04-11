/**
 * Offers module — partner-scope merchant offer activation + completion.
 *
 * Auth context: `partner` (X-Partner-Key header).
 *
 * Three methods model the offer lifecycle:
 *
 *   - `getForSession(sessionId)` — list offers available to the driver
 *     during the given charging session.
 *   - `activate({ sessionId, offerId })` — mark an offer as claimed.
 *     Creates an ExclusiveSession in the backend.
 *   - `complete({ sessionId, offerId, transactionId })` — mark the
 *     offer as redeemed. Links the partner's own transaction id
 *     (e.g. POS order id) to the offer for reconciliation.
 *
 * Backend reference: `backend/app/routers/exclusive.py` and
 * `backend/app/routers/partner_api.py`.
 *
 * Case-conversion contract: request bodies are camelCase → snake_case
 * explicit per field; response bodies run through `camelCaseKeys()`
 * at the parse boundary before casting to the typed response shape.
 */

import type { NeravaClient } from "../client.js";
import { camelCaseKeys } from "../internal/case.js";
import type { Money } from "../types.js";

// ---------------------------------------------------------------------------
// Module-specific types (inline per the hybrid scope decision)
// ---------------------------------------------------------------------------

/**
 * Status lifecycle of a merchant offer relative to a driver's session.
 */
export type OfferStatus =
  | "available"
  | "activated"
  | "completed"
  | "expired"
  | "rejected";

/**
 * Summary of a merchant offer as returned by the discovery endpoint.
 */
export interface OfferSummary {
  readonly id: string;
  readonly merchantId: string;
  readonly merchantName: string;
  readonly title: string;
  readonly description: string | null;
  readonly rewardAmount: Money | null;
  readonly distanceMeters: number | null;
  readonly walkMinutes: number | null;
  readonly status: OfferStatus;
  readonly expiresAt: string | null;
}

/**
 * Request body for `offers.activate()`.
 */
export interface ActivateOfferRequest {
  readonly sessionId: string;
  readonly offerId: string;
}

/**
 * Request body for `offers.complete()`. Requires the partner's own
 * transaction id (e.g. POS order id) so the backend can reconcile the
 * redemption against the partner's financial system.
 */
export interface CompleteOfferRequest {
  readonly sessionId: string;
  readonly offerId: string;
  readonly transactionId: string;
}

/**
 * Response shape for an activated or completed offer. Same fields as
 * `OfferSummary` plus lifecycle timestamps.
 */
export interface OfferResponse extends OfferSummary {
  readonly activatedAt: string | null;
  readonly completedAt: string | null;
  readonly transactionId: string | null;
}

// ---------------------------------------------------------------------------
// Module class
// ---------------------------------------------------------------------------

/**
 * Offers module surface. Partners use this to (a) discover offers
 * during a session and (b) record activation + completion events
 * that drive the billing pipeline.
 */
export class OffersModule {
  readonly #client: NeravaClient;

  constructor(client: NeravaClient) {
    this.#client = client;
  }

  /**
   * List offers available to the driver during a given charging session.
   * Returns an array — NOT paginated. The backend applies distance
   * thresholds + per-session caps so the result is always bounded.
   */
  async getForSession(sessionId: string): Promise<readonly OfferSummary[]> {
    if (!sessionId) {
      throw new Error("offers.getForSession(): sessionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: `/v1/partners/sessions/${encodeURIComponent(sessionId)}/offers`,
    });
    return camelCaseKeys(raw) as readonly OfferSummary[];
  }

  /**
   * Activate an offer for a driver. Backend creates an ExclusiveSession
   * tied to the given session id + offer id. Throws `NeravaError` with
   * code `OFFER_ALREADY_REDEEMED` if the driver has already activated
   * this offer, or `OFFER_EXPIRED` if the offer is past its expiration.
   */
  async activate(request: ActivateOfferRequest): Promise<OfferResponse> {
    if (!request.sessionId) {
      throw new Error("offers.activate(): sessionId is required");
    }
    if (!request.offerId) {
      throw new Error("offers.activate(): offerId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      method: "POST",
      path: "/v1/partners/offers/activate",
      body: {
        session_id: request.sessionId,
        offer_id: request.offerId,
      },
    });
    return camelCaseKeys(raw) as OfferResponse;
  }

  /**
   * Mark an offer as redeemed. Links the partner's own transaction id
   * (e.g. POS order id) to the offer record so the backend can
   * reconcile the redemption against the partner's financial system.
   */
  async complete(request: CompleteOfferRequest): Promise<OfferResponse> {
    if (!request.sessionId) {
      throw new Error("offers.complete(): sessionId is required");
    }
    if (!request.offerId) {
      throw new Error("offers.complete(): offerId is required");
    }
    if (!request.transactionId) {
      throw new Error("offers.complete(): transactionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      method: "POST",
      path: "/v1/partners/offers/complete",
      body: {
        session_id: request.sessionId,
        offer_id: request.offerId,
        transaction_id: request.transactionId,
      },
    });
    return camelCaseKeys(raw) as OfferResponse;
  }
}
