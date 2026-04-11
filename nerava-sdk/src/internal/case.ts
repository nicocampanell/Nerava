/**
 * Internal case-conversion utilities.
 *
 * Not re-exported from `src/index.ts`. These are SDK internals that
 * handle the snake_case ↔ camelCase translation at the network boundary
 * so the public TypeScript surface can use idiomatic camelCase while the
 * backend stays on snake_case.
 *
 * Why this lives here:
 *
 *   - Every module that touches a backend response needs this mapping.
 *     Without it, `response.startedAt` is `undefined` while `response.started_at`
 *     silently holds the real value — a latent bug that only fires when
 *     a consumer reads the mistyped field.
 *
 *   - A single small utility is simpler to audit than six copies of the
 *     same mapping logic spread across modules 5-8.
 *
 *   - Placed under `src/internal/*` to signal "not public surface" and so
 *     refactoring doesn't accidentally break the SDK's export contract.
 */

/**
 * Converts a single snake_case string to camelCase.
 *
 *   "started_at"       → "startedAt"
 *   "driver_id"        → "driverId"
 *   "amount_cents"     → "amountCents"
 *   "nextCursor"       → "nextCursor"   (already camelCase — unchanged)
 *   "HTTPStatus"       → "HTTPStatus"   (no underscores — unchanged)
 *   "__private"        → "_Private"     (edge case, not expected from backend)
 *
 * Leading underscores are preserved as-is. Trailing underscores are dropped
 * along with the character that follows (none → no-op). This matches the
 * Python backend's naming conventions which never use leading/trailing
 * underscores in public response fields.
 */
export function toCamelCase(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_match, char: string) => char.toUpperCase());
}

/**
 * Recursively converts all object keys from snake_case to camelCase,
 * preserving arrays and primitive values. Used at the response-parse
 * boundary of every SDK module method.
 *
 * Behavior:
 *
 *   - Objects: every key is converted, values recurse.
 *   - Arrays: every element recurses, order preserved.
 *   - Primitives (string, number, boolean, null): returned as-is.
 *   - `undefined`: returned as-is (though JSON responses don't contain
 *     `undefined`, this keeps the function total for test ergonomics).
 *   - `Date`, `Map`, `Set`, class instances: never expected in a JSON
 *     response, but if present they're returned as-is without recursing.
 *
 * This is deliberately a type-erasing function (`unknown → unknown`).
 * Callers must cast the result to the module's response type. That's
 * safe because the caller knows exactly which response shape to expect
 * from a given endpoint — the SDK has already committed to that contract
 * via the method's return type.
 */
export function camelCaseKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(camelCaseKeys);
  }
  if (value !== null && typeof value === "object") {
    // Guard against class instances (Date, Map, Set) — only walk plain
    // objects whose prototype is Object.prototype (or null, for
    // Object.create(null) results).
    const proto = Object.getPrototypeOf(value) as unknown;
    if (proto !== null && proto !== Object.prototype) {
      return value;
    }
    const result: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      result[toCamelCase(key)] = camelCaseKeys(val);
    }
    return result;
  }
  return value;
}
