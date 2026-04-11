// Public surface of @nerava/sdk.
//
// Grows incrementally through the 20-step build plan. Step 11 finalizes
// the surface with the top-level `Nerava` facade class that composes
// every module behind a single `new Nerava({apiKey})` entry point.

// Step 11 — top-level facade (idiomatic entry point)
export { Nerava, type NeravaConfig } from "./nerava.js";

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

// Step 8 — intelligence module (PENDING backend — mock-only)
export {
  IntelligenceModule,
  type AntiFraudSignals,
  type IntelligenceGrant,
  type QualityBucket,
  type SessionIntelligenceResponse,
} from "./modules/intelligence.js";
