/**
 * Mock fixtures for the intelligence endpoint.
 *
 * ⚠️ PENDING backend: the real endpoint does not exist yet. This
 * fixture doubles as the specification for the eventual backend
 * implementation — when the real endpoint ships, its response
 * shape should match this fixture exactly (snake_case on the wire,
 * matching the SDK's `SessionIntelligenceResponse` type after
 * camelCase conversion).
 */

export const mockIntelligence = {
  session_id: "sess_mock_1",
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
      campaign_id: "camp_mock_heights_pizza",
      campaign_name: "Harker Heights Free Pizza",
      matched_at: "2026-04-11T04:30:00Z",
      priority: 1,
      evaluation_notes: null,
    },
  ],
  evaluated_at: "2026-04-11T04:30:05Z",
  backend_version: "2026.04.11-mock",
};
