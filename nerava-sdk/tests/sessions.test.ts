// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 5 acceptance tests. Exercises SessionsModule against a mock fetch
// and verifies URL construction, body shape (snake_case conversion),
// header injection (partner context), query param serialization,
// pagination limit guard, and idempotencyKey pass-through.

import { beforeEach, describe, expect, it, vi, type MockedFunction } from "vitest";

import { AuthManager } from "../src/auth.js";
import { NeravaClient } from "../src/client.js";
import {
  SessionsModule,
  type SessionResponse,
  type SessionStatus,
} from "../src/modules/sessions.js";
import { latLng } from "../src/types.js";

const TEST_API_KEY = "nrv_pk_testkey1234567890abcdef";
const TEST_BASE_URL = "http://localhost:3001";

type MockFetch = MockedFunction<typeof fetch>;

/**
 * Builds a mock fetch that responds with the given JSON payload and
 * records every outbound call for assertion.
 */
function makeMockFetch(responseBody: unknown): MockFetch {
  return vi.fn<typeof fetch>().mockImplementation(async () => {
    return new Response(JSON.stringify(responseBody), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
}

function buildSessionsModule(mockFetch: MockFetch): SessionsModule {
  const auth = new AuthManager({ apiKey: TEST_API_KEY });
  const client = new NeravaClient({
    auth,
    baseUrl: TEST_BASE_URL,
    fetch: mockFetch,
  });
  return new SessionsModule(client);
}

function firstCall(mockFetch: MockFetch): { url: URL; init: RequestInit } {
  const call = mockFetch.mock.calls[0];
  if (!call) throw new Error("mockFetch was not called");
  const url = new URL(String(call[0]));
  const init = call[1] ?? {};
  return { url, init };
}

function headersOf(init: RequestInit): Record<string, string> {
  return (init.headers ?? {}) as Record<string, string>;
}

/**
 * Backend-shaped (snake_case) fixture. Mirrors what FastAPI actually
 * returns on the wire. The SDK converts this to camelCase via
 * `camelCaseKeys()` inside each module method, so consumer-facing
 * assertions use the camelCase form (`FIXTURE_SESSION`).
 */
const FIXTURE_SESSION_WIRE = {
  id: "sess_abc",
  status: "open",
  vehicle_id: "v_1",
  charger_id: "c_1",
  started_at: "2026-04-11T04:30:00Z",
  ended_at: null,
  duration_seconds: null,
  kwh_delivered: null,
  lat: 31.0824,
  lng: -97.6492,
  partner_id: "partner_1",
  driver_id: null,
};

/** Camel-case shape the SDK exposes to consumers. */
const FIXTURE_SESSION: SessionResponse = {
  id: "sess_abc",
  status: "open",
  vehicleId: "v_1",
  chargerId: "c_1",
  startedAt: "2026-04-11T04:30:00Z",
  endedAt: null,
  durationSeconds: null,
  kwhDelivered: null,
  lat: 31.0824,
  lng: -97.6492,
  partnerId: "partner_1",
  driverId: null,
};

// ===========================================================================
// submit
// ===========================================================================

describe("sessions.submit", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(FIXTURE_SESSION_WIRE);
  });

  it("POSTs /v1/partners/sessions with snake_case body and converts response back to camelCase", async () => {
    const sessions = buildSessionsModule(mockFetch);

    const result = await sessions.submit({
      vehicleId: "v_123",
      chargerId: "c_456",
      lat: 31.0824,
      lng: -97.6492,
    });

    // Backend returned snake_case wire fixture; SDK converts to camelCase.
    expect(result).toEqual(FIXTURE_SESSION);
    // Spot-check specific fields to prove the conversion happened and
    // consumers don't see snake_case leak through.
    expect(result.vehicleId).toBe("v_1");
    expect(result.startedAt).toBe("2026-04-11T04:30:00Z");
    // @ts-expect-error — snake_case fields must NOT exist on the typed shape
    expect(result.vehicle_id).toBeUndefined();
    expect(mockFetch).toHaveBeenCalledOnce();

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions");
    expect(init.method).toBe("POST");

    const headers = headersOf(init);
    expect(headers["X-Partner-Key"]).toBe(TEST_API_KEY);
    expect(headers["Authorization"]).toBeUndefined();
    expect(headers["Content-Type"]).toBe("application/json");

    // The SDK converts camelCase to the backend's snake_case contract —
    // the backend's Pydantic schemas use snake_case field names, and the
    // SDK is the single translation layer.
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({
      vehicle_id: "v_123",
      charger_id: "c_456",
      lat: 31.0824,
      lng: -97.6492,
    });
    // idempotency_key is NOT set when caller omits it.
    expect(body).not.toHaveProperty("idempotency_key");
  });

  it("includes idempotency_key in the body when supplied", async () => {
    const sessions = buildSessionsModule(mockFetch);

    await sessions.submit({
      vehicleId: "v_1",
      chargerId: "c_1",
      lat: 31,
      lng: -97,
      idempotencyKey: "partner-trace-xyz",
    });

    const body = JSON.parse(String(firstCall(mockFetch).init.body));
    expect(body["idempotency_key"]).toBe("partner-trace-xyz");
  });

  it("accepts coordinates produced by the latLng() helper", async () => {
    const sessions = buildSessionsModule(mockFetch);
    const here = latLng(31.0824, -97.6492);
    await sessions.submit({
      vehicleId: "v_1",
      chargerId: "c_1",
      ...here,
    });
    const body = JSON.parse(String(firstCall(mockFetch).init.body));
    expect(body["lat"]).toBeCloseTo(31.0824);
    expect(body["lng"]).toBeCloseTo(-97.6492);
  });
});

// ===========================================================================
// get
// ===========================================================================

describe("sessions.get", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(FIXTURE_SESSION_WIRE);
  });

  it("GETs /v1/partners/sessions/{id} with partner auth", async () => {
    const sessions = buildSessionsModule(mockFetch);
    const result = await sessions.get("sess_abc");

    expect(result).toEqual(FIXTURE_SESSION);
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions/sess_abc");
    // Default method is GET; fetch init will have no method set or "GET".
    expect(init.method === undefined || init.method === "GET").toBe(true);
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
  });

  it("URL-encodes session ids containing reserved characters", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await sessions.get("sess/with spaces?and=weird");
    const { url } = firstCall(mockFetch);
    expect(url.pathname).toBe(
      `/v1/partners/sessions/${encodeURIComponent("sess/with spaces?and=weird")}`,
    );
  });

  it("throws on empty sessionId", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await expect(sessions.get("")).rejects.toThrow(/sessionId is required/);
  });
});

// ===========================================================================
// list
// ===========================================================================

describe("sessions.list", () => {
  const PAGE_FIXTURE_WIRE = {
    items: [FIXTURE_SESSION_WIRE],
    next_cursor: null,
  };

  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch(PAGE_FIXTURE_WIRE);
  });

  it("returns a paginated response with partner auth (snake→camel conversion)", async () => {
    const sessions = buildSessionsModule(mockFetch);
    const page = await sessions.list();
    // Backend sends `next_cursor: null` — SDK exposes `nextCursor: null`.
    expect(page.items).toHaveLength(1);
    expect(page.nextCursor).toBeNull();
    // And every field inside the item has been converted too.
    expect(page.items[0]).toEqual(FIXTURE_SESSION);

    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
  });

  it("serializes all filter fields as snake_case query params", async () => {
    const sessions = buildSessionsModule(mockFetch);
    const status: SessionStatus = "completed";
    await sessions.list({
      cursor: "c_xyz",
      limit: 50,
      status,
      vehicleId: "v_1",
      since: "2026-04-01T00:00:00Z",
      until: "2026-04-11T00:00:00Z",
      vehicleType: "tesla",
    });

    const { url } = firstCall(mockFetch);
    expect(url.searchParams.get("cursor")).toBe("c_xyz");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("status")).toBe("completed");
    expect(url.searchParams.get("vehicle_id")).toBe("v_1");
    expect(url.searchParams.get("since")).toBe("2026-04-01T00:00:00Z");
    expect(url.searchParams.get("until")).toBe("2026-04-11T00:00:00Z");
    expect(url.searchParams.get("vehicle_type")).toBe("tesla");
  });

  it("guards against limit > 200 before calling the backend", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await expect(sessions.list({ limit: 500 })).rejects.toThrow(/limit must be ≤ 200/);
    // Importantly: the guard fires before any fetch call.
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("accepts limit of exactly 200 (boundary, guard is `> 200` not `>= 200`)", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await expect(sessions.list({ limit: 200 })).resolves.toBeDefined();
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("omits undefined filter fields from the query string", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await sessions.list({ cursor: "c_only" });
    const { url } = firstCall(mockFetch);
    expect(url.searchParams.get("cursor")).toBe("c_only");
    expect(url.searchParams.has("status")).toBe(false);
    expect(url.searchParams.has("vehicle_id")).toBe(false);
  });
});

// ===========================================================================
// complete
// ===========================================================================

describe("sessions.complete", () => {
  let mockFetch: MockFetch;

  beforeEach(() => {
    mockFetch = makeMockFetch({ ...FIXTURE_SESSION_WIRE, status: "completed" });
  });

  it("PATCHes /v1/partners/sessions/{id} with status: completed and partner auth", async () => {
    const sessions = buildSessionsModule(mockFetch);
    const result = await sessions.complete("sess_abc");

    expect(result.status).toBe("completed");
    // Verify snake→camel conversion applies on complete too.
    expect(result.vehicleId).toBe("v_1");
    expect(result.startedAt).toBe("2026-04-11T04:30:00Z");
    const { url, init } = firstCall(mockFetch);
    expect(url.pathname).toBe("/v1/partners/sessions/sess_abc");
    expect(init.method).toBe("PATCH");
    expect(headersOf(init)["X-Partner-Key"]).toBe(TEST_API_KEY);
    expect(JSON.parse(String(init.body))).toEqual({ status: "completed" });
  });

  it("throws on empty sessionId", async () => {
    const sessions = buildSessionsModule(mockFetch);
    await expect(sessions.complete("")).rejects.toThrow(/sessionId is required/);
  });
});

// ===========================================================================
// latLng() runtime guard tests (deferred from Step 3, co-located with
// first consumer per the Step 3 CodeRabbit recommendation)
// ===========================================================================

describe("latLng() helper (Step 3 deferred tests)", () => {
  it("accepts valid coordinates", () => {
    expect(latLng(0, 0)).toEqual({ lat: 0, lng: 0 });
    expect(latLng(31.0824, -97.6492)).toEqual({ lat: 31.0824, lng: -97.6492 });
    expect(latLng(-90, -180)).toEqual({ lat: -90, lng: -180 });
    expect(latLng(90, 180)).toEqual({ lat: 90, lng: 180 });
  });

  it("rejects lat outside [-90, 90]", () => {
    expect(() => latLng(91, 0)).toThrow(/lat must be/);
    expect(() => latLng(-91, 0)).toThrow(/lat must be/);
    expect(() => latLng(200, 0)).toThrow(/Did you swap lat and lng/);
  });

  it("rejects lng outside [-180, 180]", () => {
    expect(() => latLng(0, 181)).toThrow(/lng must be/);
    expect(() => latLng(0, -181)).toThrow(/lng must be/);
  });

  it("rejects non-finite values", () => {
    expect(() => latLng(Number.NaN, 0)).toThrow();
    expect(() => latLng(0, Number.NaN)).toThrow();
    expect(() => latLng(Number.POSITIVE_INFINITY, 0)).toThrow();
  });
});
