// Public surface of @nerava/sdk.
//
// This file grows incrementally as each step adds modules. Step 11 will do a
// final checkpoint pass and verify the surface matches the README. For now
// the re-exports track the build plan step-by-step.

// Step 2 — auth + client
export {
  AuthManager,
  InMemoryTokenStore,
  type AuthManagerConfig,
  type TokenStore,
} from "./auth.js";

export {
  NeravaClient,
  DEFAULT_BASE_URL,
  type AuthContext,
  type HttpMethod,
  type NeravaClientConfig,
  type QueryValue,
  type RequestOptions,
} from "./client.js";

// Step 3 — shared primitive types
export {
  latLng,
  usd,
  type JsonArray,
  type JsonObject,
  type JsonPrimitive,
  type JsonValue,
  type LatLng,
  type Money,
  type PaginatedResponse,
  type PaginationParams,
  type VehicleType,
} from "./types.js";

// Step 4 — errors
export {
  NeravaError,
  isKnownErrorCode,
  KNOWN_ERROR_CODES,
  type ErrorCode,
  type KnownErrorCode,
  type NeravaErrorInit,
} from "./errors.js";

// Step 5 — sessions module
export {
  SessionsModule,
  type SessionListFilters,
  type SessionResponse,
  type SessionStatus,
  type SubmitSessionRequest,
} from "./modules/sessions.js";

// Step 6 — wallet module
export {
  WalletModule,
  type CreditWalletRequest,
  type DebitWalletRequest,
  type PayoutResponse,
  type PayoutStatus,
  type WalletBalance,
  type WalletTransaction,
  type WalletTransactionFilters,
  type WalletTransactionType,
} from "./modules/wallet.js";

// Step 7 — campaigns + offers modules
export {
  CampaignsModule,
  type CampaignStatus,
  type CampaignSummary,
  type GetAvailableCampaignsRequest,
} from "./modules/campaigns.js";

export {
  OffersModule,
  type ActivateOfferRequest,
  type CompleteOfferRequest,
  type OfferResponse,
  type OfferStatus,
  type OfferSummary,
} from "./modules/offers.js";

// TODO(step-8): re-export intelligence module.
// TODO(step-11): add the top-level `Nerava` facade class that composes
//   AuthManager + NeravaClient + all modules behind a single `new Nerava({...})`
//   entry point matching the README quickstart.
