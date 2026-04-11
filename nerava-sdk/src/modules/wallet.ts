/**
 * Wallet module — driver-scope operations on a specific driver's wallet.
 *
 * Auth context: `driver` (Authorization: Bearer <jwt>). Every method
 * here identifies a specific driver via the pre-minted JWT held by
 * `AuthManager`. The SDK does not own driver login — the partner backend
 * is responsible for obtaining the JWT (see the Step 2 Option D decision
 * captured in auth.ts).
 *
 * Money handling: every amount crosses the network boundary as `Money`
 * (amountCents + currency), not a raw number. This is non-negotiable per
 * the Step 3 design — floating-point dollars are a source of ledger bugs.
 *
 * Backend reference: `backend/app/routers/driver_wallet.py` and
 * `backend/app/services/payout_service.py`. The SDK's types mirror the
 * public API surface, not the Pydantic schemas directly.
 */

import type { NeravaClient } from "../client.js";
import { camelCaseKeys } from "../internal/case.js";
import type { Money, PaginatedResponse, PaginationParams } from "../types.js";

// ---------------------------------------------------------------------------
// Module-specific types (inline per the hybrid scope decision)
// ---------------------------------------------------------------------------

/**
 * Driver wallet snapshot. Includes both the cash balance in cents and
 * optional Nova-points balance for drivers with the loyalty program
 * enabled.
 */
export interface WalletBalance {
  readonly driverId: string;
  readonly balance: Money;
  readonly pendingBalance: Money;
  readonly lifetimeEarned: Money;
  readonly novaBalance: number;
}

/**
 * A single wallet transaction — credit or debit.
 */
export type WalletTransactionType = "credit" | "debit";

export interface WalletTransaction {
  readonly id: string;
  readonly driverId: string;
  readonly type: WalletTransactionType;
  readonly amount: Money;
  readonly balanceAfter: Money;
  readonly referenceType: string;
  readonly referenceId: string | null;
  readonly description: string | null;
  readonly createdAt: string;
}

/**
 * Request for `wallet.credit()`. Requires a campaign reference for
 * the grant to route through the correct budget.
 */
export interface CreditWalletRequest {
  readonly driverId: string;
  readonly amount: Money;
  readonly campaignId: string;
  readonly referenceId?: string;
  readonly description?: string;
}

/**
 * Request for `wallet.debit()`. Requires a merchant reference for
 * the debit to be attributed to a specific redemption.
 */
export interface DebitWalletRequest {
  readonly driverId: string;
  readonly amount: Money;
  readonly merchantId: string;
  readonly referenceId?: string;
  readonly description?: string;
}

/**
 * Status lifecycle of a payout request.
 */
export type PayoutStatus =
  | "pending"
  | "processing"
  | "paid"
  | "failed"
  | "cancelled";

/**
 * Response from `wallet.requestPayout()`. Includes the payout id
 * so partners can reconcile against their own systems.
 */
export interface PayoutResponse {
  readonly id: string;
  readonly driverId: string;
  readonly amount: Money;
  readonly fee: Money;
  readonly status: PayoutStatus;
  readonly providerReference: string | null;
  readonly createdAt: string;
}

/**
 * Filters for `wallet.getTransactions()`. Pagination + optional type.
 */
export interface WalletTransactionFilters extends PaginationParams {
  readonly type?: WalletTransactionType;
  readonly since?: string;
  readonly until?: string;
}

// ---------------------------------------------------------------------------
// Module class
// ---------------------------------------------------------------------------

/**
 * Wallet module. Every method uses driver-scope auth — the partner
 * backend must have called `auth.setDriverToken()` with a valid JWT
 * for the target driver before any of these methods will succeed.
 *
 * If the JWT is missing or expired, methods throw `NeravaError` with
 * code `NO_DRIVER_TOKEN` or `UNAUTHORIZED`.
 */
export class WalletModule {
  readonly #client: NeravaClient;

  constructor(client: NeravaClient) {
    this.#client = client;
  }

  /**
   * Fetch the current wallet balance for a driver.
   */
  async getBalance(driverId: string): Promise<WalletBalance> {
    if (!driverId) {
      throw new Error("wallet.getBalance(): driverId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "driver",
      path: "/v1/wallet/balance",
      query: { driver_id: driverId },
    });
    return camelCaseKeys(raw) as WalletBalance;
  }

  /**
   * Paginated list of wallet transactions. `limit` is capped at 200
   * server-side; the SDK guards against larger values client-side to
   * fail fast.
   */
  async getTransactions(
    driverId: string,
    filters: WalletTransactionFilters = {},
  ): Promise<PaginatedResponse<WalletTransaction>> {
    if (!driverId) {
      throw new Error("wallet.getTransactions(): driverId is required");
    }
    if (filters.limit !== undefined && filters.limit > 200) {
      throw new Error(
        `wallet.getTransactions(): limit must be ≤ 200 (got ${filters.limit}). Backend will reject anything larger.`,
      );
    }
    const query: Record<string, string | number> = { driver_id: driverId };
    if (filters.cursor !== undefined) query["cursor"] = filters.cursor;
    if (filters.limit !== undefined) query["limit"] = filters.limit;
    if (filters.type !== undefined) query["type"] = filters.type;
    if (filters.since !== undefined) query["since"] = filters.since;
    if (filters.until !== undefined) query["until"] = filters.until;

    const raw = await this.#client.request<unknown>({
      auth: "driver",
      path: "/v1/wallet/transactions",
      query,
    });
    return camelCaseKeys(raw) as PaginatedResponse<WalletTransaction>;
  }

  /**
   * Credit a driver's wallet. Goes through the driver-scope JWT even
   * though conceptually it's the platform giving the driver money —
   * this matches the backend route and keeps the SDK's auth contract
   * consistent.
   *
   * The amount is sent as two discrete fields (`amount_cents` +
   * `currency`) to match the backend's schema, not as a nested `Money`
   * object. The SDK does this flattening at the request boundary.
   */
  async credit(request: CreditWalletRequest): Promise<WalletTransaction> {
    if (!request.driverId) {
      throw new Error("wallet.credit(): driverId is required");
    }
    if (!request.campaignId) {
      throw new Error("wallet.credit(): campaignId is required");
    }
    const body: Record<string, string | number> = {
      driver_id: request.driverId,
      amount_cents: request.amount.amountCents,
      currency: request.amount.currency,
      campaign_id: request.campaignId,
    };
    if (request.referenceId !== undefined) body["reference_id"] = request.referenceId;
    if (request.description !== undefined) body["description"] = request.description;

    const raw = await this.#client.request<unknown>({
      auth: "driver",
      method: "POST",
      path: "/v1/wallet/credit",
      body,
    });
    return camelCaseKeys(raw) as WalletTransaction;
  }

  /**
   * Debit a driver's wallet for a merchant redemption. Throws
   * `NeravaError` with code `INSUFFICIENT_BALANCE` if the driver's
   * balance is below the debit amount.
   */
  async debit(request: DebitWalletRequest): Promise<WalletTransaction> {
    if (!request.driverId) {
      throw new Error("wallet.debit(): driverId is required");
    }
    if (!request.merchantId) {
      throw new Error("wallet.debit(): merchantId is required");
    }
    const body: Record<string, string | number> = {
      driver_id: request.driverId,
      amount_cents: request.amount.amountCents,
      currency: request.amount.currency,
      merchant_id: request.merchantId,
    };
    if (request.referenceId !== undefined) body["reference_id"] = request.referenceId;
    if (request.description !== undefined) body["description"] = request.description;

    const raw = await this.#client.request<unknown>({
      auth: "driver",
      method: "POST",
      path: "/v1/wallet/debit",
      body,
    });
    return camelCaseKeys(raw) as WalletTransaction;
  }

  /**
   * Request a payout of the driver's available balance to their
   * connected payout account. Returns a `PayoutResponse` with the
   * payout id and initial status (`pending`).
   *
   * The payout amount is determined server-side from the driver's
   * current balance minus the provider fee — the SDK does not accept
   * a client-side amount to avoid race conditions between the balance
   * check and the payout.
   */
  async requestPayout(driverId: string): Promise<PayoutResponse> {
    if (!driverId) {
      throw new Error("wallet.requestPayout(): driverId is required");
    }
    const raw = await this.#client.request<unknown>({
      auth: "driver",
      method: "POST",
      path: "/v1/wallet/payout",
      body: { driver_id: driverId },
    });
    return camelCaseKeys(raw) as PayoutResponse;
  }
}
