# @nerava/sdk

Official TypeScript SDK for the Nerava EV charging intelligence and incentive platform.

The SDK wraps the partner-facing REST API behind a small set of typed modules. Partners install it into their Node services or TypeScript apps and call methods instead of hand-rolling HTTP requests, auth headers, or response parsing.

- **Written in TypeScript**, ships `.d.ts` for partner consumers
- **Zero runtime dependencies** — native `fetch` on Node 18.17+
- **Mock server included** — exercise every endpoint without backend credentials
- **Flat error model** — one `NeravaError` class, discriminated by `code`
- **Cent-integer money** — no floating-point dollars at any boundary

## Requirements

- **Node 18.17 or newer** (native `fetch`, `Response`, `Headers` built in)
- A Nerava partner API key in `nrv_pk_*` format
- For driver-scope operations (`wallet.*`): a pre-minted driver JWT obtained by your backend

## Installation

Once published, the package is installed as:

```bash
npm install @nerava/sdk
```

During local development inside this monorepo, the SDK is imported directly from its source path.

## Quickstart

```ts
import {
  AuthManager,
  NeravaClient,
  SessionsModule,
  usd,
  latLng,
  NeravaError,
} from "@nerava/sdk";

const auth = new AuthManager({
  apiKey: "nrv_pk_yourPartnerKey1234",
});

const client = new NeravaClient({ auth });
const sessions = new SessionsModule(client);

try {
  const session = await sessions.submit({
    vehicleId: "v_abc",
    chargerId: "c_heights",
    ...latLng(31.0824, -97.6492),
  });
  console.log(`Created session ${session.id} (${session.status})`);
} catch (err) {
  if (err instanceof NeravaError) {
    console.error(`Nerava API error: [${err.code}] ${err.message}`);
  } else {
    throw err;
  }
}
```

## Auth model

The SDK handles two separate auth contexts transparently. You never hand-roll headers.

### Partner operations — `X-Partner-Key`

Used by `sessions.*`, `campaigns.*`, `offers.*`, and `intelligence.*`. Your partner API key is set once at `AuthManager` construction and attached automatically to every partner-scope request.

```ts
const auth = new AuthManager({
  apiKey: "nrv_pk_yourPartnerKey1234",
});
```

### Driver operations — `Authorization: Bearer <jwt>`

Used by `wallet.*`. The SDK does **not** own the driver login flow — your backend mints the driver JWT through your preferred auth path and hands it to the SDK.

There are two ways to provide a driver JWT:

**1. At construction:**

```ts
const auth = new AuthManager({
  apiKey: "nrv_pk_yourPartnerKey1234",
  driverToken: "eyJ.driver.jwt.here",
});
```

**2. After construction (for refresh or per-request updates):**

```ts
await auth.setDriverToken("eyJ.new.jwt.here");
// later:
await auth.clearDriverToken();
```

### Persisting the driver JWT across process restarts

The default `InMemoryTokenStore` holds the driver JWT in a private field for the lifetime of the process. Multi-tenant servers that need to keep driver tokens across restarts should implement their own `TokenStore`:

```ts
import type { TokenStore } from "@nerava/sdk";

class RedisTokenStore implements TokenStore {
  constructor(private readonly redis: RedisClient, private readonly key: string) {}
  async get(): Promise<string | null> {
    return await this.redis.get(this.key);
  }
  async set(token: string | null): Promise<void> {
    if (token === null) {
      await this.redis.del(this.key);
    } else {
      await this.redis.set(this.key, token);
    }
  }
}

const auth = new AuthManager({
  apiKey: "nrv_pk_yourPartnerKey1234",
  tokenStore: new RedisTokenStore(redis, `nerava:driver:${driverId}`),
});
```

The SDK deliberately does not ship a file-based or built-in Redis store — those decisions belong to the partner's infrastructure.

## Modules

### sessions

Partner-scope charging session ingest and retrieval.

```ts
import { SessionsModule, latLng } from "@nerava/sdk";

const sessions = new SessionsModule(client);

// Submit a new session
const session = await sessions.submit({
  vehicleId: "v_abc",
  chargerId: "c_heights",
  ...latLng(31.0824, -97.6492),
  idempotencyKey: "partner-trace-001",
});

// Get by id
const same = await sessions.get(session.id);

// List with filters + pagination
const page = await sessions.list({
  status: "completed",
  vehicleType: "tesla",
  limit: 50,
});
for (const s of page.items) {
  console.log(s.id, s.durationSeconds);
}

// Mark a session completed
const completed = await sessions.complete(session.id);
```

### wallet

Driver-scope wallet operations. Requires a driver JWT.

```ts
import { WalletModule, usd } from "@nerava/sdk";

const wallet = new WalletModule(client);

// Current balance
const balance = await wallet.getBalance("drv_abc");
console.log(`$${balance.balance.amountCents / 100} available`);

// Transactions with pagination
const txns = await wallet.getTransactions("drv_abc", { limit: 25 });

// Credit a driver for a campaign grant
await wallet.credit({
  driverId: "drv_abc",
  amount: usd(500), // $5.00 — always integer cents
  campaignId: "camp_heights_pizza",
  description: "Session bonus",
});

// Debit a driver for a merchant redemption
await wallet.debit({
  driverId: "drv_abc",
  amount: usd(400),
  merchantId: "merch_heights",
});

// Request a payout (amount is server-determined)
const payout = await wallet.requestPayout("drv_abc");
```

### campaigns

Partner-scope campaign discovery.

```ts
import { CampaignsModule } from "@nerava/sdk";

const campaigns = new CampaignsModule(client);

// Discover campaigns by location
const available = await campaigns.getAvailable({
  lat: 31.0824,
  lng: -97.6492,
  vehicleType: "tesla",
});

// Retroactive lookup for a session
const matched = await campaigns.getForSession("sess_abc");
```

### offers

Partner-scope merchant offer activation and completion.

```ts
import { OffersModule } from "@nerava/sdk";

const offers = new OffersModule(client);

// Discover offers for an active session
const available = await offers.getForSession("sess_abc");

// Activate an offer
const activated = await offers.activate({
  sessionId: "sess_abc",
  offerId: available[0].id,
});

// Complete the redemption
const completed = await offers.complete({
  sessionId: "sess_abc",
  offerId: activated.id,
  transactionId: "pos_txn_99", // your POS / order id for reconciliation
});
```

### intelligence

Session intelligence and anti-fraud signals.

> **⚠️ PENDING backend:** The `GET /v1/partners/sessions/{id}/intelligence` endpoint is not yet implemented on the Nerava backend. Calls to `intelligence.getSessionData()` against production will return `NeravaError` with code `NOT_FOUND`. Until the backend ships, point the SDK at the mock server (`baseUrl: 'http://localhost:3001'`) when exercising this module. The type contract is final — no SDK changes will be required when the backend endpoint lands.

```ts
import { IntelligenceModule } from "@nerava/sdk";

const intelligence = new IntelligenceModule(client);
const intel = await intelligence.getSessionData("sess_abc");

console.log(`Quality: ${intel.qualityBucket} (${intel.qualityScore})`);
console.log(`Matched grants: ${intel.matchedGrants.length}`);
for (const grant of intel.matchedGrants) {
  console.log(`  ${grant.campaignName} (priority ${grant.priority})`);
}
```

## Mock server

The SDK ships a zero-dependency Node HTTP mock server for local development. Start it and point the SDK at it:

```bash
npm run mock
# → [nerava/sdk mock] listening on http://localhost:3001
```

```ts
const client = new NeravaClient({
  auth,
  baseUrl: "http://localhost:3001",
});
```

Every public method returns a canned fixture. Auth is validated for presence only — any well-formed key works.

### In-process server for tests

For unit tests that need a running server, import `startMockServer` directly:

```ts
import { startMockServer } from "@nerava/sdk/mock/server";

const { port, stop } = await startMockServer(0); // 0 = OS-assigned
try {
  const client = new NeravaClient({
    auth,
    baseUrl: `http://localhost:${port}`,
  });
  // ...
} finally {
  await stop();
}
```

A complete end-to-end example that spawns the mock server, constructs every module, and exercises every method is in [`examples/basic-integration.ts`](./examples/basic-integration.ts). Run it with:

```bash
npm run example
```

## Error handling

Every SDK method throws `NeravaError` on failure. There are no hierarchical subclasses — consumers discriminate by `code` or `status` in the catch block.

```ts
import { NeravaError } from "@nerava/sdk";

try {
  await wallet.debit({
    driverId: "drv_abc",
    amount: usd(9999),
    merchantId: "merch_heights",
  });
} catch (err) {
  if (err instanceof NeravaError) {
    switch (err.code) {
      case "INSUFFICIENT_BALANCE":
        console.error("Driver balance too low for this redemption");
        break;
      case "UNAUTHORIZED":
        console.error("Driver JWT expired — refresh and retry");
        break;
      case "RATE_LIMITED":
        console.error("Back off and retry");
        break;
      default:
        console.error(`[${err.code}] ${err.message}`);
    }
    // status is `undefined` for network errors, a number for HTTP failures.
    console.error(`status: ${err.status ?? "no-http"}`);
    // requestId helps Nerava support correlate with backend logs.
    if (err.requestId) console.error(`requestId: ${err.requestId}`);
  } else {
    throw err;
  }
}
```

### Error codes

`ErrorCode` is an **open union** — the known codes get autocomplete, but any string is accepted so new backend codes never break SDK compilation. Use the `isKnownErrorCode()` type guard to narrow to the known literals:

```ts
import { isKnownErrorCode } from "@nerava/sdk";

if (err instanceof NeravaError && isKnownErrorCode(err.code)) {
  // `err.code` is now narrowed to the union of known literals
}
```

Known codes include:

- **HTTP**: `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `VALIDATION_ERROR`, `RATE_LIMITED`, `SERVER_ERROR`, `SERVICE_UNAVAILABLE`
- **Business**: `SESSION_NOT_FOUND`, `INSUFFICIENT_BALANCE`, `CAMPAIGN_NOT_FOUND`, `CAMPAIGN_INACTIVE`, `WALLET_NOT_FOUND`, `DUPLICATE_SUBMISSION`, `OFFER_NOT_FOUND`, `OFFER_EXPIRED`, `OFFER_ALREADY_REDEEMED`
- **SDK-originated**: `NO_DRIVER_TOKEN`, `INVALID_CONFIG`, `NETWORK_ERROR`, `INVALID_RESPONSE`

Network failures (DNS, connection refused, TLS) always map to `code: "NETWORK_ERROR"` with `status: undefined`. This unambiguously distinguishes "no HTTP exchange occurred" from any real HTTP status.

### FastAPI validation errors

The SDK's error parser handles all three FastAPI error envelopes:

1. **SDK envelope:** `{ "code": "X", "message": "Y" }`
2. **FastAPI HTTPException:** `{ "detail": "string message" }` — `detail` is mapped to `message`
3. **FastAPI validation:** `{ "detail": [{ "loc": ["body","field"], "msg": "...", "type": "..." }, ...] }` — the array is flattened into a human-readable string like `body.amount_cents: ensure this value is greater than 0; body.driver_id: field required`

You will never see `[object Object]` in a NeravaError message.

## Money handling

The SDK uses integer cents everywhere — never floating-point dollars. The `Money` type is:

```ts
interface Money {
  readonly amountCents: number; // integer — fractional cents are a bug
  readonly currency: string;    // ISO 4217, currently only "USD" is live
}
```

The `usd()` helper constructs USD `Money` values with a runtime guard against the classic dollars-vs-cents bug:

```ts
import { usd } from "@nerava/sdk";

const fivedollars = usd(500);    // ✅ $5.00 — 500 cents
const wrong = usd(5);            // ✅ $0.05 — 5 cents (still valid)
usd(5.00);                       // ❌ throws — floats are a bug
usd(Number.MAX_SAFE_INTEGER + 1); // ❌ throws — unsafe integer
```

If you try to pass a floating-point value to `usd()`, it throws immediately with a `"Did you pass dollars instead of cents?"` hint. This catches the most common fintech-SDK bug at the call site.

## Geographic coordinates

The `latLng()` helper constructs validated `LatLng` values with WGS-84 range checking:

```ts
import { latLng } from "@nerava/sdk";

const here = latLng(31.0824, -97.6492); // ✅
latLng(200, 0);                          // ❌ throws — "Did you swap lat and lng?"
latLng(0, -181);                         // ❌ throws — lng out of range
latLng(Number.NaN, 0);                   // ❌ throws — non-finite
```

The "did you swap lat and lng" hint catches the most common coordinate bug: JavaScript has no way to nominally distinguish latitudes from longitudes, so a swapped-argument mistake would otherwise produce a cryptic backend validation error far from the call site.

## Pagination

List methods return a `PaginatedResponse<T>` with `items` and `nextCursor`. `nextCursor` is `string | null` — loop on `!== null` to walk all pages:

```ts
let cursor: string | undefined;
do {
  const page = await sessions.list({ cursor, limit: 100 });
  for (const s of page.items) handle(s);
  cursor = page.nextCursor ?? undefined;
} while (cursor !== undefined);
```

The `limit` parameter is capped at 200 server-side. The SDK enforces this client-side and throws before the network round-trip if you pass anything larger.

## Type safety notes

The SDK was built with strict TypeScript settings:

- `strict: true`
- `noUncheckedIndexedAccess: true` — indexed accesses return `T | undefined`
- `exactOptionalPropertyTypes: true` — optional fields must be absent, not `undefined`
- `verbatimModuleSyntax: true` — type imports are preserved exactly

All response shapes are camelCase at the public surface even though the backend uses snake_case on the wire. The SDK converts in both directions at the module boundary, so your code only ever sees `session.vehicleId`, never `session.vehicle_id`.

## Package scripts

- `npm run build` — emit typed `dist/` via `tsconfig.build.json`
- `npm run typecheck` — strict typecheck of all source, tests, mock, examples
- `npm run test` — run the vitest suite
- `npm run test:watch` — vitest watch mode
- `npm run mock` — start the mock server on `localhost:3001`
- `npm run example` — run the end-to-end integration example against a spawned mock
- `npm run clean` — remove `dist/`
- `npm run prepublishOnly` — typecheck → clean → build (run before `npm publish`)

## Contributing

The SDK is built incrementally through the `feature/sdk-appstore` branch against a 20-step review plan. Every commit goes through a CodeRabbit review pass and must reach zero findings before the next step lands.

Report issues at <https://github.com/jamesdouglasskirk96/Nerava/issues>.

## License

Proprietary. Contact the Nerava team for partner licensing.
