/**
 * Mock fixtures for the sessions endpoints. Snake_case to mirror the
 * real FastAPI wire format — the SDK's `camelCaseKeys()` converter
 * handles the translation at the response boundary.
 */

export const mockSessions: readonly Record<string, unknown>[] = [
  {
    id: "sess_mock_1",
    status: "open",
    vehicle_id: "v_mock_1",
    charger_id: "c_mock_heights",
    started_at: "2026-04-11T04:30:00Z",
    ended_at: null,
    duration_seconds: null,
    kwh_delivered: null,
    lat: 31.0824,
    lng: -97.6492,
    partner_id: "partner_mock",
    driver_id: "drv_mock_1",
  },
  {
    id: "sess_mock_2",
    status: "completed",
    vehicle_id: "v_mock_2",
    charger_id: "c_mock_temple",
    started_at: "2026-04-10T22:15:00Z",
    ended_at: "2026-04-10T22:57:00Z",
    duration_seconds: 2520,
    kwh_delivered: 34.2,
    lat: 31.0975,
    lng: -97.3432,
    partner_id: "partner_mock",
    driver_id: "drv_mock_2",
  },
];
