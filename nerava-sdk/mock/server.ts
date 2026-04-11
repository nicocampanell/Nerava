/**
 * Mock HTTP server for the Nerava SDK.
 *
 * Runs on localhost:3001 by default. Partners and SDK developers can
 * point the SDK at this URL via the `baseUrl` config option to exercise
 * every public method against canned fixtures without hitting the real
 * backend.
 *
 * Usage:
 *
 *   npm run mock            # start the server, ctrl+c to stop
 *
 *   const nerava = new NeravaClient({
 *     auth: new AuthManager({ apiKey: 'nrv_pk_test_...' }),
 *     baseUrl: 'http://localhost:3001',
 *   });
 *
 * Dependencies: none outside Node's built-in `http` module. No
 * express / fastify / koa — keeps the SDK package dependency-free.
 *
 * Design principles:
 *
 *   - Fixtures live in `mock/fixtures/*.ts`, server.ts just routes.
 *   - Auth is validated loosely — the server checks for the presence
 *     of `X-Partner-Key` or `Authorization: Bearer *`, NOT the actual
 *     value, so any test key works.
 *   - Responses are always snake_case to mirror the real backend.
 *   - Error paths (404, 401, etc.) return the backend-shaped error
 *     envelope `{code, message, request_id}` so the SDK's error
 *     parser exercises the full FastAPI-compatible path.
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";

import { mockCampaigns } from "./fixtures/campaigns.js";
import { mockIntelligence } from "./fixtures/intelligence.js";
import { mockOfferActivated, mockOfferCompleted, mockOffers } from "./fixtures/offers.js";
import { mockSessions } from "./fixtures/sessions.js";
import {
  mockPayout,
  mockWalletBalance,
  mockWalletTransactions,
} from "./fixtures/wallet.js";

const DEFAULT_PORT = 3001;

// ---------------------------------------------------------------------------
// Route handler type
// ---------------------------------------------------------------------------

interface Route {
  readonly method: string;
  /** Regex matched against the pathname; capture groups become params. */
  readonly pattern: RegExp;
  readonly auth: "partner" | "driver";
  readonly handler: (params: readonly string[], body: unknown) => unknown;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

const routes: readonly Route[] = [
  // ===== sessions =====
  {
    method: "POST",
    pattern: /^\/v1\/partners\/sessions$/,
    auth: "partner",
    handler: (_params, body) => {
      const input = (body ?? {}) as Record<string, unknown>;
      return {
        ...mockSessions[0],
        vehicle_id: input["vehicle_id"] ?? mockSessions[0]?.["vehicle_id"],
        charger_id: input["charger_id"] ?? mockSessions[0]?.["charger_id"],
        lat: input["lat"] ?? mockSessions[0]?.["lat"],
        lng: input["lng"] ?? mockSessions[0]?.["lng"],
      };
    },
  },
  {
    method: "GET",
    pattern: /^\/v1\/partners\/sessions$/,
    auth: "partner",
    handler: () => ({
      items: mockSessions,
      next_cursor: null,
    }),
  },
  {
    method: "GET",
    pattern: /^\/v1\/partners\/sessions\/([^/]+)\/campaigns$/,
    auth: "partner",
    handler: () => mockCampaigns,
  },
  {
    method: "GET",
    pattern: /^\/v1\/partners\/sessions\/([^/]+)\/offers$/,
    auth: "partner",
    handler: () => mockOffers,
  },
  {
    method: "GET",
    pattern: /^\/v1\/partners\/sessions\/([^/]+)\/intelligence$/,
    auth: "partner",
    handler: () => mockIntelligence,
  },
  {
    method: "GET",
    pattern: /^\/v1\/partners\/sessions\/([^/]+)$/,
    auth: "partner",
    handler: (params) => ({
      ...mockSessions[0],
      id: params[0],
    }),
  },
  {
    method: "PATCH",
    pattern: /^\/v1\/partners\/sessions\/([^/]+)$/,
    auth: "partner",
    handler: (params) => ({
      ...mockSessions[0],
      id: params[0],
      status: "completed",
      ended_at: "2026-04-11T04:45:00Z",
      duration_seconds: 900,
      kwh_delivered: 12.5,
    }),
  },

  // ===== campaigns =====
  {
    method: "GET",
    pattern: /^\/v1\/partners\/campaigns$/,
    auth: "partner",
    handler: () => mockCampaigns,
  },

  // ===== offers =====
  {
    method: "POST",
    pattern: /^\/v1\/partners\/offers\/activate$/,
    auth: "partner",
    handler: () => mockOfferActivated,
  },
  {
    method: "POST",
    pattern: /^\/v1\/partners\/offers\/complete$/,
    auth: "partner",
    handler: (_params, body) => {
      const input = (body ?? {}) as Record<string, unknown>;
      return {
        ...mockOfferCompleted,
        transaction_id: input["transaction_id"] ?? "txn_partner_pos_99",
      };
    },
  },

  // ===== wallet =====
  {
    method: "GET",
    pattern: /^\/v1\/wallet\/balance$/,
    auth: "driver",
    handler: () => mockWalletBalance,
  },
  {
    method: "GET",
    pattern: /^\/v1\/wallet\/transactions$/,
    auth: "driver",
    handler: () => ({
      items: mockWalletTransactions,
      next_cursor: null,
    }),
  },
  {
    method: "POST",
    pattern: /^\/v1\/wallet\/credit$/,
    auth: "driver",
    handler: () => mockWalletTransactions[0],
  },
  {
    method: "POST",
    pattern: /^\/v1\/wallet\/debit$/,
    auth: "driver",
    handler: () => mockWalletTransactions[1] ?? mockWalletTransactions[0],
  },
  {
    method: "POST",
    pattern: /^\/v1\/wallet\/payout$/,
    auth: "driver",
    handler: () => mockPayout,
  },
];

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

function errorResponse(
  code: string,
  message: string,
  requestId = "req_mock",
): string {
  return JSON.stringify({ code, message, request_id: requestId });
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return await new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("error", reject);
    req.on("end", () => {
      const text = Buffer.concat(chunks).toString("utf8");
      if (!text) {
        resolve(undefined);
        return;
      }
      try {
        resolve(JSON.parse(text));
      } catch {
        // Surface as an empty body — per-route handlers ignore it.
        resolve(undefined);
      }
    });
  });
}

function sendJson(
  res: ServerResponse,
  status: number,
  payload: unknown,
): void {
  const body = typeof payload === "string" ? payload : JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body).toString(),
    "x-request-id": "req_mock",
  });
  res.end(body);
}

/**
 * Starts the mock server on the given port. Returns a promise that
 * resolves with a `stop()` function once the server is listening.
 *
 * Exported so tests can spawn the server in-process, exercise it via
 * the SDK, and tear it down cleanly — no shell scripting or port
 * pinning required.
 */
export async function startMockServer(
  port: number = DEFAULT_PORT,
): Promise<{ readonly port: number; readonly stop: () => Promise<void> }> {
  const server = createServer((req, res) => {
    void handleRequest(req, res);
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, () => resolve());
  });

  const address = server.address();
  const actualPort =
    typeof address === "object" && address !== null ? address.port : port;

  return {
    port: actualPort,
    stop: () =>
      new Promise<void>((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

async function handleRequest(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<void> {
  const method = req.method ?? "GET";
  const rawUrl = req.url ?? "/";
  // Parse URL relative to a synthetic host so URLSearchParams works.
  const url = new URL(rawUrl, "http://localhost");
  const pathname = url.pathname;

  // Match a route.
  let matched: { route: Route; params: readonly string[] } | undefined;
  for (const route of routes) {
    if (route.method !== method) continue;
    const match = route.pattern.exec(pathname);
    if (match) {
      matched = { route, params: match.slice(1) };
      break;
    }
  }

  if (!matched) {
    sendJson(
      res,
      404,
      errorResponse("NOT_FOUND", `No mock route for ${method} ${pathname}`),
    );
    return;
  }

  // Auth check — presence only, not value.
  const headers = req.headers;
  if (matched.route.auth === "partner") {
    const key = headers["x-partner-key"];
    if (!key || typeof key !== "string") {
      sendJson(
        res,
        401,
        errorResponse(
          "UNAUTHORIZED",
          "Missing X-Partner-Key header on a partner-scope mock route",
        ),
      );
      return;
    }
  } else {
    const authHeader = headers["authorization"];
    if (
      !authHeader ||
      typeof authHeader !== "string" ||
      !authHeader.startsWith("Bearer ")
    ) {
      sendJson(
        res,
        401,
        errorResponse(
          "UNAUTHORIZED",
          "Missing Authorization: Bearer <jwt> on a driver-scope mock route",
        ),
      );
      return;
    }
  }

  // Read body (if any) and invoke the handler.
  const body = await readJsonBody(req);
  const payload = matched.route.handler(matched.params, body);
  sendJson(res, 200, payload);
}

// ---------------------------------------------------------------------------
// CLI entry — only runs when this file is executed directly, not when
// imported as a module.
//
// Uses `pathToFileURL` to compare full file URLs. When this file is
// imported by examples/basic-integration.ts or tests, `import.meta.url`
// is the server.ts file URL but `process.argv[1]` is the entry-point
// script URL — they don't match and `isDirectRun` is false. When run
// directly via `tsx mock/server.ts` or `node dist/mock/server.js`,
// they do match.
// ---------------------------------------------------------------------------

const entryPoint = process.argv[1];
const isDirectRun =
  entryPoint !== undefined && import.meta.url === pathToFileURL(entryPoint).href;

if (isDirectRun) {
  const port = Number(process.env["PORT"]) || DEFAULT_PORT;
  startMockServer(port)
    .then(({ port: actualPort }) => {
      // eslint-disable-next-line no-console -- CLI entry point, logging is the product
      console.log(`[nerava/sdk mock] listening on http://localhost:${actualPort}`);
    })
    .catch((err: unknown) => {
      // eslint-disable-next-line no-console -- CLI entry point
      console.error("[nerava/sdk mock] failed to start:", err);
      process.exit(1);
    });
}
