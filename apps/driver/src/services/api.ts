import { useQuery, useMutation } from '@tanstack/react-query'
import type {
  CaptureIntentRequest,
  CaptureIntentResponse,
  MerchantDetailsResponse,
  WalletActivateRequest,
  WalletActivateResponse,
  RequestToJoinResponse,
  ClaimRewardResponse,
  ClaimDetailResponse,
  ReceiptUploadResponse,
} from '../types'
import {
  captureIntentMock,
  getMerchantDetailsMock,
  activateExclusiveMock,
} from '../mock/mockApi'
import type { MockCaptureIntentRequest } from '../mock/types'
import {
  validateResponse,
  CaptureIntentResponseSchema,
  ActivateExclusiveResponseSchema,
  ActiveExclusiveResponseSchema,
  LocationCheckResponseSchema,
  MerchantDetailsResponseSchema,
} from './schemas'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

// Proactive token refresh — refresh before expiry when app resumes from background
const TOKEN_REFRESH_THRESHOLD_MS = 10 * 60 * 1000 // Refresh if token is within 10 min of expiry

function getTokenExpiry(): number | null {
  const token = localStorage.getItem('access_token')
  if (!token) return null
  try {
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.exp ? payload.exp * 1000 : null // Convert to ms
  } catch {
    return null
  }
}

async function proactiveTokenRefresh(): Promise<void> {
  const expiry = getTokenExpiry()
  if (!expiry) return

  const timeLeft = expiry - Date.now()
  if (timeLeft > TOKEN_REFRESH_THRESHOLD_MS) return // Token still fresh

  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return

  try {
    const response = await fetch(`${API_BASE_URL}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (response.ok) {
      const data = await response.json()
      localStorage.setItem('access_token', data.access_token)
      if (data.refresh_token) {
        localStorage.setItem('refresh_token', data.refresh_token)
      }
      try { window.neravaNative?.setAuthToken(data.access_token) } catch {}
      console.log('[API] Proactive token refresh succeeded')
    }
  } catch {
    // Silent fail — reactive refresh on 401 will handle it
  }
}

// Refresh token when app becomes visible (user returns from background)
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      proactiveTokenRefresh()
    }
  })
  // Also refresh on app focus (covers WKWebView resume)
  window.addEventListener('focus', () => {
    proactiveTokenRefresh()
  })
}

// Check if mock mode is enabled - default to backend mode unless explicitly set
export function isMockMode(): boolean {
  return import.meta.env.VITE_MOCK_MODE === 'true'
}

// Check if demo mode is enabled - allows mock data fallback when API fails
export function isDemoMode(): boolean {
  return import.meta.env.VITE_DEMO_MODE === 'true'
}

export class ApiError extends Error {
  status: number
  code?: string

  constructor(
    status: number,
    code?: string,
    message?: string
  ) {
    super(message || `API error: ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function fetchAPI<T>(endpoint: string, options?: RequestInit, retryOn401 = true): Promise<T> {
  const url = `${API_BASE_URL}${endpoint}`
  const token = localStorage.getItem('access_token')

  const headers = new Headers(options?.headers)
  headers.set('Content-Type', 'application/json')

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  console.log('[API] Fetching:', url, options)

  try {
    const response = await fetch(url, {
      ...options,
      headers,
    })

    console.log('[API] Response status:', response.status, response.statusText)

    // Handle 401 Unauthorized - try token refresh
    if (response.status === 401 && retryOn401) {
      const refreshToken = localStorage.getItem('refresh_token')
      if (refreshToken) {
        try {
          console.log('[API] Attempting token refresh...')
          const refreshResponse = await fetch(`${API_BASE_URL}/v1/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
          })

          if (refreshResponse.ok) {
            const refreshData = await refreshResponse.json()
            localStorage.setItem('access_token', refreshData.access_token)
            if (refreshData.refresh_token) {
              localStorage.setItem('refresh_token', refreshData.refresh_token)
            }
            try { window.neravaNative?.setAuthToken(refreshData.access_token) } catch {}
            console.log('[API] Token refreshed, retrying original request')

            // Retry original request with new token
            const newHeaders = new Headers(options?.headers)
            newHeaders.set('Content-Type', 'application/json')
            newHeaders.set('Authorization', `Bearer ${refreshData.access_token}`)
            const retryResponse = await fetch(url, {
              ...options,
              headers: newHeaders,
            })

            if (!retryResponse.ok) {
              // If retry still fails, clear tokens
              localStorage.removeItem('access_token')
              localStorage.removeItem('refresh_token')
              throw new ApiError(retryResponse.status, undefined, 'Authentication failed after token refresh')
            }

            const retryData = await retryResponse.json()
            console.log('[API] Retry response data:', retryData)
            return retryData
          } else {
            // Refresh failed, clear tokens
            localStorage.removeItem('access_token')
            localStorage.removeItem('refresh_token')
            throw new ApiError(401, 'refresh_failed', 'Token refresh failed')
          }
        } catch (refreshError) {
          // Refresh failed, clear tokens
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          if (refreshError instanceof ApiError) {
            window.dispatchEvent(new CustomEvent('nerava:session-expired'))
            throw refreshError
          }
          window.dispatchEvent(new CustomEvent('nerava:session-expired'))
          throw new ApiError(401, 'refresh_error', 'Failed to refresh token')
        }
      } else {
        // No refresh token, clear access token and throw
        localStorage.removeItem('access_token')
        // Don't log 401 errors when retryOn401 is false (expected for anonymous users)
        if (retryOn401) {
          console.error('[API] No refresh token available for 401 retry')
        }
        throw new ApiError(401, 'no_refresh_token', 'No refresh token available')
      }
    }

    if (!response.ok) {
      let errorData: { error?: string; message?: string; detail?: string } = {}
      try {
        errorData = await response.json()
      } catch {
        // Not JSON, try to get text
        try {
          const errorText = await response.text()
          errorData = { message: errorText || response.statusText }
        } catch {
          errorData = { message: response.statusText }
        }
      }
      console.error('[API] Error response:', errorData)
      // Handle FastAPI's standard error format: {detail: "..."} or {error: "...", message: "..."}
      const errorMessage = errorData.message || errorData.detail || response.statusText
      const errorCode = errorData.error || (response.status >= 500 ? 'server_error' : undefined)
      throw new ApiError(
        response.status,
        errorCode,
        errorMessage
      )
    }

    const data = await response.json()
    console.log('[API] Response data:', data)
    return data
  } catch (error) {
    // Don't log 401 errors when retryOn401 is false (expected for anonymous users)
    if (!(error instanceof ApiError && error.status === 401 && !retryOn401)) {
      console.error('[API] Fetch error:', error)
    }
    // Re-throw ApiError as-is, wrap other errors
    if (error instanceof ApiError) {
      // Only log non-401 errors or 401s where retry was attempted
      if (!(error.status === 401 && !retryOn401)) {
        console.error('[API] Throwing ApiError:', error.status, error.code, error.message)
      }
      throw error
    }
    // Network errors or other fetch failures
    const apiError = new ApiError(0, 'network_error', error instanceof Error ? error.message : 'Network error')
    console.error('[API] Throwing wrapped ApiError:', apiError.status, apiError.code, apiError.message)
    throw apiError
  }
}

// Intent Capture - with module-level cache and pending request deduplication
// This provides multiple layers of protection against infinite fetches
interface IntentCache {
  key: string
  data: CaptureIntentResponse
  timestamp: number
}
let intentCache: IntentCache | null = null
let pendingIntentRequest: Promise<CaptureIntentResponse> | null = null
let pendingIntentKey: string | null = null
const INTENT_CACHE_TTL_MS = 60000 // 60 seconds cache TTL

// Rate limiting to prevent rapid re-fetches (but allow location-based re-fetches)
let lastSuccessfulCacheKey: string | null = null
let lastFetchTimestamp = 0
const MIN_FETCH_INTERVAL_MS = 5000 // Minimum 5 seconds between fetches

export function useIntentCapture(request: CaptureIntentRequest | null) {
  // Use stable queryKey with ROUNDED coordinates to prevent refetch on GPS fluctuation
  // GPS watchPosition returns slightly different values each time - round to 4 decimal places (~11m precision)
  const roundedLat = request ? Math.round(request.lat * 10000) / 10000 : null
  const roundedLng = request ? Math.round(request.lng * 10000) / 10000 : null
  const cacheKey = request ? `${roundedLat},${roundedLng}` : ''
  const queryKey = request
    ? ['intent-capture', roundedLat, roundedLng]
    : ['intent-capture', null]

  return useQuery({
    queryKey,
    queryFn: async () => {
      const now = Date.now()

      // Rate limit - prevent fetches within 5 seconds ONLY for the same location
      if (lastSuccessfulCacheKey === cacheKey && (now - lastFetchTimestamp) < MIN_FETCH_INTERVAL_MS) {
        console.log('[API] Intent capture rate limited (same location, too soon)')
        if (intentCache && intentCache.key === cacheKey) {
          return intentCache.data
        }
      }

      // Check module-level cache first - prevents unnecessary API calls
      if (intentCache && intentCache.key === cacheKey && (now - intentCache.timestamp) < INTENT_CACHE_TTL_MS) {
        console.log('[API] Intent capture using cached response (cache hit)')
        return intentCache.data
      }

      // Check if there's already a pending request for the same key - deduplicate
      if (pendingIntentRequest && pendingIntentKey === cacheKey) {
        console.log('[API] Intent capture reusing pending request (deduplication)')
        return pendingIntentRequest
      }

      // Create the actual fetch function
      const doFetch = async (): Promise<CaptureIntentResponse> => {
        if (isMockMode() && request) {
          // Use mock API in mock mode
          return await captureIntentMock(request as MockCaptureIntentRequest)
        }
        // Use real API - disable token refresh for anonymous requests
        // This endpoint supports optional authentication
        const hasToken = !!localStorage.getItem('access_token')
        const data = await fetchAPI<unknown>('/v1/intent/capture', {
          method: 'POST',
          body: JSON.stringify(request),
        }, hasToken) // Only retry token refresh if user has a token

        // Debug: Log raw API response before validation
        console.log('[API] Raw intent capture response:', {
          merchants_count: Array.isArray((data as any)?.merchants) ? (data as any).merchants.length : 'not array',
          merchants: (data as any)?.merchants,
          charger_summary: (data as any)?.charger_summary,
          confidence_tier: (data as any)?.confidence_tier,
        })

        // Validate response schema
        const validated = validateResponse(CaptureIntentResponseSchema, data, '/v1/intent/capture') as unknown as CaptureIntentResponse

        // Debug: Log validated response
        console.log('[API] Validated intent capture response:', {
          merchants_count: validated.merchants?.length || 0,
          merchants: validated.merchants,
          charger_summary: validated.charger_summary,
          confidence_tier: validated.confidence_tier,
        })

        return validated
      }

      // Set pending request state and execute
      pendingIntentKey = cacheKey
      lastFetchTimestamp = now // Track fetch time for rate limiting
      pendingIntentRequest = doFetch().then(validated => {
        // Store in module-level cache on success
        intentCache = { key: cacheKey, data: validated, timestamp: now }
        lastSuccessfulCacheKey = cacheKey // Track which location was fetched
        lastFetchTimestamp = Date.now() // Update to completion time
        return validated
      }).finally(() => {
        // Clear pending state when done
        if (pendingIntentKey === cacheKey) {
          pendingIntentRequest = null
          pendingIntentKey = null
        }
      })

      return pendingIntentRequest
    },
    enabled: request !== null,
    staleTime: 60000, // Data is fresh for 1 minute - prevents refetching
    gcTime: 300000, // Keep in cache for 5 minutes
    retry: false, // Don't retry on error
    refetchOnMount: false, // Don't refetch on mount - CRITICAL for preventing loops
    refetchOnReconnect: false, // Don't refetch on reconnect
    refetchOnWindowFocus: false, // Don't refetch on window focus
    refetchInterval: false, // No automatic refetching
  })
}

// Merchant Details
export function useMerchantDetails(
  merchantId: string | null,
  sessionId?: string
) {
  return useQuery({
    queryKey: ['merchant-details', merchantId, sessionId],
    queryFn: async () => {
      if (isMockMode() && merchantId) {
        // Use mock API in mock mode
        return await getMerchantDetailsMock(merchantId, sessionId)
      }
      // Use real API
      const params = sessionId ? `?session_id=${sessionId}` : ''
      const data = await fetchAPI<unknown>(`/v1/merchants/${merchantId}${params}`)
      return validateResponse(MerchantDetailsResponseSchema, data, `/v1/merchants/${merchantId}`) as unknown as MerchantDetailsResponse
    },
    enabled: merchantId !== null,
    staleTime: 120_000, // 2 min — merchant details rarely change
  })
}

// Wallet Activate (legacy - use exclusive endpoints instead)
export function useWalletActivate() {
  return useMutation({
    mutationFn: async (request: WalletActivateRequest) => {
      if (isMockMode()) {
        // Use mock API in mock mode
        return await activateExclusiveMock(request.merchant_id, request.session_id)
      }
      // Use real API
      return fetchAPI<WalletActivateResponse>('/v1/wallet/pass/activate', {
        method: 'POST',
        body: JSON.stringify(request),
      })
    },
  })
}

// Exclusive Session Types
export interface ActivateExclusiveRequest {
  merchant_id?: string
  merchant_place_id?: string | null
  charger_id: string
  charger_place_id?: string
  intent_session_id?: string
  lat: number | null  // V3: null allowed when location unavailable
  lng: number | null  // V3: null allowed when location unavailable
  accuracy_m?: number
  // NEW: Intent capture fields (V3)
  intent?: 'eat' | 'work' | 'quick-stop'
  party_size?: number
  needs_power_outlet?: boolean
  is_to_go?: boolean
}

export interface ExclusiveSessionResponse {
  id: string
  merchant_id?: string
  charger_id?: string
  expires_at: string
  activated_at: string
  remaining_seconds: number
  // Enriched fields for claim card / claim details
  merchant_name?: string | null
  merchant_place_id?: string | null
  exclusive_title?: string | null
  merchant_lat?: number | null
  merchant_lng?: number | null
  merchant_distance_m?: number | null
  merchant_walk_time_min?: number | null
  merchant_category?: string | null
  merchant_photo_url?: string | null
  charger_name?: string | null
  verification_code?: string | null
  charging_active?: boolean | null
  charging_session_ended_at?: string | null
}

export interface ActivateExclusiveResponse {
  status: string
  exclusive_session: ExclusiveSessionResponse
}

export interface CompleteExclusiveRequest {
  exclusive_session_id: string
  feedback?: {
    thumbs_up?: boolean
    tags?: string[]
  }
}

export interface CompleteExclusiveResponse {
  status: string
}

export interface ActiveExclusiveResponse {
  exclusive_session: ExclusiveSessionResponse | null
}

// Exclusive Session API Functions
export async function activateExclusive(request: ActivateExclusiveRequest): Promise<ActivateExclusiveResponse> {
  const data = await fetchAPI<unknown>('/v1/exclusive/activate', {
    method: 'POST',
    body: JSON.stringify(request),
  })
  return validateResponse(ActivateExclusiveResponseSchema, data, '/v1/exclusive/activate') as unknown as ActivateExclusiveResponse
}

export async function completeExclusive(request: CompleteExclusiveRequest): Promise<CompleteExclusiveResponse> {
  // Complete response is simple, no schema needed for MVP
  return fetchAPI<CompleteExclusiveResponse>('/v1/exclusive/complete', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function getActiveExclusive(): Promise<ActiveExclusiveResponse | null> {
  // Check if user is authenticated before making request
  const hasToken = !!localStorage.getItem('access_token')
  if (!hasToken) {
    // Return null for anonymous users (no active exclusive)
    return { exclusive_session: null }
  }

  try {
    // Disable token refresh retry - if auth fails, user is not authenticated
    const data = await fetchAPI<unknown>('/v1/exclusive/active', undefined, false)
    return validateResponse(ActiveExclusiveResponseSchema, data, '/v1/exclusive/active') as unknown as ActiveExclusiveResponse
  } catch (error) {
    // Handle 401 gracefully - user is not authenticated, so no active exclusive
    // This is expected for anonymous users, so don't log as error
    if (error instanceof ApiError && error.status === 401) {
      // Silently return null (no active exclusive for unauthenticated users)
      return { exclusive_session: null }
    }
    // Log and re-throw other errors
    console.error('[API] Error fetching active exclusive:', error)
    throw error
  }
}

// Location Check
export interface LocationCheckResponse {
  in_charger_radius: boolean
  nearest_charger_id?: string
  distance_m?: number
}

export async function checkLocation(lat: number, lng: number): Promise<LocationCheckResponse> {
  const data = await fetchAPI<unknown>(`/v1/drivers/location/check?lat=${lat}&lng=${lng}`)
  return validateResponse(LocationCheckResponseSchema, data, '/v1/drivers/location/check') as unknown as LocationCheckResponse
}

// Ad impression tracking
export async function trackAdImpressions(
  impressions: Array<{ merchant_id: string; impression_type: string }>
): Promise<{ recorded: number }> {
  if (!impressions.length) return { recorded: 0 }
  return fetchAPI<{ recorded: number }>('/v1/ads/impressions', {
    method: 'POST',
    body: JSON.stringify({ impressions }),
  })
}

// React Query Hooks for Exclusive Sessions
export function useActivateExclusive() {
  return useMutation({
    mutationFn: activateExclusive,
  })
}

export function useCompleteExclusive() {
  return useMutation({
    mutationFn: completeExclusive,
  })
}

export function useActiveExclusive() {
  // Check authentication state - handle 401 gracefully in getActiveExclusive
  // Query will run but return null for anonymous users (no error thrown)
  return useQuery<ActiveExclusiveResponse | null>({
    queryKey: ['active-exclusive'],
    queryFn: getActiveExclusive,
    staleTime: 15_000, // 15 sec — active sessions need reasonably fresh data
    retry: false, // Don't retry on error (401 is expected for anonymous users)
    refetchOnWindowFocus: true, // Refetch when app returns from background
    refetchInterval: () => {
      // Only poll if we have a token AND page is visible
      const hasToken = !!localStorage.getItem('access_token')
      const isVisible = !document.hidden
      return hasToken && isVisible ? 30000 : false
    },
    // Note: onError was removed in React Query v5, errors are handled via the error state
  })
}

export function useLocationCheck(lat: number | null, lng: number | null) {
  return useQuery({
    queryKey: ['location-check', lat, lng],
    queryFn: () => lat !== null && lng !== null ? checkLocation(lat, lng) : null,
    enabled: lat !== null && lng !== null,
    staleTime: 5_000, // 5 sec — location check needs near-realtime data
    refetchOnWindowFocus: true, // Refetch when app returns from background
    refetchInterval: () => {
      // Only poll when page is visible
      return document.hidden ? false : 10000
    },
  })
}

// Merchants for Charger
export interface MerchantForCharger {
  id: string
  merchant_id: string
  place_id?: string  // Frontend expects place_id for MerchantSummary compatibility
  name: string
  lat: number
  lng: number
  address?: string
  phone?: string
  logo_url?: string
  photo_url?: string  // Also support photo_url
  photo_urls?: string[]
  category?: string
  types?: string[]  // Frontend expects types array
  is_primary?: boolean
  exclusive_title?: string
  exclusive_description?: string
  open_now?: boolean
  open_until?: string
  rating?: number
  user_rating_count?: number
  walk_time_s?: number
  walk_time_seconds?: number
  distance_m?: number
}

export async function apiGetMerchantsForCharger(
  chargerId: string,
  options?: { state?: 'pre-charge' | 'charging', open_only?: boolean }
): Promise<MerchantForCharger[]> {
  const params = new URLSearchParams({
    charger_id: chargerId,
    state: options?.state || 'charging',
  })
  if (options?.open_only) {
    params.append('open_only', 'true')
  }

  const data = await fetchAPI<unknown>(`/v1/drivers/merchants/open?${params.toString()}`)
  return data as MerchantForCharger[]
}

export function useMerchantsForCharger(
  chargerId: string | null,
  options?: { state?: 'pre-charge' | 'charging', open_only?: boolean }
) {
  return useQuery({
    queryKey: ['merchants-for-charger', chargerId, options?.state, options?.open_only],
    queryFn: () => chargerId ? apiGetMerchantsForCharger(chargerId, options) : [],
    enabled: chargerId !== null,
    staleTime: 60_000, // 1 min — merchant list for a charger changes infrequently
  })
}

// Verify Visit - generates incremental verification code for merchant
export interface VerifyVisitRequest {
  exclusive_session_id: string
  lat?: number
  lng?: number
}

export interface VerifyVisitResponse {
  status: string // "VERIFIED" or "ALREADY_VERIFIED"
  verification_code: string // e.g., "ATX-ASADAS-023"
  visit_number: number
  merchant_name: string
  verified_at: string
}

export async function verifyVisit(request: VerifyVisitRequest): Promise<VerifyVisitResponse> {
  return fetchAPI<VerifyVisitResponse>('/v1/exclusive/verify', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export function useVerifyVisit() {
  return useMutation({
    mutationFn: verifyVisit,
  })
}

// Amenity Votes API
export interface AmenityVoteRequest {
  vote_type: 'up' | 'down'
}

export interface AmenityVoteResponse {
  ok: boolean
  upvotes: number
  downvotes: number
}

export async function voteAmenity(
  merchantId: string,
  amenity: 'bathroom' | 'wifi',
  voteType: 'up' | 'down'
): Promise<AmenityVoteResponse> {
  return fetchAPI<AmenityVoteResponse>(
    `/v1/merchants/${merchantId}/amenities/${amenity}/vote`,
    {
      method: 'POST',
      body: JSON.stringify({ vote_type: voteType }),
    }
  )
}

export function useVoteAmenity() {
  return useMutation({
    mutationFn: ({ merchantId, amenity, voteType }: { merchantId: string; amenity: 'bathroom' | 'wifi'; voteType: 'up' | 'down' }) =>
      voteAmenity(merchantId, amenity, voteType),
  })
}

// ===================== Charger Detail =====================

export interface ChargerDetailNearbyMerchant {
  place_id: string
  name: string
  photo_url: string
  distance_m: number
  walk_time_min: number
  has_exclusive: boolean
  phone?: string | null
  website?: string | null
  category?: string | null
  lat?: number | null
  lng?: number | null
  exclusive_title?: string | null
  is_nerava_merchant?: boolean
  join_request_count?: number
}

export interface ChargerDetail {
  id: string
  name: string
  address: string | null
  city: string | null
  state: string | null
  lat: number
  lng: number
  network_name: string | null
  connector_types: string[]
  power_kw: number | null
  num_evse: number | null
  status: string
  distance_m: number
  drive_time_min: number
  total_sessions_30d: number
  unique_drivers_30d: number
  avg_duration_min: number
  active_reward_cents: number | null
  nearby_merchants: ChargerDetailNearbyMerchant[]
  pricing_per_kwh: number | null
  pricing_source: string | null
  nerava_score: number | null
  drivers_charging_now: number
}

export async function fetchChargerDetail(chargerId: string, lat?: number, lng?: number): Promise<ChargerDetail> {
  const params = new URLSearchParams()
  if (lat !== undefined) params.append('lat', String(lat))
  if (lng !== undefined) params.append('lng', String(lng))
  const qs = params.toString()
  return fetchAPI<ChargerDetail>(`/v1/chargers/${chargerId}/detail${qs ? `?${qs}` : ''}`, undefined, false)
}

export function useChargerDetail(chargerId: string | null, lat?: number, lng?: number) {
  return useQuery({
    queryKey: ['charger-detail', chargerId, lat, lng],
    queryFn: () => chargerId ? fetchChargerDetail(chargerId, lat, lng) : null,
    enabled: chargerId !== null,
    staleTime: 60_000,
  })
}

// ===================== Charging Sessions =====================

export interface ChargingSessionIncentive {
  grant_id: string
  campaign_id: string
  amount_cents: number
  status: string
  granted_at: string | null
}

export interface ChargingSession {
  id: string
  session_start: string | null
  session_end: string | null
  duration_minutes: number | null
  charger_id: string | null
  charger_network: string | null
  connector_type: string | null
  power_kw: number | null
  kwh_delivered: number | null
  verified: boolean
  lat: number | null
  lng: number | null
  battery_start_pct: number | null
  battery_end_pct: number | null
  quality_score: number | null
  ended_reason: string | null
  incentive: ChargingSessionIncentive | null
  location_trail: { lat: number; lng: number; ts: string }[] | null
}

export interface ChargingSessionsResponse {
  sessions: ChargingSession[]
  count: number
}

export interface ActiveSessionResponse {
  session: ChargingSession | null
  active: boolean
  last_ended_session?: ChargingSession | null
}

export interface PollSessionResponse {
  session_active: boolean
  session_id?: string
  duration_minutes?: number
  kwh_delivered?: number | null
  cached?: boolean
  session_ended?: boolean
  incentive_granted?: boolean
  incentive_amount_cents?: number
  telemetry_mode?: boolean
  error?: string
  vehicle_asleep?: boolean
  recommended_interval_s?: number
  minutes_to_full?: number | null
  battery_level?: number | null
  charger_power_kw?: number | null
}

export interface TeslaConnectionStatus {
  connected: boolean
  vehicle_name?: string | null
  vehicle_model?: string | null
  vehicle_year?: number | null
  exterior_color?: string | null
  vin?: string | null
  battery_level?: number | null
}

export async function fetchChargingSessions(limit = 50, offset = 0): Promise<ChargingSessionsResponse> {
  return fetchAPI<ChargingSessionsResponse>(`/v1/charging-sessions/?limit=${limit}&offset=${offset}`)
}

export async function fetchActiveSession(): Promise<ActiveSessionResponse> {
  return fetchAPI<ActiveSessionResponse>('/v1/charging-sessions/active')
}

export async function endChargingSession(sessionId: string): Promise<{ session: ChargingSession; ended: boolean }> {
  return fetchAPI(`/v1/charging-sessions/${sessionId}/end`, { method: 'POST' })
}

export async function pollChargingSession(deviceLat?: number, deviceLng?: number): Promise<PollSessionResponse> {
  const body = deviceLat != null && deviceLng != null
    ? JSON.stringify({ lat: deviceLat, lng: deviceLng })
    : undefined
  return fetchAPI<PollSessionResponse>('/v1/charging-sessions/poll', {
    method: 'POST',
    body,
  })
}

export async function fetchTeslaStatus(): Promise<TeslaConnectionStatus> {
  return fetchAPI<TeslaConnectionStatus>('/v1/auth/tesla/status')
}

export function useChargingSessions(limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['charging-sessions', limit, offset],
    queryFn: () => fetchChargingSessions(limit, offset),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
    refetchOnWindowFocus: true,
  })
}

export function useActiveChargingSession() {
  return useQuery({
    queryKey: ['charging-sessions', 'active'],
    queryFn: fetchActiveSession,
    enabled: !!localStorage.getItem('access_token'),
    refetchInterval: 60000,
    refetchOnWindowFocus: true,
  })
}

export function useTeslaStatus() {
  return useQuery({
    queryKey: ['tesla-status'],
    queryFn: fetchTeslaStatus,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 300000, // 5 min
    retry: false,
  })
}

// ===================== EV Codes (Tesla verify-charging) =====================

export interface EVCode {
  code: string
  merchant_name: string | null
  expires_at: string
  status: string
}

export async function fetchActiveEVCodes(): Promise<EVCode[]> {
  return fetchAPI<EVCode[]>('/v1/auth/tesla/codes')
}

/**
 * Returns the first active, non-expired EV code (or null).
 * Uses React Query with a 60-second refetchInterval instead of raw setInterval.
 */
export function useActiveEVCode() {
  return useQuery<EVCode | null>({
    queryKey: ['ev-codes', 'active'],
    queryFn: async () => {
      const codes = await fetchActiveEVCodes()
      const now = new Date()
      const active = codes.find((c) => {
        // Server returns UTC datetimes without 'Z' -- append it for correct parsing
        const expiresStr = c.expires_at.endsWith('Z') ? c.expires_at : c.expires_at + 'Z'
        return c.status === 'active' && new Date(expiresStr) > now
      })
      return active || null
    },
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30_000, // 30 sec
    refetchInterval: 60_000, // Poll every 60 seconds
    refetchOnWindowFocus: true,
    retry: false,
  })
}

// ===================== Wallet & Payouts =====================

export interface WalletBalance {
  available_cents: number
  pending_cents: number
  total_earned_cents: number
  total_withdrawn_cents: number
  can_withdraw: boolean
  minimum_withdrawal_cents: number
  stripe_onboarding_complete: boolean
  payout_provider?: string  // "stripe" or "dwolla"
  bank_verified?: boolean
}

export interface StripeAccountResult {
  stripe_account_id: string
  status: string
  onboarding_complete: boolean
}

export interface StripeAccountLinkResult {
  url: string
  expires_at: string
}

export interface WithdrawResult {
  payout_id: string
  status: string
  amount_cents: number
  stripe_transfer_id?: string
  mock?: boolean
}

export interface PayoutHistoryEntry {
  id: string
  amount_cents: number
  status: string
  created_at: string
  paid_at: string | null
  failure_reason: string | null
}

export async function fetchWalletBalance(): Promise<WalletBalance> {
  return fetchAPI<WalletBalance>('/v1/wallet/balance')
}

export async function createStripeAccount(email: string): Promise<StripeAccountResult> {
  return fetchAPI<StripeAccountResult>('/v1/wallet/stripe/account', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export async function createStripeAccountLink(returnUrl: string, refreshUrl: string): Promise<StripeAccountLinkResult> {
  return fetchAPI<StripeAccountLinkResult>('/v1/wallet/stripe/account-link', {
    method: 'POST',
    body: JSON.stringify({ return_url: returnUrl, refresh_url: refreshUrl }),
  })
}

export async function checkStripeStatus(): Promise<{ onboarding_complete: boolean; has_account: boolean; details_submitted?: boolean }> {
  return fetchAPI('/v1/wallet/stripe/status')
}

export async function requestWithdrawal(amountCents: number): Promise<WithdrawResult> {
  return fetchAPI<WithdrawResult>('/v1/wallet/withdraw', {
    method: 'POST',
    body: JSON.stringify({ amount_cents: amountCents }),
  })
}

export async function fetchPayoutHistory(limit = 20): Promise<{ payouts: PayoutHistoryEntry[] }> {
  return fetchAPI<{ payouts: PayoutHistoryEntry[] }>(`/v1/wallet/history?limit=${limit}`)
}

export function useWalletBalance() {
  return useQuery({
    queryKey: ['wallet'],
    queryFn: fetchWalletBalance,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 15000,
    refetchOnWindowFocus: true,
  })
}

export function usePayoutHistory(limit = 20) {
  return useQuery({
    queryKey: ['wallet', 'payouts', limit],
    queryFn: () => fetchPayoutHistory(limit),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
  })
}

// ===================== Driver Campaigns =====================

export interface DriverCampaign {
  id: string
  name: string
  sponsor_name: string
  sponsor_logo_url: string | null
  description: string | null
  reward_cents: number
  campaign_type: string
  eligible: boolean
  end_date: string | null
}

export async function fetchDriverCampaigns(lat?: number, lng?: number, chargerId?: string): Promise<{ campaigns: DriverCampaign[] }> {
  const params = new URLSearchParams()
  if (lat !== undefined) params.append('lat', String(lat))
  if (lng !== undefined) params.append('lng', String(lng))
  if (chargerId) params.append('charger_id', chargerId)
  return fetchAPI<{ campaigns: DriverCampaign[] }>(`/v1/campaigns/driver/active?${params.toString()}`)
}

export function useDriverCampaigns(lat?: number, lng?: number, chargerId?: string) {
  return useQuery({
    queryKey: ['driver-campaigns', lat, lng, chargerId],
    queryFn: () => fetchDriverCampaigns(lat, lng, chargerId),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 60000,
  })
}

// ===================== Wallet Ledger =====================

export interface WalletLedgerEntry {
  id: string
  amount_cents: number
  balance_after_cents: number
  transaction_type: string
  description: string | null
  created_at: string | null
  campaign_name: string | null
  sponsor_name: string | null
}

export async function fetchWalletLedger(limit = 50, offset = 0): Promise<{ entries: WalletLedgerEntry[]; count: number }> {
  return fetchAPI<{ entries: WalletLedgerEntry[]; count: number }>(`/v1/wallet/ledger?limit=${limit}&offset=${offset}`)
}

export function useWalletLedger(limit = 50) {
  return useQuery({
    queryKey: ['wallet', 'ledger', limit],
    queryFn: () => fetchWalletLedger(limit),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
  })
}

// ===================== Energy Reputation =====================

export interface EnergyReputation {
  points: number
  tier: string
  tier_color: string
  next_tier: string | null
  points_to_next: number | null
  progress_to_next: number
  streak_days: number
}

export async function fetchEnergyReputation(): Promise<EnergyReputation> {
  return fetchAPI<EnergyReputation>('/v1/charging-sessions/reputation')
}

export function useEnergyReputation() {
  return useQuery({
    queryKey: ['energy-reputation'],
    queryFn: fetchEnergyReputation,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 60000, // 1 minute
    retry: false,
  })
}

// ===================== Account Stats =====================

export interface AccountStats {
  total_sessions: number
  total_kwh: number
  total_earned_cents: number
  total_nova: number
  favorite_charger: { name: string; sessions: number } | null
  member_since: string | null
  current_streak: number
  co2_avoided_kg: number
}

export async function fetchAccountStats(): Promise<AccountStats> {
  return fetchAPI<AccountStats>('/v1/account/stats')
}

export function useAccountStats() {
  return useQuery({
    queryKey: ['account-stats'],
    queryFn: fetchAccountStats,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 60000,
  })
}

// ===================== Profile =====================

export async function updateProfile(data: { email?: string; display_name?: string }) {
  return fetchAPI('/v1/account/profile', {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

// ===================== Charger Favorites =====================

export async function toggleChargerFavorite(chargerId: string, isFavorite: boolean) {
  return fetchAPI(`/v1/chargers/${chargerId}/favorite`, {
    method: isFavorite ? 'DELETE' : 'POST',
  })
}

export async function fetchChargerFavorites(): Promise<{ favorites: string[] }> {
  return fetchAPI<{ favorites: string[] }>('/v1/chargers/favorites')
}

export function useChargerFavorites() {
  return useQuery({
    queryKey: ['charger-favorites'],
    queryFn: fetchChargerFavorites,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
  })
}

// ===================== Plaid / Funding Sources =====================

export interface PlaidLinkToken {
  link_token: string
  expiration: string
}

export interface FundingSourceData {
  id: string
  institution_name: string | null
  account_mask: string | null
  account_type: string | null
  is_default: boolean
  created_at: string
}

export async function createPlaidLinkToken(): Promise<PlaidLinkToken> {
  return fetchAPI<PlaidLinkToken>('/v1/wallet/plaid/link-token', { method: 'POST' })
}

export async function exchangePlaidToken(publicToken: string, accountId: string): Promise<{ ok: boolean }> {
  return fetchAPI<{ ok: boolean }>('/v1/wallet/plaid/exchange', {
    method: 'POST',
    body: JSON.stringify({ public_token: publicToken, account_id: accountId }),
  })
}

export async function fetchFundingSources(): Promise<{ funding_sources: FundingSourceData[] }> {
  return fetchAPI<{ funding_sources: FundingSourceData[] }>('/v1/wallet/funding-sources')
}

export async function removeFundingSource(id: string): Promise<{ ok: boolean }> {
  return fetchAPI<{ ok: boolean }>(`/v1/wallet/funding-sources/${id}`, { method: 'DELETE' })
}

// ===================== Merchant Rewards (Request-to-Join, Claims, Receipts) =====================

export async function requestMerchantJoin(
  placeId: string,
  merchantName: string,
  interestTags?: string[],
): Promise<RequestToJoinResponse> {
  return fetchAPI<RequestToJoinResponse>(`/v1/merchants/${placeId}/request-join`, {
    method: 'POST',
    body: JSON.stringify({
      place_id: placeId,
      merchant_name: merchantName,
      interest_tags: interestTags,
    }),
  })
}

export function useRequestToJoin() {
  return useMutation({
    mutationFn: ({ placeId, merchantName, interestTags }: { placeId: string; merchantName: string; interestTags?: string[] }) =>
      requestMerchantJoin(placeId, merchantName, interestTags),
  })
}

export async function claimReward(
  merchantName: string,
  placeId?: string,
  merchantId?: string,
  rewardDescription?: string,
): Promise<ClaimRewardResponse> {
  return fetchAPI<ClaimRewardResponse>('/v1/rewards/claim', {
    method: 'POST',
    body: JSON.stringify({
      merchant_name: merchantName,
      place_id: placeId,
      merchant_id: merchantId,
      reward_description: rewardDescription,
    }),
  })
}

export function useClaimReward() {
  return useMutation({
    mutationFn: ({ merchantName, placeId, merchantId, rewardDescription }: {
      merchantName: string; placeId?: string; merchantId?: string; rewardDescription?: string
    }) => claimReward(merchantName, placeId, merchantId, rewardDescription),
  })
}

export function useActiveClaims() {
  return useQuery({
    queryKey: ['reward-claims', 'active'],
    queryFn: () => fetchAPI<{ claims: ClaimRewardResponse[] }>('/v1/rewards/claims/active'),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
  })
}

export function useClaimDetail(claimId: string | null) {
  return useQuery({
    queryKey: ['reward-claims', claimId],
    queryFn: () => fetchAPI<ClaimDetailResponse>(`/v1/rewards/claims/${claimId}`),
    enabled: !!claimId && !!localStorage.getItem('access_token'),
    staleTime: 10000,
  })
}

export async function uploadReceipt(claimId: string, imageBase64: string): Promise<ReceiptUploadResponse> {
  const url = `${API_BASE_URL}/v1/rewards/claims/${claimId}/receipt`
  const token = localStorage.getItem('access_token')

  const formData = new FormData()
  formData.append('image_base64', imageBase64)

  const response = await fetch(url, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  })

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}))
    throw new ApiError(response.status, undefined, errData.detail || 'Failed to upload receipt')
  }
  return response.json()
}

export function useUploadReceipt() {
  return useMutation({
    mutationFn: ({ claimId, imageBase64 }: { claimId: string; imageBase64: string }) =>
      uploadReceipt(claimId, imageBase64),
  })
}

export function usePlaidLinkToken() {
  return useQuery({
    queryKey: ['plaid-link-token'],
    queryFn: createPlaidLinkToken,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 1800000, // 30 minutes
  })
}

export function useFundingSources() {
  return useQuery({
    queryKey: ['funding-sources'],
    queryFn: fetchFundingSources,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 30000,
  })
}

// ===================== Referrals =====================

export interface ReferralCodeData {
  code: string
  referral_link: string
}

export interface ReferralStats {
  total_referrals: number
  total_earned_cents: number
  pending_count: number
}

export async function fetchReferralCode(): Promise<ReferralCodeData> {
  return fetchAPI<ReferralCodeData>('/v1/referrals/code')
}

export async function fetchReferralStats(): Promise<ReferralStats> {
  return fetchAPI<ReferralStats>('/v1/referrals/stats')
}

export async function redeemReferralCode(code: string): Promise<{ ok: boolean; message: string }> {
  return fetchAPI<{ ok: boolean; message: string }>('/v1/referrals/redeem', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })
}

export function useReferralCode() {
  return useQuery({
    queryKey: ['referral-code'],
    queryFn: fetchReferralCode,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 300000, // 5 minutes
  })
}

export function useReferralStats() {
  return useQuery({
    queryKey: ['referral-stats'],
    queryFn: fetchReferralStats,
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 60000,
  })
}

// ===================== Public Stats =====================

export interface PublicStats {
  total_drivers: number
  total_earned_cents: number
  total_sessions: number
}

export async function fetchPublicStats(): Promise<PublicStats> {
  return fetchAPI<PublicStats>('/v1/stats/public')
}

export function usePublicStats() {
  return useQuery({
    queryKey: ['public-stats'],
    queryFn: fetchPublicStats,
    staleTime: 600000, // 10 minutes
  })
}

// ===================== Leaderboard =====================

export interface LeaderboardEntry {
  rank: number
  display_name: string
  total_earned_cents: number
  is_current_user: boolean
}

export interface LeaderboardResponse {
  entries: LeaderboardEntry[]
  current_user_rank: number | null
  current_user_earned_cents: number | null
}

export async function fetchLeaderboard(limit = 20): Promise<LeaderboardResponse> {
  return fetchAPI<LeaderboardResponse>(`/v1/leaderboard?limit=${limit}`)
}

export function useLeaderboard(limit = 20) {
  return useQuery({
    queryKey: ['leaderboard', limit],
    queryFn: () => fetchLeaderboard(limit),
    enabled: !!localStorage.getItem('access_token'),
    staleTime: 300000, // 5 minutes
  })
}

// ===================== Charger Search (Geocoded) =====================

export interface SearchChargerResult {
  id: string
  name: string
  lat: number
  lng: number
  distance_m: number
  network_name: string | null
  power_kw: number | null
  num_evse: number | null
  connector_types: string[]
  pricing_per_kwh: number | null
  has_merchant_perk?: boolean
  merchant_perk_title?: string
}

export interface GeocodedSearchResult {
  chargers: SearchChargerResult[]
  location: { lat: number; lng: number; name: string } | null
}

export async function searchChargers(query: string, lat?: number, lng?: number): Promise<GeocodedSearchResult> {
  const params = new URLSearchParams({ q: query })
  if (lat !== undefined) params.append('lat', String(lat))
  if (lng !== undefined) params.append('lng', String(lng))
  return fetchAPI<GeocodedSearchResult>(`/v1/chargers/search?${params.toString()}`, undefined, false)
}

// ===================== Street View Proxy =====================

export function getStreetViewUrl(chargerId: string): string {
  return `${API_BASE_URL}/v1/chargers/${chargerId}/streetview`
}

// ===================== Tesla Fleet Telemetry Configuration =====================

export async function configureTelemetry(): Promise<{ status: string; vin: string; telemetry_enabled: boolean }> {
  return fetchAPI('/v1/tesla/configure-telemetry', { method: 'POST' })
}

// ===================== Device Token Registration =====================

export async function registerDeviceToken(token: string, platform: 'ios' | 'android'): Promise<{ ok: boolean }> {
  return fetchAPI<{ ok: boolean }>('/v1/notifications/register-device', {
    method: 'POST',
    body: JSON.stringify({ fcm_token: token, platform }),
  })
}

// API client object for convenience
export const api = {
  get: <T>(endpoint: string, retryOn401 = true): Promise<T> => {
    return fetchAPI<T>(endpoint, { method: 'GET' }, retryOn401)
  },
  post: <T>(endpoint: string, data?: any, retryOn401 = true): Promise<T> => {
    return fetchAPI<T>(endpoint, {
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    }, retryOn401)
  },
  put: <T>(endpoint: string, data?: any, retryOn401 = true): Promise<T> => {
    return fetchAPI<T>(endpoint, {
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    }, retryOn401)
  },
  delete: <T>(endpoint: string, retryOn401 = true): Promise<T> => {
    return fetchAPI<T>(endpoint, { method: 'DELETE' }, retryOn401)
  },
}

// ---------------------------------------------------------------------------
// Loyalty
// ---------------------------------------------------------------------------

export interface LoyaltyProgressItem {
  card_id: string
  program_name: string
  visits_required: number
  reward_cents: number
  reward_description: string | null
  visit_count: number
  reward_unlocked: boolean
  reward_claimed: boolean
  last_visit_at: string | null
}

export async function fetchLoyaltyProgress(merchantId: string): Promise<LoyaltyProgressItem[]> {
  return fetchAPI<LoyaltyProgressItem[]>(`/v1/loyalty/progress?merchant_id=${merchantId}`)
}

export async function claimLoyaltyReward(cardId: string): Promise<{ ok: boolean; card_id: string; reward_claimed: boolean }> {
  return fetchAPI(`/v1/loyalty/rewards/${cardId}/claim`, { method: 'POST' })
}

export function useLoyaltyProgress(merchantId: string | null) {
  return useQuery({
    queryKey: ['loyalty-progress', merchantId],
    queryFn: () => fetchLoyaltyProgress(merchantId!),
    enabled: !!merchantId,
    staleTime: 30_000,
  })
}

