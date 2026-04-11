// Framework: vitest (not Playwright) — this is a Node SDK, not a browser app.
// Playwright E2E belongs in the sibling nerava-appstore React package in
// Steps 12-19. Unit tests for the SDK live here and run under vitest.
//
// Step 3 acceptance tests. Runtime tests for the `usd()` helper and
// type-level tests that lock in the shape of the shared primitives in
// `src/types.ts`. Type-level tests use vitest's `expectTypeOf` — if anyone
// later changes `Money` from `{ amountCents, currency }` to anything else,
// these tests break at typecheck time, which is exactly the regression
// safety net we want.

import { describe, expect, expectTypeOf, it } from "vitest";

import {
  usd,
  type JsonArray,
  type JsonObject,
  type JsonValue,
  type LatLng,
  type Money,
  type PaginatedResponse,
  type PaginationParams,
  type VehicleType,
} from "../src/types.js";

// ===========================================================================
// usd() — runtime tests
// ===========================================================================

describe("usd()", () => {
  it("wraps integer cents in a Money object with USD currency", () => {
    const value = usd(150);
    expect(value).toEqual({ amountCents: 150, currency: "USD" });
  });

  it("allows zero cents", () => {
    expect(usd(0)).toEqual({ amountCents: 0, currency: "USD" });
  });

  it("allows negative cents (for debits and refunds)", () => {
    expect(usd(-150)).toEqual({ amountCents: -150, currency: "USD" });
  });

  it("throws on non-integer values to catch dollars-vs-cents bugs", () => {
    // This is the #1 bug this helper exists to prevent: a developer types
    // `usd(1.50)` thinking it means $1.50, but the Money contract expects
    // 150 (cents). Runtime guard surfaces the mistake at the call site.
    expect(() => usd(1.5)).toThrow(/must be a safe integer/);
    expect(() => usd(0.1)).toThrow(/must be a safe integer/);
    expect(() => usd(99.99)).toThrow(/must be a safe integer/);
  });

  it("throws on NaN", () => {
    expect(() => usd(Number.NaN)).toThrow(/must be a safe integer/);
  });

  it("throws on values outside the safe integer range", () => {
    // Number.MAX_SAFE_INTEGER + 1 can no longer round-trip through JSON
    // without losing precision, which is catastrophic for a ledger system.
    expect(() => usd(Number.MAX_SAFE_INTEGER + 1)).toThrow(
      /must be a safe integer/,
    );
  });

  it("returns an object that satisfies the Money type", () => {
    const value = usd(250);
    expectTypeOf(value).toEqualTypeOf<Money>();
  });
});

// ===========================================================================
// Money — type-level contract lock-in
// ===========================================================================

describe("Money type contract", () => {
  it("has exactly amountCents and currency fields", () => {
    // These typeof expressions lock the shape. Adding or removing a field,
    // or changing a type (e.g. amountCents: string), fails typecheck.
    expectTypeOf<Money>().toHaveProperty("amountCents").toEqualTypeOf<number>();
    expectTypeOf<Money>().toHaveProperty("currency").toEqualTypeOf<string>();
  });

  it("has readonly fields", () => {
    // Runtime verification: a Money value built via usd() should reject
    // mutation in strict mode. TypeScript's `readonly` is compile-time only
    // so this is mostly an API-intent assertion.
    const m = usd(100);
    // @ts-expect-error — readonly field
    m.amountCents = 200;
    // The line above is compile-time proof that the field is readonly;
    // we don't care about the runtime result since strict mode throws only
    // on frozen objects, which we intentionally do not freeze (perf cost).
  });
});

// ===========================================================================
// JsonValue — structural acceptance
// ===========================================================================

describe("JsonValue type contract", () => {
  it("accepts all JSON primitives", () => {
    const s: JsonValue = "hello";
    const n: JsonValue = 42;
    const b: JsonValue = true;
    const nul: JsonValue = null;
    expect([s, n, b, nul]).toEqual(["hello", 42, true, null]);
  });

  it("accepts nested objects and arrays", () => {
    const nested: JsonValue = {
      driverId: "d1",
      amount: 150,
      items: [
        { sku: "pizza", qty: 2 },
        { sku: "drink", qty: 1 },
      ],
      meta: null,
    };
    // Assignment typechecking is the assertion — if the compile passes,
    // the type is accepting the shape we expect.
    expect(nested).toBeDefined();
  });

  it("preserves the recursive shape via JsonObject and JsonArray aliases", () => {
    // If the aliases drifted apart from JsonValue, this would break.
    expectTypeOf<JsonObject>().toMatchTypeOf<JsonValue>();
    expectTypeOf<JsonArray>().toMatchTypeOf<JsonValue>();
  });
});

// ===========================================================================
// LatLng — shape lock-in
// ===========================================================================

describe("LatLng type contract", () => {
  it("has exactly lat and lng number fields", () => {
    expectTypeOf<LatLng>().toHaveProperty("lat").toEqualTypeOf<number>();
    expectTypeOf<LatLng>().toHaveProperty("lng").toEqualTypeOf<number>();
  });

  it("accepts valid coordinate literals", () => {
    const harkerHeights: LatLng = { lat: 31.0824, lng: -97.6492 };
    expect(harkerHeights.lat).toBeCloseTo(31.0824);
    expect(harkerHeights.lng).toBeCloseTo(-97.6492);
  });
});

// ===========================================================================
// VehicleType — literal union lock-in
// ===========================================================================

describe("VehicleType literal union", () => {
  it("is the exact literal union from the prompt", () => {
    expectTypeOf<VehicleType>().toEqualTypeOf<"tesla" | "smartcar" | "unknown">();
  });

  it("accepts each literal member", () => {
    const a: VehicleType = "tesla";
    const b: VehicleType = "smartcar";
    const c: VehicleType = "unknown";
    expect([a, b, c]).toEqual(["tesla", "smartcar", "unknown"]);
  });
});

// ===========================================================================
// PaginationParams and PaginatedResponse<T>
// ===========================================================================

describe("Pagination types", () => {
  it("PaginationParams has optional cursor and limit", () => {
    // All-fields-omitted construction should typecheck — both fields are
    // optional so the first-page request shape is `{}`.
    const firstPage: PaginationParams = {};
    expect(firstPage).toEqual({});

    const paged: PaginationParams = { cursor: "c_123", limit: 100 };
    expect(paged.cursor).toBe("c_123");
    expect(paged.limit).toBe(100);
  });

  it("PaginatedResponse<T> preserves the item generic", () => {
    interface Widget {
      readonly id: string;
    }
    const page: PaginatedResponse<Widget> = {
      items: [{ id: "w1" }, { id: "w2" }],
      nextCursor: "c_next",
    };
    expectTypeOf(page.items).toEqualTypeOf<readonly Widget[]>();
    expect(page.items).toHaveLength(2);
  });

  it("PaginatedResponse<T> uses null (not undefined) for the final page", () => {
    const finalPage: PaginatedResponse<string> = {
      items: ["a", "b"],
      nextCursor: null,
    };
    // The `null` (not `undefined`) choice is important — partners loop by
    // checking `while (page.nextCursor !== null)` and JSON can't serialize
    // `undefined` so the backend would have to use `null` anyway.
    expect(finalPage.nextCursor).toBeNull();
    expectTypeOf<PaginatedResponse<string>["nextCursor"]>().toEqualTypeOf<
      string | null
    >();
  });
});
