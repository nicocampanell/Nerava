// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 8 acceptance tests. The intelligence backend endpoint is PENDING
// — tests use mock fetch fixtures to lock in the SDK's type contract so
// that when the backend ships, no SDK changes are needed.

import { beforeEach, describe, expect, it, vi, type MockedFunction } from "vitest";

import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import {
  IntelligenceModule,
  type SessionIntelligenceResponse,
} from "../src/modules/intelligence.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
const TEST_BASE_URL = "http://localhost:3001";

type MockFetch = MockedFunction<typeof fetch>;

function makeMockFetch(responseBody: unknown): MockFetch {
  return vi.fn<typeof fetch>().mockImplementation(async () => {
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function buildModule(mockFetch: MockFetch): IntelligenceModule {
  const auth = new AuthManager({ apiKey: TEST_API_KEY });
  const client = new NeravaClient({ auth, baseUrl: TEST_BASE_URL, fetch: mockFetch });
  return new IntelligenceModule(client);
}

function firstCall(mockFetch: MockFetch): { url: URL; init: RequestInit } {
  const call = mockFetch.mock.calls[0];
  if (!call) throw new Error("mockFetch was not called");
  return { url: new URL(String(call[0])), init: call[1] ?? {} };
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

// Backend-shaped (snake_case) fixture mirroring what the eventual
// /v1/partners/sessions/{id}/intelligence endpoint will return.
const INTEL_WIRE = {
  session_id: "sess_abc",
  quality_score: 92,
  quality_bucket: "verified",
  anti_fraud: {
    location_consistent: true,
    telemetry_consistent: true,
    vehicle_authorized: true,
    charger_whitelisted: true,
    within_expected_window: true,
    duplicate_detected: false,
  },
  matched_grants: [
    {
      campaign_id: "camp_1",
      campaign_name: "Harker Heights Free Pizza",
      matched_at: "2026-04-11T04:30:00Z",
      priority: 1,
      evaluation_notes: null,
    },
  ],
  evaluated_at: "2026-04-11T04:30:05Z",
  backend_version: "2026.04.11",
};

const INTEL_CAMEL: SessionIntelligenceResponse = {
  sessionId: "sess_abc",
  qualityScore: 92,
  qualityBucket: "verified",
  antiFraud: {
    locationConsistent: true,
    telemetryConsistent: true,
    vehicleAuthorized: true,
    chargerWhitelisted: true,
    withinExpectedWindow: true,
    duplicateDetected: false,
  },
  matchedGrants: [
    {
      campaignId: "camp_1",
      campaignName: "Harker Heights Free Pizza",
      matchedAt: "2026-04-11T04:30:00Z",
      priority: 1,
      evaluationNotes: null,
    },
  ],
  evaluatedAt: "2026-04-11T04:30:05Z",
  backendVersion: "2026.04.11",
};

// ===========================================================================
// intelligence.getSessionData
// ===========================================================================

describe("intelligence.getSessionData (PENDING backend, mock-only)", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(INTEL_WIRE);
  });

  it("GETs /v1/partners/sessions/{id}/intelligence with partner auth", async () => {
    const intelligence = buildModule(mockFetch);
    const result = await intelligence.getSessionData("sess_abc");

    expect(result).toEqual(INTEL_CAMEL);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions/sess_abc/intelligence");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
    // Driver JWT must NOT be sent — this is partner-scope.
    expect(headersOf(init)["Authorization"]).toBeUndefined();
  });

  it("recursively converts snake_case response body to camelCase", async () => {
    const intelligence = buildModule(mockFetch);
    const result = await intelligence.getSessionData("sess_abc");

    // Spot-check nested anti_fraud object was converted.
    expect(result.antiFraud.locationConsistent).toBe(true);
    expect(result.antiFraud.duplicateDetected).toBe(false);
    // Spot-check array of objects inside matched_grants was converted.
    expect(result.matchedGrants).toHaveLength(1);
    expect(result.matchedGrants[0]?.campaignId).toBe("camp_1");
    expect(result.matchedGrants[0]?.matchedAt).toBe("2026-04-11T04:30:00Z");
    // Spot-check top-level fields.
    expect(result.qualityScore).toBe(92);
    expect(result.backendVersion).toBe("2026.04.11");
  });

  it("URL-encodes session ids with reserved characters", async () => {
    const intelligence = buildModule(mockFetch);
    await intelligence.getSessionData("sess/with spaces");
    expect(firstCall(mockFetch).url.pathname).toBe(
      `/v1/partners/sessions/${encodeURIComponent("sess/with spaces")}/intelligence`,
    );
  });

  it("throws on empty sessionId", async () => {
    const intelligence = buildModule(mockFetch);
    await expect(intelligence.getSessionData("")).rejects.toThrow(
      /sessionId is required/,
    );
  });
});
