// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// TODO(step-7): real campaigns + offers module tests land here once
// src/modules/campaigns.ts and src/modules/offers.ts exist. Replace this
// stub then. Covered flows: getAvailable, getForSession for campaigns;
// getForSession, activate, complete for offers — all mocked against
// mock/server.ts.
import { describe, it, expect } from "vitest";

describe.skip("campaigns + offers modules (placeholder — Step 7)", () => {
  it("scaffold placeholder — real tests land in Step 7", () => {
    expect(true).toBe(true);
  });
});
