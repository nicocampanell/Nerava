// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// TODO(step-6): real wallet module tests land here once src/modules/wallet.ts
// exists. Replace this stub then. Covered flows: getBalance, getTransactions,
// credit, debit, requestPayout — all mocked against mock/server.ts.
import { describe, it, expect } from "vitest";

describe.skip("wallet module (placeholder — Step 6)", () => {
  it("scaffold placeholder — real wallet tests land in Step 6", () => {
    expect(true).toBe(true);
  });
});
