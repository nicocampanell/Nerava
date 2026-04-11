/**
 * Campaigns module — partner-scope campaign discovery.
 *
 * Auth context: `partner` (X-Partner-Key header).
 *
 * Two read-only methods:
 *
 *   - `getAvailable({ lat, lng, vehicleType? })` — discovery by proximity
 *     and optional vehicle type. Used during or right before a charging
 *     session to show the driver which campaigns could reward them.
 *
 *   - `getForSession(sessionId)` — retroactive lookup for campaigns that
 *     matched a specific session at the moment it was ingested. Used for
 *     reconciliation and debugging.
 *
 * Backend reference: `backend/app/routers/partner_api.py` and
 * `backend/app/services/campaign_service.py`. The SDK's types mirror
 * the public partner API surface, not the Pydantic schemas directly.
 *
 * Case-conversion contract: same as sessions/wallet — request query
 * params are camelCase-to-snake-case translated explicitly per field,
 * and response bodies run through `camelCaseKeys()` at the parse
 * boundary before casting to the typed response shape.
 */

import type { NeravaClient } from "../client.js";
import { camelCaseKeys } from "../internal/case.js";
import type { LatLng, Money, VehicleType } from "../types.js";

// ---------------------------------------------------------------------------
// Module-specific types (inline per the hybrid scope decision)
// ---------------------------------------------------------------------------

/**
 * Campaign status lifecycle from the backend. `paused` and `exhausted`
 * both mean "no new grants" but the root cause differs — `exhausted`
 * means the budget ran out, `paused` means the sponsor disabled it.
 */
export type CampaignStatus = "draft" | "active" | "paused" | "exhausted" | "ended";

/**
 * Summary of a campaign as returned by discovery endpoints. Not the
 * full campaign object — this is the minimal fields the SDK exposes
 * for the "which campaigns match this session" use case.
 */
export interface CampaignSummary {
  readonly id: string;
  readonly name: string;
  readonly status: CampaignStatus;
  readonly rewardAmount: Money;
  readonly maxPerDriver: number | null;
  readonly expiresAt: string | null;
  readonly sponsorName: string | null;
}

/**
 * Query parameters for `campaigns.getAvailable()`. Requires a `LatLng`
 * pair for proximity matching, plus optional vehicle-type filtering.
 */
export interface GetAvailableCampaignsRequest extends LatLng {
  readonly vehicleType?: VehicleType;
}

// ---------------------------------------------------------------------------
// Module class
// ---------------------------------------------------------------------------

/**
 * Campaigns module surface. Read-only — partners cannot create or
 * modify campaigns via the SDK (sponsor-side campaign creation lives
 * in the merchant portal / console, not the partner API).
 */
export class CampaignsModule {
  readonly #client: NeravaClient;

  constructor(client: NeravaClient) {
    this.#client = client;
  }

  /**
   * Discover campaigns available for a given location + vehicle type.
   * Returns an array of campaign summaries — NOT paginated, because the
   * backend returns at most a few dozen candidates per discovery call
   * (filtered server-side by proximity, status, trust tier, and caps).
   */
  async getAvailable(
    request: GetAvailableCampaignsRequest,
  ): Promise<readonly CampaignSummary[]> {
    const query: Record<string, string | number> = {
      lat: request.lat,
      lng: request.lng,
    };
    if (request.vehicleType !== undefined) {
      query["vehicle_type"] = request.vehicleType;
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: "/v1/partners/campaigns",
      query,
    });
    return camelCaseKeys(raw) as readonly CampaignSummary[];
  }

  /**
   * List campaigns that matched a specific session at ingest time.
   * Used for reconciliation — partners reconcile their own grant
   * records against the SDK's view of which campaigns actually
   * matched. Order matches the backend's priority ranking.
   */
  async getForSession(sessionId: string): Promise<readonly CampaignSummary[]> {
    if (!sessionId) {
      throw new Error("campaigns.getForSession(): sessionId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "partner",
      path: `/v1/partners/sessions/${encodeURIComponent(sessionId)}/campaigns`,
    });
    return camelCaseKeys(raw) as readonly CampaignSummary[];
  }
}
