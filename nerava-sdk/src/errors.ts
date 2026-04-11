/**
 * Error handling for the Nerava SDK.
 *
 * Single error class: `NeravaError`. Flat, not hierarchical — consumers
 * discriminate by `code` (and optionally `status`) in their catch blocks.
 * Subclasses for every HTTP status were explicitly rejected during the
 * Step 4 design pass: they add ~7 files for a rarely-used ergonomic win.
 *
 * Error codes are an "open union" — known business and HTTP codes are
 * string literals with editor autocomplete, but the `ErrorCode` type also
 * allows any `string` so a new backend error code never breaks SDK
 * compilation. Partners doing narrow matching can use the
 * `isKnownErrorCode()` type guard.
 *
 * Body parsing handles three FastAPI response envelopes:
 *
 *   1. Custom JSON:            { "code": "...", "message": "..." }
 *   2. FastAPI HTTPException:  { "detail": "string message" }
 *   3. FastAPI validation:     { "detail": [ { "loc", "msg", "type" }, ... ] }
 *
 * Plus graceful fallback to plain text for non-JSON responses and empty
 * bodies. A `requestId` (from the backend's `x-request-id` header or
 * response body) is preserved on the error for support tracing. The raw
 * body is always attached as `rawBody` for debugging.
 */

// ---------------------------------------------------------------------------
// Error code open union
// ---------------------------------------------------------------------------

/**
 * The set of error codes the SDK knows about and offers autocomplete for.
 * This is a non-exhaustive list — backend error codes are added over time
 * and `ErrorCode` allows arbitrary strings as an escape hatch (see below).
 *
 * Grouped by origin:
 *
 * - HTTP-status-derived: set by the SDK when the backend response doesn't
 *   include a `code` field. One per common HTTP status.
 *
 * - Business-logic: emitted by the backend on specific domain errors.
 *   Stable string keys partners can match on without inspecting status.
 *
 * - SDK-originated: emitted by the SDK itself for client-side problems
 *   before any network round-trip (missing driver JWT, invalid config,
 *   network failure).
 */
export const KNOWN_ERROR_CODES = [
  // HTTP-status-derived (SDK fallback when backend body has no code)
  "UNAUTHORIZED",
  "FORBIDDEN",
  "NOT_FOUND",
  "CONFLICT",
  "VALIDATION_ERROR",
  "RATE_LIMITED",
  "SERVER_ERROR",
  "SERVICE_UNAVAILABLE",

  // Business-logic (backend-emitted)
  "SESSION_NOT_FOUND",
  "INSUFFICIENT_BALANCE",
  "CAMPAIGN_NOT_FOUND",
  "CAMPAIGN_INACTIVE",
  "WALLET_NOT_FOUND",
  "DUPLICATE_SUBMISSION",
  "OFFER_NOT_FOUND",
  "OFFER_EXPIRED",
  "OFFER_ALREADY_REDEEMED",

  // SDK-originated (never hit the network)
  "NO_DRIVER_TOKEN",
  "INVALID_CONFIG",
  "NETWORK_ERROR",
  "INVALID_RESPONSE",
] as const;

/**
 * The literal string type of a known error code. Used internally and as
 * the parameter type for `isKnownErrorCode()`.
 */
export type KnownErrorCode = (typeof KNOWN_ERROR_CODES)[number];

/**
 * Error code type. An OPEN union: known codes get autocomplete and
 * narrowing, but arbitrary strings are also allowed so a new backend
 * error code never breaks SDK compilation.
 *
 * The `(string & {})` trick prevents TypeScript from widening the union
 * to plain `string` and losing the autocomplete — see
 * https://github.com/microsoft/TypeScript/issues/29729
 */
// eslint-disable-next-line @typescript-eslint/ban-types
export type ErrorCode = KnownErrorCode | (string & {});

/**
 * Type guard for narrowing an `ErrorCode` to the known literals. Useful
 * when partners want to write exhaustive switch statements over the
 * known codes and fall through to a generic branch for unknown ones.
 *
 * ```ts
 * try {
 *   await nerava.wallet.credit({ driverId, amount: usd(500) });
 * } catch (err) {
 *   if (err instanceof NeravaError && isKnownErrorCode(err.code)) {
 *     switch (err.code) {
 *       case "INSUFFICIENT_BALANCE": // handle
 *       case "RATE_LIMITED":          // handle
 *       // ...
 *     }
 *   }
 * }
 * ```
 */
export function isKnownErrorCode(code: string): code is KnownErrorCode {
  return (KNOWN_ERROR_CODES as readonly string[]).includes(code);
}

// ---------------------------------------------------------------------------
// NeravaError
// ---------------------------------------------------------------------------

/**
 * Construction options for `NeravaError`. Callers typically do not build
 * these by hand — use the static factories `fromResponse()` and
 * `fromNetworkError()` instead.
 */
export interface NeravaErrorInit {
  /** Error code — known literal or arbitrary string (see `ErrorCode`). */
  readonly code: ErrorCode;
  /** Human-readable message for logs and error screens. */
  readonly message: string;
  /**
   * HTTP status code if this error came from a non-2xx response.
   * `undefined` when no HTTP exchange took place (e.g. network failure,
   * SDK-originated config errors). `0` is deliberately NOT used because
   * it's a valid status code in some theoretical contexts — `undefined`
   * is unambiguous.
   *
   * The `| undefined` is required under `exactOptionalPropertyTypes: true`
   * so callers can pass `status: undefined` explicitly (which is what
   * `fromNetworkError()` does to signal "no HTTP exchange occurred").
   */
  readonly status?: number | undefined;
  /**
   * Backend request id, extracted from the `x-request-id` response
   * header or a `request_id` field in the error body. Useful for
   * correlating SDK errors with backend logs during support incidents.
   */
  readonly requestId?: string | undefined;
  /**
   * The raw response body text (JSON source or plain text). Attached
   * for debugging; not included in `toString()` or log output by default.
   */
  readonly rawBody?: string | undefined;
  /**
   * Optional upstream error. Used by `fromNetworkError()` to preserve
   * the original `TypeError` / `AbortError` / DNS-failure cause.
   */
  readonly cause?: unknown;
}

/**
 * Single error class for every SDK-originated failure.
 *
 * Flat design: instead of `NotFoundError extends NeravaError`, callers
 * discriminate by the `code` or `status` fields. This keeps the module
 * count down and matches the prompt's "throw NeravaError with status,
 * code, and message" contract literally.
 *
 * `NeravaError` extends the built-in `Error` so `instanceof NeravaError`
 * works for catch-block narrowing. The `name` property is set so
 * stack traces render as `NeravaError: ...` rather than `Error: ...`.
 */
export class NeravaError extends Error {
  readonly code: ErrorCode;
  readonly status: number | undefined;
  readonly requestId: string | undefined;
  readonly rawBody: string | undefined;

  constructor(init: NeravaErrorInit) {
    // Pass `cause` to the base Error so native `Error.cause` chaining works
    // for error-wrapping scenarios (e.g. a network TypeError wrapped by
    // NeravaError.fromNetworkError).
    super(init.message, init.cause !== undefined ? { cause: init.cause } : undefined);
    this.name = "NeravaError";
    this.code = init.code;
    this.status = init.status;
    this.requestId = init.requestId;
    this.rawBody = init.rawBody;
  }

  /**
   * Short one-line representation for logs. Deliberately omits `rawBody`
   * (which can be long and contain customer PII) and `cause`.
   */
  override toString(): string {
    const parts = [
      `NeravaError [${this.code}]`,
      this.status !== undefined ? `http ${this.status}` : "no-http",
      this.message,
    ];
    if (this.requestId) {
      parts.push(`(request ${this.requestId})`);
    }
    return parts.join(" ");
  }

  /**
   * Factory: build a `NeravaError` from a non-2xx `Response`. Awaits
   * `response.text()` to read the body ONCE, then attempts JSON parsing
   * and maps the three FastAPI envelope shapes to the SDK's flat
   * `{code, message}` contract. Falls back gracefully for non-JSON
   * responses, empty bodies, and unexpected shapes.
   *
   * Callers should `await` this factory before throwing:
   *
   * ```ts
   * if (!response.ok) {
   *   throw await NeravaError.fromResponse(response, { method, path });
   * }
   * ```
   */
  static async fromResponse(
    response: Response,
    context: { method: string; path: string },
  ): Promise<NeravaError> {
    const rawBody = await readBodySafely(response);
    const parsed = parseErrorBody(rawBody);

    const code: ErrorCode = parsed.code ?? httpStatusToCode(response.status);
    const message = parsed.message
      ?? `${context.method} ${context.path} failed with HTTP ${response.status} ${response.statusText}`;
    const requestId = parsed.requestId ?? response.headers.get("x-request-id") ?? undefined;

    return new NeravaError({
      code,
      message,
      status: response.status,
      requestId,
      rawBody: rawBody || undefined,
    });
  }

  /**
   * Factory: build a `NeravaError` for a network-level failure (DNS,
   * connection refused, TLS handshake, abort, etc.). `status` is
   * `undefined` because no HTTP exchange occurred.
   */
  static fromNetworkError(
    cause: unknown,
    context: { method: string; path: string },
  ): NeravaError {
    const causeMessage = cause instanceof Error ? cause.message : String(cause);
    return new NeravaError({
      code: "NETWORK_ERROR",
      message: `${context.method} ${context.path} failed before an HTTP response was received: ${causeMessage}`,
      status: undefined,
      cause,
    });
  }
}

// ---------------------------------------------------------------------------
// Internals — body parsing
// ---------------------------------------------------------------------------

interface ParsedErrorBody {
  readonly code?: ErrorCode;
  readonly message?: string;
  readonly requestId?: string;
}

/**
 * Reads `response.text()` swallowing any stream-read failures. Returning
 * an empty string rather than throwing keeps `fromResponse()` deterministic
 * — the worst case is a less-informative error message, not a crash
 * inside the error-construction path itself.
 */
async function readBodySafely(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

/**
 * Parses an HTTP error body into the SDK's flat shape. Recognizes:
 *
 *   1. Custom JSON: `{ "code": "...", "message": "..." }`
 *   2. FastAPI string detail: `{ "detail": "..." }`
 *   3. FastAPI validation array: `{ "detail": [{loc, msg, type}, ...] }`
 *
 * Falls back to treating `rawBody` as a plain text message when it isn't
 * valid JSON. Empty bodies produce an empty `ParsedErrorBody`, letting
 * `fromResponse()` fall back to the HTTP status line.
 */
function parseErrorBody(rawBody: string): ParsedErrorBody {
  if (!rawBody) {
    return {};
  }

  let json: unknown;
  try {
    json = JSON.parse(rawBody);
  } catch {
    // Non-JSON body — surface it as the message verbatim so partners
    // still get something useful in their logs.
    return { message: rawBody };
  }

  if (!isObject(json)) {
    // JSON but not an object (e.g. a bare string or number). Stringify
    // for the message and move on.
    return { message: typeof json === "string" ? json : JSON.stringify(json) };
  }

  // Shape 1: SDK's preferred envelope — { code, message }
  const code = typeof json["code"] === "string" ? (json["code"] as string) : undefined;
  const directMessage =
    typeof json["message"] === "string" ? (json["message"] as string) : undefined;
  const requestId =
    typeof json["request_id"] === "string"
      ? (json["request_id"] as string)
      : typeof json["requestId"] === "string"
        ? (json["requestId"] as string)
        : undefined;

  // Shape 2/3: FastAPI's `detail` field (string OR array of validation errors).
  const detailMessage = extractFastApiDetail(json["detail"]);

  const message = directMessage ?? detailMessage ?? undefined;

  const result: ParsedErrorBody = {
    ...(code !== undefined ? { code } : {}),
    ...(message !== undefined ? { message } : {}),
    ...(requestId !== undefined ? { requestId } : {}),
  };
  return result;
}

/**
 * Flattens FastAPI's `detail` field into a human-readable string.
 *
 *   - string `detail` (HTTPException) → returned as-is
 *   - array of validation errors → rendered as
 *     `field.path: msg; other.path: msg`
 *   - anything else → undefined (fall through to other shapes)
 *
 * Without this helper, a FastAPI validation error would serialize to
 * `[object Object]` in the SDK's error message, which is the exact bug
 * James warned about during Step 4 scoping.
 */
function extractFastApiDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts: string[] = [];
    for (const entry of detail) {
      if (!isObject(entry)) {
        continue;
      }
      const msg = typeof entry["msg"] === "string" ? (entry["msg"] as string) : undefined;
      const loc = Array.isArray(entry["loc"])
        ? (entry["loc"] as readonly unknown[]).filter(
            (segment): segment is string | number =>
              typeof segment === "string" || typeof segment === "number",
          )
        : [];
      if (msg === undefined) {
        continue;
      }
      if (loc.length > 0) {
        parts.push(`${loc.join(".")}: ${msg}`);
      } else {
        parts.push(msg);
      }
    }
    if (parts.length > 0) {
      return parts.join("; ");
    }
  }
  return undefined;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Maps an HTTP status code to one of the `KNOWN_ERROR_CODES` literals.
 * Used only as a fallback when the error body didn't include its own
 * `code` field — which happens on stock FastAPI error envelopes that
 * just return `{ "detail": "..." }` without a structured code.
 */
function httpStatusToCode(status: number): KnownErrorCode {
  if (status === 401) return "UNAUTHORIZED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "CONFLICT";
  if (status === 422) return "VALIDATION_ERROR";
  if (status === 429) return "RATE_LIMITED";
  if (status === 503) return "SERVICE_UNAVAILABLE";
  if (status >= 500) return "SERVER_ERROR";
  return "INVALID_RESPONSE";
}
