// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Tests for the internal case-conversion utility used by every module
// to translate snake_case backend responses into camelCase public types.

import { describe, expect, it } from "vitest";

import { camelCaseKeys, toCamelCase } from "../src/internal/case.js";

// ===========================================================================
// toCamelCase string-level
// ===========================================================================

describe("toCamelCase", () => {
  it("converts single-underscore snake_case", () => {
    expect(toCamelCase("driver_id")).toBe("driverId");
    expect(toCamelCase("amount_cents")).toBe("amountCents");
    expect(toCamelCase("started_at")).toBe("startedAt");
  });

  it("converts multi-underscore snake_case", () => {
    expect(toCamelCase("some_deeply_nested_field")).toBe("someDeeplyNestedField");
    expect(toCamelCase("a_b_c_d")).toBe("aBCD");
  });

  it("passes through already-camelCase strings unchanged", () => {
    expect(toCamelCase("nextCursor")).toBe("nextCursor");
    expect(toCamelCase("vehicleId")).toBe("vehicleId");
  });

  it("passes through strings without underscores unchanged", () => {
    expect(toCamelCase("id")).toBe("id");
    expect(toCamelCase("status")).toBe("status");
    expect(toCamelCase("HTTPStatus")).toBe("HTTPStatus");
  });

  it("handles underscores before digits", () => {
    expect(toCamelCase("field_1")).toBe("field1");
    expect(toCamelCase("item_2_name")).toBe("item2Name");
  });

  it("handles empty string", () => {
    expect(toCamelCase("")).toBe("");
  });
});

// ===========================================================================
// camelCaseKeys recursive-level
// ===========================================================================

describe("camelCaseKeys", () => {
  it("converts all keys of a flat plain object", () => {
    expect(
      camelCaseKeys({
        driver_id: "drv_1",
        amount_cents: 500,
        currency: "USD",
      }),
    ).toEqual({
      driverId: "drv_1",
      amountCents: 500,
      currency: "USD",
    });
  });

  it("recursively converts keys inside nested objects", () => {
    expect(
      camelCaseKeys({
        driver_id: "drv_1",
        balance: {
          amount_cents: 2500,
          currency: "USD",
        },
        lifetime_earned: {
          amount_cents: 15000,
          currency: "USD",
        },
      }),
    ).toEqual({
      driverId: "drv_1",
      balance: { amountCents: 2500, currency: "USD" },
      lifetimeEarned: { amountCents: 15000, currency: "USD" },
    });
  });

  it("recursively converts keys inside arrays of objects", () => {
    expect(
      camelCaseKeys({
        items: [
          { driver_id: "d1", amount_cents: 100 },
          { driver_id: "d2", amount_cents: 200 },
        ],
        next_cursor: null,
      }),
    ).toEqual({
      items: [
        { driverId: "d1", amountCents: 100 },
        { driverId: "d2", amountCents: 200 },
      ],
      nextCursor: null,
    });
  });

  it("preserves primitive values at every depth", () => {
    expect(camelCaseKeys("bare string")).toBe("bare string");
    expect(camelCaseKeys(42)).toBe(42);
    expect(camelCaseKeys(true)).toBe(true);
    expect(camelCaseKeys(null)).toBe(null);
    expect(camelCaseKeys(undefined)).toBe(undefined);
  });

  it("preserves arrays of primitives", () => {
    expect(camelCaseKeys([1, 2, 3])).toEqual([1, 2, 3]);
    expect(camelCaseKeys(["a", "b"])).toEqual(["a", "b"]);
  });

  it("handles empty objects and arrays", () => {
    expect(camelCaseKeys({})).toEqual({});
    expect(camelCaseKeys([])).toEqual([]);
  });

  it("preserves null values inside objects", () => {
    expect(camelCaseKeys({ ended_at: null, driver_id: "x" })).toEqual({
      endedAt: null,
      driverId: "x",
    });
  });

  it("does not walk into class instances (e.g. Date)", () => {
    const date = new Date("2026-04-11T00:00:00Z");
    // Dates wrapped in a plain object should be passed through as-is
    // — the SDK never expects Date instances from JSON, but if they
    // appear (e.g. from a test mock) they should survive unchanged
    // rather than being destructured into a plain object of {}.
    const result = camelCaseKeys({ created_at: date }) as {
      createdAt: Date;
    };
    expect(result.createdAt).toBe(date);
    expect(result.createdAt instanceof Date).toBe(true);
  });

  it("does not mutate the input object", () => {
    const input = {
      driver_id: "d1",
      nested: { some_field: "x" },
    };
    const snapshot = JSON.parse(JSON.stringify(input));
    camelCaseKeys(input);
    expect(input).toEqual(snapshot);
  });
});
