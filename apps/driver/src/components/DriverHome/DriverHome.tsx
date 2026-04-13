// DriverHome - Main orchestrator component for driver app
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { usePageVisibility } from '../../hooks/usePageVisibility'
import { useNavigate } from 'react-router-dom'
// Zap import removed - using logo image instead
import { useDriverSessionContext } from '../../contexts/DriverSessionContext'
import { useExclusiveSessionState } from '../../hooks/useExclusiveSessionState'
import { useQueryClient } from '@tanstack/react-query'
import {
  useIntentCapture,
  useActivateExclusive,
  useCompleteExclusive,
  useActiveExclusive,
  useLocationCheck,
  checkLocation,
} from '../../services/api'
import { useChargingState } from '../../hooks/useChargingState'
import { capture, page, DRIVER_EVENTS } from '../../analytics'
import { useFavorites } from '../../contexts/FavoritesContext'
import { ExclusiveActiveView } from '../ExclusiveActiveView/ExclusiveActiveView'
import { MerchantDetailModal } from '../MerchantDetail/MerchantDetailModal'
import { ActivateExclusiveModal } from '../ActivateExclusiveModal/ActivateExclusiveModal'
import { ArrivalConfirmationModal } from '../ArrivalConfirmationModal/ArrivalConfirmationModal'
import { CompletionFeedbackModal } from '../CompletionFeedbackModal/CompletionFeedbackModal'
import { PreferencesModal } from '../Preferences/PreferencesModal'
import { AnalyticsDebugPanel } from '../Debug/AnalyticsDebugPanel'
import { AccountPage } from '../Account/AccountPage'
import { LoginModal } from '../Account/LoginModal'
import { WalletModal } from '../Wallet/WalletModal'
import { TabBar, type TabId } from '../shared/TabBar'
import { groupMerchantsIntoSets, groupChargersIntoSets } from '../../utils/dataMapping'
import { getChargerSetsWithExperiences } from '../../mock/mockChargers'
import { isMockMode, isDemoMode } from '../../services/api'
// guaranteedFallback removed - app now uses real location data only
import { preloadImage } from '../../utils/imageCache'
import { ErrorBanner } from '../shared/ErrorBanner'
import { InlineError } from '../shared/InlineError'
// MerchantCarouselSkeleton, MerchantCardSkeleton removed — Discovery view handles loading
// Badge removed — header bar eliminated
import { LocationDeniedScreen } from '../LocationDenied/LocationDeniedScreen'
import { StateTransitionToast } from '../shared/StateTransitionToast'
import type { MockMerchant } from '../../mock/mockMerchants'
import type { ExclusiveMerchant } from '../../hooks/useExclusiveSessionState'
import { ChargerDetailSheet } from '../ChargerDetail/ChargerDetailSheet'
import { VehiclePage } from '../Vehicle/VehiclePage'
import type { MerchantSummary } from '../../types'
import { isTeslaBrowser } from '../../utils/evBrowserDetection'
import { EVHome } from '../EVHome/EVHome'
import { useSessionPolling } from '../../hooks/useSessionPolling'
import { useChargingSessions, useWalletBalance, useActiveEVCode, useTeslaStatus, registerDeviceToken, searchChargers } from '../../services/api'
import type { SearchChargerResult } from '../../services/api'
import { SessionActivityScreen } from '../SessionActivity/SessionActivityScreen'
import { DiscoveryView } from '../Discovery/DiscoveryView'

/**
 * Main entry point that orchestrates the three states:
 * - PRE_CHARGING: User not within charger radius
 * - CHARGING_ACTIVE: User within charger radius
 * - EXCLUSIVE_ACTIVE: User has activated an exclusive
 */
export function DriverHome() {
  const {
    locationPermission,
    locationFix,
    coordinates,
    appChargingState,
    sessionId,
    setAppChargingState,
    setSessionId,
    requestLocationPermission,
  } = useDriverSessionContext()
  const { activeExclusive, remainingMinutes, remainingSeconds, activateExclusive: activateExclusiveLocal, clearExclusive } = useExclusiveSessionState()
  const activateExclusiveMutation = useActivateExclusive()
  const completeExclusiveMutation = useCompleteExclusive()
  const { data: activeExclusiveData } = useActiveExclusive()
  const queryClient = useQueryClient()
  const manualClearRef = useRef(false)
  const chargingState = useChargingState()
  const navigate = useNavigate()

  // Check if Tesla browser - if so, show EV-optimized experience
  const [isTeslaBrowserUser, setIsTeslaBrowserUser] = useState(false)

  useEffect(() => {
    setIsTeslaBrowserUser(isTeslaBrowser())
  }, [])

  // Handle push notification deep links (e.g. charging_detected from Fleet Telemetry)
  useEffect(() => {
    const handlePushDeepLink = (e: CustomEvent<{ type: string; deep_link?: string; data?: any }>) => {
      if (e.detail.type === 'charging_detected') {
        // Invalidate session queries to pick up the telemetry-created session
        queryClient.invalidateQueries({ queryKey: ['charging-sessions'] })
        queryClient.invalidateQueries({ queryKey: ['charging-sessions', 'active'] })
        capture(DRIVER_EVENTS.PUSH_NOTIFICATION_TAPPED, { type: 'charging_detected' })
      }
    }
    window.addEventListener('nerava:push-deep-link', handlePushDeepLink as EventListener)
    return () => window.removeEventListener('nerava:push-deep-link', handlePushDeepLink as EventListener)
  }, [queryClient])

  // If Tesla browser and at charger, show EV-optimized experience
  // This can be enhanced to check if actually at charger via coordinates
  if (isTeslaBrowserUser && coordinates && appChargingState === 'CHARGING_ACTIVE') {
    return <EVHome />
  }

  const [currentSetIndex] = useState(0)
  const [selectedMerchant, setSelectedMerchant] = useState<MockMerchant | null>(null)
  const [selectedCharger, setSelectedCharger] = useState<{ id: string; name: string; network_name?: string; lat?: number; lng?: number } | null>(null)
  const [showActivateModal, setShowActivateModal] = useState(false)
  const [showArrivalModal, setShowArrivalModal] = useState(false)
  const [localExclusiveSessionId, setLocalExclusiveSessionId] = useState<string | null>(null)
  const [showCompletionModal, setShowCompletionModal] = useState(false)
  const [showPreferencesModal, setShowPreferencesModal] = useState(false)
  const [checkedIn, setCheckedIn] = useState(false)
  const [checkedInMerchantName, setCheckedInMerchantName] = useState<string | null>(null)
  const { favorites: likedMerchants, toggleFavorite, isFavorite } = useFavorites()
  const [primaryFilters, setPrimaryFilters] = useState<string[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchChargerResult[] | null>(null)
  const [searchLocation, setSearchLocation] = useState<{ lat: number; lng: number; name: string } | null>(null)
  const [isSearching, setIsSearching] = useState(false)

  // Geocoded search handler — calls backend /v1/chargers/search
  const handleSearchSubmit = useCallback(async (query: string) => {
    setIsSearching(true)
    try {
      const result = await searchChargers(query, coordinates?.lat, coordinates?.lng)
      setSearchResults(result.chargers)
      setSearchLocation(result.location)
      if (result.chargers.length === 0) {
        console.log('[DriverHome] Search returned no chargers for:', query)
      }
    } catch (err) {
      console.error('[DriverHome] Search failed:', err)
      setSearchResults(null)
      setSearchLocation(null)
    } finally {
      setIsSearching(false)
    }
  }, [coordinates])

  // Clear search results and return to local chargers
  const handleClearSearch = useCallback(() => {
    setSearchResults(null)
    setSearchLocation(null)
    setSearchQuery('')
  }, [])

  // Search chargers in a specific area (when user pans the map)
  const handleSearchArea = useCallback(async (lat: number, lng: number) => {
    setIsSearching(true)
    try {
      const result = await searchChargers('', lat, lng)
      setSearchResults(result.chargers)
      setSearchLocation({ lat, lng, name: 'Map area' })
    } catch (err) {
      console.error('[DriverHome] Area search failed:', err)
    } finally {
      setIsSearching(false)
    }
  }, [])

  // Wrapper to maintain compatibility with existing components
  const handleToggleLike = (merchantId: string) => {
    toggleFavorite(merchantId).catch((e) => {
      console.error('Failed to toggle favorite', e)
    })
  }

  // Primary filter toggle handler
  const handleFilterToggle = (filter: string) => {
    setPrimaryFilters((prev) =>
      prev.includes(filter) ? prev.filter((f) => f !== filter) : [...prev, filter]
    )
  }

  // Filter merchants by selected amenities
  // When multiple filters are selected, merchant must match ALL of them (AND logic)
  const filterMerchantsByAmenities = useCallback((merchants: MerchantSummary[]): MerchantSummary[] => {
    if (primaryFilters.length === 0) return merchants

    return merchants.filter((merchant) => {
      return primaryFilters.every((filter) => {
        const types = merchant.types || []
        const typesLower = types.map((t) => t.toLowerCase())

        switch (filter) {
          case 'bathroom':
            // Future: Check amenity votes, for now assume all merchants have bathrooms
            return true
          case 'food':
            return (
              typesLower.some((t) =>
                t.includes('restaurant') ||
                t.includes('food') ||
                t.includes('cafe') ||
                t.includes('bakery') ||
                t.includes('meal')
              ) || merchant.is_primary === true // Primary merchants are often food
            )
          case 'wifi':
            // Future: Check amenity votes, for now assume cafes/restaurants have WiFi
            return typesLower.some(
              (t) => t.includes('cafe') || t.includes('restaurant') || t.includes('coffee')
            )
          case 'pets':
            return typesLower.some((t) => t.includes('pet') || t.includes('veterinary'))
          case 'music':
            // Future: Check merchant type or amenity data
            return false // Placeholder - no backend data yet
          case 'patio':
            // Future: Check merchant type or amenity data
            return false // Placeholder - no backend data yet
          default:
            return false
        }
      })
    })
  }, [primaryFilters])

  // Filter merchants by search query (name, type, or category label)
  const filterMerchantsBySearch = useCallback((merchants: MerchantSummary[]): MerchantSummary[] => {
    if (!searchQuery.trim()) return merchants
    const q = searchQuery.toLowerCase()
    // Category label mappings (mirrors getCategoryLabel in dataMapping.ts)
    const categoryLabels: Record<string, string> = {
      cafe: 'coffee', restaurant: 'restaurant', bakery: 'bakery', bar: 'bar',
      store: 'store', shopping_mall: 'shopping', park: 'park', gym: 'gym',
      movie_theater: 'movies', pizza: 'pizza', pizzeria: 'pizza',
    }
    return merchants.filter((m) => {
      // Match on name
      if (m.name.toLowerCase().includes(q)) return true
      // Match on raw types
      const types = m.types || []
      if (types.some((t) => t.toLowerCase().includes(q))) return true
      // Match on resolved category labels
      for (const t of types) {
        const label = categoryLabels[t.toLowerCase()]
        if (label && label.includes(q)) return true
        // Also match the formatted fallback (e.g., "Italian Restaurant")
        const formatted = t.replace(/_/g, ' ').toLowerCase()
        if (formatted.includes(q)) return true
      }
      return false
    })
  }, [searchQuery])

  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    // Check if user has access token
    return !!localStorage.getItem('access_token')
  })
  const [showLoginModal, setShowLoginModal] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)

  // Sync authentication state when tokens change
  useEffect(() => {
    const checkAuth = () => {
      const hasToken = !!localStorage.getItem('access_token')
      setIsAuthenticated(hasToken)
    }

    // Check on mount and listen for cross-tab storage changes
    checkAuth()
    window.addEventListener('storage', checkAuth)

    // Listen for same-window token changes via custom event
    // (storage event only fires for cross-tab changes)
    window.addEventListener('nerava:auth-changed', checkAuth)

    return () => {
      window.removeEventListener('storage', checkAuth)
      window.removeEventListener('nerava:auth-changed', checkAuth)
    }
  }, [])

  // Refresh stale data when app returns from background
  usePageVisibility(useCallback(() => {
    // Invalidate only the queries that matter for fresh data on resume
    queryClient.invalidateQueries({ queryKey: ['active-exclusive'] })
    queryClient.invalidateQueries({ queryKey: ['location-check'] })
    queryClient.invalidateQueries({ queryKey: ['active-charging-session'] })
  }, [queryClient]))

  // Initialize browse mode if location is unavailable
  const [browseMode, setBrowseMode] = useState(() => {
    // Check if we should start in browse mode (no coordinates available)
    const stored = localStorage.getItem('nerava_driver_session')
    if (stored) {
      try {
        const data = JSON.parse(stored)
        if (!data.coordinates || !data.coordinates.lat || !data.coordinates.lng) {
          return true // Enable browse mode if no stored coordinates
        }
      } catch {
        // Invalid JSON, enable browse mode
        return true
      }
    }
    // Enable browse mode by default if no location available
    return true
  })

  // CRITICAL: Ensure app always starts in PRE_CHARGING state to show charger immediately
  // Only run on mount, before any API data loads
  const [hasInitialized, setHasInitialized] = useState(false)
  useEffect(() => {
    if (!hasInitialized && appChargingState !== 'PRE_CHARGING' && chargingState.state !== 'EXCLUSIVE_ACTIVE' && !activeExclusive) {
      setAppChargingState('PRE_CHARGING')
      chargingState.transitionTo('PRE_CHARGING')
      setHasInitialized(true)
    }
  }, [hasInitialized, appChargingState, chargingState.state, activeExclusive, setAppChargingState])
  const [showTransitionToast, setShowTransitionToast] = useState(false)
  const [currentTab, setCurrentTab] = useState<TabId>('stations')
  const [showSessionActivity, setShowSessionActivity] = useState(false)
  const [showVehiclePage, setShowVehiclePage] = useState(false)

  // Handle Stripe return redirect — open wallet and refresh data
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('stripe_return') === 'true' || params.get('stripe_refresh') === 'true') {
      // Clean up URL
      const url = new URL(window.location.href)
      url.searchParams.delete('stripe_return')
      url.searchParams.delete('stripe_refresh')
      window.history.replaceState({}, '', url.pathname)
      // Refresh wallet data and switch to wallet tab
      queryClient.invalidateQueries({ queryKey: ['wallet-balance'] })
      setCurrentTab('wallet')
    }
  }, [])

  const [incentiveToast, setIncentiveToast] = useState<number | null>(null)
  const [nativeBridgeError, setNativeBridgeError] = useState<string | null>(null)

  // Listen for native bridge events (session-rejected, auth-required)
  useEffect(() => {
    const handleSessionRejected = (e: Event) => {
      const reason = (e as CustomEvent).detail?.reason || 'Session could not be started'
      setNativeBridgeError(reason)
      setTimeout(() => setNativeBridgeError(null), 8000)
    }
    const handleAuthRequired = () => {
      setCurrentTab('account')
    }
    window.addEventListener('nerava:session-rejected', handleSessionRejected)
    window.addEventListener('nerava:auth-required', handleAuthRequired)
    return () => {
      window.removeEventListener('nerava:session-rejected', handleSessionRejected)
      window.removeEventListener('nerava:auth-required', handleAuthRequired)
    }
  }, [])

  // Listen for device token from native bridge and register with backend
  // Persist token to localStorage so it survives page reloads
  useEffect(() => {
    const handleNativeEvent = (event: Event) => {
      const { action, payload } = (event as CustomEvent).detail || {}
      if (action === 'DEVICE_TOKEN_REGISTERED' && payload?.token) {
        console.log('[DriverHome] Received device token from bridge, length:', payload.token.length)
        // Persist to localStorage so we can retry on future loads
        localStorage.setItem('nerava_device_token', payload.token)
        const platform = /android/i.test(navigator.userAgent) ? 'android' : 'ios'
        if (isAuthenticated) {
          console.log('[DriverHome] Registering device token with backend...')
          registerDeviceToken(payload.token, platform)
            .then(() => {
              console.log('[DriverHome] Device token registered successfully')
              capture(DRIVER_EVENTS.DEVICE_TOKEN_REGISTERED, { platform })
            })
            .catch((err) => {
              console.error('[DriverHome] Failed to register device token:', err)
            })
        } else {
          console.log('[DriverHome] Not authenticated yet, token saved for later registration')
        }
      }
    }
    window.addEventListener('neravaNative', handleNativeEvent)
    return () => window.removeEventListener('neravaNative', handleNativeEvent)
  }, [isAuthenticated])

  // Register device token on every authenticated load (retry from localStorage)
  useEffect(() => {
    if (!isAuthenticated) return
    const token = localStorage.getItem('nerava_device_token')
    if (token) {
      const platform = /android/i.test(navigator.userAgent) ? 'android' : 'ios'
      console.log('[DriverHome] Re-registering persisted device token with backend...')
      registerDeviceToken(token, platform)
        .then(() => {
          console.log('[DriverHome] Persisted device token registered successfully')
          capture(DRIVER_EVENTS.DEVICE_TOKEN_REGISTERED, { platform })
        })
        .catch((err) => {
          console.error('[DriverHome] Failed to register persisted device token:', err)
        })
    } else {
      console.log('[DriverHome] No device token in localStorage — notification permission may not be granted')
    }
  }, [isAuthenticated])

  // Active EV session state (from Tesla verify-charging) — via React Query
  const { data: activeEVSession } = useActiveEVCode()
  const [showEVCodeOverlay, setShowEVCodeOverlay] = useState(false)

  // Wallet balance (React Query)
  const { data: walletData, refetch: refetchWallet } = useWalletBalance()
  const walletBalance = walletData?.available_cents ?? 0
  const walletPending = walletData?.pending_cents ?? 0
  // Tesla connection status (for vehicle card)
  const { data: teslaStatus } = useTeslaStatus()

  // Session polling — detects charging via Tesla API
  const sessionPolling = useSessionPolling()
  const { data: sessionsData } = useChargingSessions(20)

  // Show incentive toast when session ends with a reward
  useEffect(() => {
    if (sessionPolling.lastIncentive && sessionPolling.lastIncentive.amountCents > 0) {
      capture(DRIVER_EVENTS.CHARGING_INCENTIVE_EARNED, {
        amount_cents: sessionPolling.lastIncentive.amountCents,
      })
      setIncentiveToast(sessionPolling.lastIncentive.amountCents)
      const timer = setTimeout(() => {
        setIncentiveToast(null)
        sessionPolling.clearIncentive()
      }, 5000)
      return () => clearTimeout(timer)
    }
  }, [sessionPolling.lastIncentive])

  // Track session detection
  useEffect(() => {
    if (sessionPolling.isActive && sessionPolling.sessionId) {
      capture(DRIVER_EVENTS.CHARGING_SESSION_DETECTED, {
        session_id: sessionPolling.sessionId,
      })
    }
  }, [sessionPolling.isActive, sessionPolling.sessionId])

  const lastChargingStateRef = useRef(appChargingState)

  // Auto-enable browse mode when location is denied or skipped
  // but still try to get real location via geolocation API
  useEffect(() => {
    if ((locationPermission === 'denied' || locationPermission === 'skipped') && !browseMode) {
      setBrowseMode(true)
      // Still try to get location - the user may have changed their mind in settings
      if (!coordinates) {
        requestLocationPermission()
      }
    }
  }, [locationPermission, browseMode, coordinates, requestLocationPermission])

  // Use real coordinates, fall back to Harker Heights TX center for browse mode
  // This ensures chargers load even when location is denied (Google Play reviewer scenario)
  const BROWSE_FALLBACK = { lat: 30.9876, lng: -97.6492, accuracy_m: 50 }
  const effectiveCoordinates = coordinates || (browseMode ? BROWSE_FALLBACK : null)

  // Intent capture request - only when location is available (or browse mode) and not in EXCLUSIVE_ACTIVE
  // Use useMemo to prevent infinite fetch loops - the request object must be stable
  // CRITICAL: Round coordinates to 4 decimal places to prevent GPS fluctuation from causing refetches
  const intentRequest = useMemo(() => {
    if (!effectiveCoordinates || chargingState.state === 'EXCLUSIVE_ACTIVE') {
      return null
    }
    // Round to 4 decimal places (~11m precision) to prevent GPS fluctuation loops
    const roundedLat = Math.round(effectiveCoordinates.lat * 10000) / 10000
    const roundedLng = Math.round(effectiveCoordinates.lng * 10000) / 10000
    return {
      lat: roundedLat,
      lng: roundedLng,
      accuracy_m: Math.round(effectiveCoordinates.accuracy_m || 0),
    }
  }, [
    // Use rounded values in dependency array to prevent recalculation on GPS fluctuation
    effectiveCoordinates ? Math.round(effectiveCoordinates.lat * 10000) : null,
    effectiveCoordinates ? Math.round(effectiveCoordinates.lng * 10000) : null,
    effectiveCoordinates ? Math.round(effectiveCoordinates.accuracy_m || 0) : null,
    chargingState.state
  ])

  const { data: intentData, isLoading: intentLoading, error: intentError, refetch: refetchIntent } = useIntentCapture(intentRequest)

  // Capture intent capture request when coordinates become available
  useEffect(() => {
    if (intentRequest && !intentLoading && !intentData) {
      capture(DRIVER_EVENTS.INTENT_CAPTURE_REQUEST, {
        location_accuracy: coordinates?.accuracy_m,
      })
    }
  }, [intentRequest, intentLoading, intentData, coordinates])

  // Store session_id when intent capture succeeds
  useEffect(() => {
    if (intentData?.session_id && intentData.session_id !== sessionId) {
      setSessionId(intentData.session_id)
      capture(DRIVER_EVENTS.INTENT_CAPTURE_SUCCESS, {
        session_id: intentData.session_id,
        location_accuracy: coordinates?.accuracy_m,
        merchant_count: intentData.merchants?.length || 0,
      })
    }
  }, [intentData?.session_id, sessionId, setSessionId, coordinates, intentData])

  // Capture page view on mount
  useEffect(() => {
    page('home')
  }, [])

  // Capture location permission events
  useEffect(() => {
    if (locationPermission === 'granted') {
      capture(DRIVER_EVENTS.LOCATION_PERMISSION_GRANTED)
    } else if (locationPermission === 'denied') {
      capture(DRIVER_EVENTS.LOCATION_PERMISSION_DENIED)
    }
  }, [locationPermission])

  // Determine which data to use (mock or real)
  const useMockData = isMockMode()
  const useDemoData = isDemoMode()
  const mockChargerSets = getChargerSetsWithExperiences()

  // Real data from intent capture
  // Debug: Log intent data to console
  useEffect(() => {
    if (import.meta.env.DEV && intentData) {
      console.log('[DriverHome] Intent data received:', {
        merchants_count: intentData.merchants?.length || 0,
        merchants: intentData.merchants,
        chargers_count: intentData.chargers?.length || 0,
        chargers: intentData.chargers,
        charger_summary: intentData.charger_summary,
        confidence_tier: intentData.confidence_tier,
        appChargingState,
      })
    }
  }, [intentData, appChargingState])

  // Check for API errors first (used in multiple places below)
  const hasApiError = intentError !== null && intentError !== undefined

  // Fix: Check for array existence AND length > 0 (empty array is truthy but has no items)
  // Apply search filter first, then amenity filter, before grouping into sets
  const searchFilteredMerchants = intentData?.merchants && Array.isArray(intentData.merchants) && intentData.merchants.length > 0
    ? filterMerchantsBySearch(intentData.merchants)
    : []
  const filteredMerchants = searchFilteredMerchants.length > 0
    ? filterMerchantsByAmenities(searchFilteredMerchants)
    : []
  const realMerchantSets = filteredMerchants.length > 0
    ? groupMerchantsIntoSets(filteredMerchants)
    : []
  // CRITICAL: Always create charger sets when chargers exist - this ensures chargers ALWAYS display
  // Use new chargers array if available, fall back to charger_summary for backward compatibility
  // When search results are active, convert SearchChargerResult[] to ChargerSummary[]
  const searchChargersAsSummary: import('../../types').ChargerSummary[] | null = searchResults
    ? searchResults.map(c => ({
        id: c.id,
        name: c.name,
        distance_m: c.distance_m,
        network_name: c.network_name ?? undefined,
        lat: c.lat,
        lng: c.lng,
        num_evse: c.num_evse ?? undefined,
        power_kw: c.power_kw ?? undefined,
        connector_types: c.connector_types,
        pricing_per_kwh: c.pricing_per_kwh,
        has_merchant_perk: c.has_merchant_perk,
        merchant_perk_title: c.merchant_perk_title ?? undefined,
      }))
    : null

  const chargersSource = searchChargersAsSummary
    ?? (intentData?.chargers && intentData.chargers.length > 0
      ? intentData.chargers
      : intentData?.charger_summary
      ? [intentData.charger_summary]
      : [])

  // Send visible chargers to native for dynamic geofencing (background detection)
  useEffect(() => {
    if (chargersSource.length > 0 && (window as any).neravaNative?.updateChargerGeofences) {
      const chargerList = chargersSource
        .filter((c: any) => c.lat && c.lng && c.id)
        .map((c: any) => ({ id: c.id, lat: c.lat, lng: c.lng }))
      if (chargerList.length > 0) {
        ;(window as any).neravaNative.updateChargerGeofences(chargerList)
      }
    }
  }, [chargersSource])

  // Pass filtered merchants to charger sets so search/filters affect charger experiences
  const merchantsForExperiences = filteredMerchants.length > 0 ? filteredMerchants
    : searchFilteredMerchants.length > 0 ? searchFilteredMerchants
    : intentData?.merchants || []
  const realChargerSets = chargersSource.length > 0
    ? groupChargersIntoSets(chargersSource, merchantsForExperiences)
    : []

  const hasChargers = chargersSource.length > 0

  // CRITICAL: If chargers exist, ALWAYS use real charger sets (never mock or empty)
  // Even if realChargerSets is empty, we'll recreate it in finalChargerSets below
  const chargerSets = hasChargers
    ? realChargerSets  // Always use real charger sets if chargers exist, even if empty (will be recreated below)
    : useMockData || (useDemoData && hasApiError && realChargerSets.length === 0)
    ? mockChargerSets
    : realChargerSets

  // Use charger sets in PRE_CHARGING mode, merchant sets in CHARGING_ACTIVE mode
  // CRITICAL: If chargers exist but chargerSets is empty, recreate it as a fallback
  // This ensures chargers ALWAYS display when chargers exist
  const finalChargerSets = (hasChargers && chargerSets.length === 0)
    ? groupChargersIntoSets(chargersSource, merchantsForExperiences)
    : chargerSets

  // Charger-first design: always show charger sets in the main carousel.
  // Users access merchants by tapping a charger card.
  const activeSets = finalChargerSets

  // Get nearest charger for radius check (first one, since they're sorted by distance)
  const nearestCharger = chargersSource[0]

  // Debug: Log computed sets
  useEffect(() => {
    if (import.meta.env.DEV) {
      console.log('[DriverHome] Computed sets:', {
        realMerchantSets_length: realMerchantSets.length,
        realChargerSets_length: realChargerSets.length,
        chargerSets_length: chargerSets.length,
        finalChargerSets_length: finalChargerSets.length,
        activeSets_length: activeSets.length,
        appChargingState,
        useMockData,
        useDemoData,
        hasApiError,
        intentData_merchants_length: intentData?.merchants?.length || 0,
        chargers_count: chargersSource.length,
        chargers_ids: chargersSource.map(c => c.id),
        nearest_charger_id: nearestCharger?.id,
        nearest_charger_distance_m: nearestCharger?.distance_m,
      })
    }
  }, [realMerchantSets, realChargerSets, chargerSets, finalChargerSets, activeSets, appChargingState, useMockData, useDemoData, hasApiError, intentData, chargersSource, nearestCharger])

  // Preload next carousel image when index changes
  useEffect(() => {
    if (activeSets.length > 0) {
      const nextIndex = (currentSetIndex + 1) % activeSets.length
      const nextSet = activeSets[nextIndex]
      if (nextSet?.featured?.imageUrl) {
        preloadImage(nextSet.featured.imageUrl).catch(() => {
          // Silently fail - preload is best effort
        })
      }
    }
  }, [currentSetIndex, activeSets])

  // Determine if user is in charger radius (for PRE_CHARGING state)
  const isInChargerRadius =
    appChargingState === 'CHARGING_ACTIVE' ||
    (nearestCharger && nearestCharger.distance_m < 150) ||
    (intentData?.confidence_tier === 'A')


  const handleCloseMerchantDetails = () => {
    setSelectedMerchant(null)
  }

  const handleActivateExclusive = async (merchant: MockMerchant) => {
    // ALWAYS do real-time location check first (not relying on state which can be manually toggled)
    if (!isMockMode()) {
      if (!coordinates) {
        setInlineError('Location required to activate exclusive offers.')
        return
      }

      try {
        const locationCheckResult = await checkLocation(coordinates.lat, coordinates.lng)
        if (!locationCheckResult.in_charger_radius) {
          capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_BLOCKED_OUTSIDE_RADIUS, {
            merchant_id: merchant.id,
            distance_m: locationCheckResult.distance_m,
            required_radius_m: 150,
          })
          setInlineError(`You must be at the charger to activate exclusive offers. You are ${Math.round(locationCheckResult.distance_m || 0)}m away.`)
          return
        }
      } catch (error) {
        console.error('Location check failed:', error)
        setInlineError('Unable to verify your location. Please try again.')
        return
      }
    }

    // Location verified - now check authentication
    if (!isAuthenticated) {
      // Show OTP modal for phone verification
      setSelectedMerchant(merchant)
      setShowActivateModal(true)
    } else {
      // User is already authenticated, proceed directly to activation
      handleActivateExclusiveDirect(merchant)
    }
  }

  // Check location to detect charger radius (for UI state, exclusive activation, etc.)
  const locationCheck = useLocationCheck(coordinates?.lat || null, coordinates?.lng || null)

  useEffect(() => {
    if (locationCheck.data && coordinates) {
      if (locationCheck.data.in_charger_radius && chargingState.state === 'PRE_CHARGING') {
        if (lastChargingStateRef.current === 'PRE_CHARGING') {
          setShowTransitionToast(true)
        }
        chargingState.transitionTo('CHARGING_ACTIVE')
        setAppChargingState('CHARGING_ACTIVE')
        lastChargingStateRef.current = 'CHARGING_ACTIVE'
      } else if (!locationCheck.data.in_charger_radius && chargingState.state === 'CHARGING_ACTIVE' && !activeExclusive) {
        chargingState.transitionTo('PRE_CHARGING')
        setAppChargingState('PRE_CHARGING')
        lastChargingStateRef.current = 'PRE_CHARGING'
      }
    }
  }, [locationCheck.data, coordinates, chargingState.state, activeExclusive, setAppChargingState])

  useEffect(() => {
    lastChargingStateRef.current = appChargingState
  }, [appChargingState])

  // Sync active exclusive from backend
  useEffect(() => {
    if (activeExclusiveData?.exclusive_session) {
      const session = activeExclusiveData.exclusive_session
      if (!activeExclusive && !manualClearRef.current) {
        // Convert backend session to ExclusiveMerchant
        const distanceM = session.merchant_distance_m
        const distanceLabel = distanceM != null
          ? (distanceM < 1000 ? `${Math.round(distanceM)}m` : `${(distanceM / 1609.34).toFixed(1)} miles`)
          : undefined
        const walkMin = session.merchant_walk_time_min
        const exclusiveMerchant: ExclusiveMerchant = {
          id: session.merchant_id || '',
          name: session.merchant_name || '',
          category: session.merchant_category || undefined,
          walkTime: walkMin != null ? `${walkMin} min walk` : '5 min',
          imageUrl: session.merchant_photo_url || null,
          exclusiveOffer: session.exclusive_title || undefined,
          distance: distanceLabel,
          lat: session.merchant_lat ?? undefined,
          lng: session.merchant_lng ?? undefined,
          expiresAt: session.expires_at,
        }
        activateExclusiveLocal(exclusiveMerchant, session.expires_at)
        setLocalExclusiveSessionId(session.id)
        chargingState.transitionTo('EXCLUSIVE_ACTIVE')
        setAppChargingState('EXCLUSIVE_ACTIVE')
      }
    } else if (activeExclusiveData?.exclusive_session === null || activeExclusiveData?.exclusive_session === undefined) {
      // Backend confirms no active session — reset manual clear flag
      manualClearRef.current = false
      if (activeExclusive) {
        // Session expired or completed
        clearExclusive()
        chargingState.transitionTo('PRE_CHARGING')
        setAppChargingState('PRE_CHARGING')
      }
    }
  }, [activeExclusiveData, activeExclusive, activateExclusiveLocal, clearExclusive, chargingState.state, setAppChargingState])

  const handleActivateExclusiveDirect = async (merchant: MockMerchant) => {
    // Check charger radius guard
    if (!coordinates) {
      setInlineError('Location required to activate exclusive.')
      return
    }

    // Capture activation click (both original and new format)
    capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_CLICK, {
      merchant_id: merchant.id,
    })
    capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_CLICKED, {
      merchant_id: merchant.id,
      path: window.location.pathname,
    })

    if (!isMockMode()) {
      // Check location first
      try {
        const locationCheckResult = await checkLocation(coordinates.lat, coordinates.lng)
        if (!locationCheckResult.in_charger_radius) {
          capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_BLOCKED_OUTSIDE_RADIUS, {
            merchant_id: merchant.id,
            distance_m: locationCheckResult.distance_m,
            required_radius_m: 150,
          })
          setInlineError(`You must be at the charger to activate. Distance: ${locationCheckResult.distance_m?.toFixed(0)}m, required: 150m`)
          return
        }

        // Check confidence tier (from intent data)
        const confidenceTier = intentData?.confidence_tier
        if (confidenceTier && !['A', 'B'].includes(confidenceTier)) {
          setInlineError('Exclusive activation requires high confidence location. Please wait for better GPS signal.')
          return
        }

        // Activate exclusive via backend
        try {
          const idempotencyKey = crypto.randomUUID()
          const response = await activateExclusiveMutation.mutateAsync({
            request: {
              merchant_id: merchant.id,
              charger_id: locationCheckResult.nearest_charger_id || 'unknown',
              lat: coordinates.lat,
              lng: coordinates.lng,
              accuracy_m: coordinates.accuracy_m,
              intent_session_id: sessionId || undefined,
            },
            idempotencyKey,
          })

          // Convert response + local merchant to ExclusiveMerchant
          // Prefer backend-enriched fields (photo, category) over local mock data
          const sess = response.exclusive_session
          const exclusiveMerchant: ExclusiveMerchant = {
            id: merchant.id,
            name: sess.merchant_name || merchant.name,
            category: sess.merchant_category || merchant.category,
            walkTime: sess.merchant_walk_time_min != null ? `${sess.merchant_walk_time_min} min walk` : merchant.walkTime,
            imageUrl: sess.merchant_photo_url || merchant.imageUrl,
            badge: merchant.badges?.includes('Exclusive') ? '⭐ Exclusive' : undefined,
            distance: merchant.distance,
            hours: merchant.hours,
            hoursStatus: merchant.hoursStatus,
            description: merchant.description,
            exclusiveOffer: sess.exclusive_title || merchant.exclusiveOffer,
            lat: sess.merchant_lat ?? undefined,
            lng: sess.merchant_lng ?? undefined,
          }

          activateExclusiveLocal(exclusiveMerchant, response.exclusive_session.expires_at)
          setLocalExclusiveSessionId(response.exclusive_session.id)
          chargingState.transitionTo('EXCLUSIVE_ACTIVE')
          setAppChargingState('EXCLUSIVE_ACTIVE')

          capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_SUCCESS, {
            merchant_id: merchant.id,
            exclusive_id: response.exclusive_session.id,
            session_id: response.exclusive_session.id,
          })
        } catch (error: any) {
          console.error('Failed to activate exclusive:', error)
          capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_FAIL, {
            merchant_id: merchant.id,
            error: error.message || 'Unknown error',
          })
          if (error.status === 403) {
            setInlineError(error.message || 'You must be at the charger to activate exclusive.')
          } else {
            setInlineError('Failed to activate exclusive. Please try again.')
          }
          return
        }
      } catch (error) {
        console.error('Location check failed:', error)
        setInlineError('Failed to verify location. Please try again.')
        return
      }
    } else {
      // Mock mode - use local activation
      const exclusiveMerchant: ExclusiveMerchant = {
        id: merchant.id,
        name: merchant.name,
        category: merchant.category,
        walkTime: merchant.walkTime,
        imageUrl: merchant.imageUrl,
        badge: merchant.badges?.includes('Exclusive') ? '⭐ Exclusive' : undefined,
        distance: merchant.distance,
        hours: merchant.hours,
        hoursStatus: merchant.hoursStatus,
        description: merchant.description,
        exclusiveOffer: merchant.exclusiveOffer,
      }
      activateExclusiveLocal(exclusiveMerchant)
      chargingState.transitionTo('EXCLUSIVE_ACTIVE')
      setAppChargingState('EXCLUSIVE_ACTIVE')
    }

    setShowActivateModal(false)
    setSelectedMerchant(null)
  }

  const handleOTPSuccess = () => {
    // User successfully authenticated (tokens stored in auth.ts)
    setIsAuthenticated(true)
    setShowActivateModal(false)

    // DO NOT automatically activate - user is now authenticated
    // They can tap "Activate Exclusive" again which will do location check
    // This keeps OTP verification separate from location-gated activation
  }

  const handleOTPClose = () => {
    setShowActivateModal(false)
  }

  const handleArrived = () => {
    setShowArrivalModal(true)
  }

  const handleArrivalDone = async () => {
    setShowArrivalModal(false)

    // Complete the exclusive session via backend
    if (activeExclusive && !isMockMode()) {
      const activeSessionId = activeExclusiveData?.exclusive_session?.id
      if (activeSessionId) {
        try {
          await completeExclusiveMutation.mutateAsync({
            exclusive_session_id: activeSessionId,
          })
          capture(DRIVER_EVENTS.EXCLUSIVE_COMPLETE_SUCCESS, {
            merchant_id: activeExclusive.id,
            session_id: activeSessionId,
          })
        } catch (error: any) {
          console.error('Failed to complete exclusive:', error)
        }
      }
    }

    // Save merchant name for banner, then clear exclusive and return to discovery
    setCheckedInMerchantName(activeExclusive?.name || null)
    setCheckedIn(true)
    // Prevent sync logic from recreating the exclusive from stale cache
    manualClearRef.current = true
    setLocalExclusiveSessionId(null)
    clearExclusive()
    chargingState.transitionTo('PRE_CHARGING')
    setAppChargingState('PRE_CHARGING')
    // Invalidate cache so next fetch gets fresh data from backend
    queryClient.invalidateQueries({ queryKey: ['active-exclusive'] })

    // Auto-hide the banner after 5 seconds
    setTimeout(() => {
      setCheckedIn(false)
      setCheckedInMerchantName(null)
    }, 5000)
  }

  const handleCancelExclusive = async () => {
    // Complete/cancel the exclusive session via backend
    if (activeExclusive && !isMockMode()) {
      const activeSessionId = activeExclusiveData?.exclusive_session?.id
      if (activeSessionId) {
        try {
          await completeExclusiveMutation.mutateAsync({
            exclusive_session_id: activeSessionId,
          })
        } catch (error: any) {
          console.error('Failed to cancel exclusive:', error)
        }
      }
    }
    // Prevent sync logic from recreating the exclusive from stale cache
    manualClearRef.current = true
    clearExclusive()
    chargingState.transitionTo('PRE_CHARGING')
    setAppChargingState('PRE_CHARGING')
    // Invalidate cache so next fetch gets fresh data from backend
    queryClient.invalidateQueries({ queryKey: ['active-exclusive'] })
  }

  const handleCompletionContinue = async () => {
    setShowCompletionModal(false)

    // Capture completion click (both original and new format)
    if (activeExclusive) {
      capture(DRIVER_EVENTS.EXCLUSIVE_COMPLETE_CLICK, {
        merchant_id: activeExclusive.id,
      })
      capture(DRIVER_EVENTS.EXCLUSIVE_DONE_CLICKED, {
        merchant_id: activeExclusive.id,
        path: window.location.pathname,
      })
    }

    // Complete exclusive session via backend
    if (activeExclusive && !isMockMode()) {
      const activeSessionId = activeExclusiveData?.exclusive_session?.id
      if (activeSessionId) {
        try {
          await completeExclusiveMutation.mutateAsync({
            exclusive_session_id: activeSessionId,
          })

          capture(DRIVER_EVENTS.EXCLUSIVE_COMPLETE_SUCCESS, {
            merchant_id: activeExclusive.id,
            session_id: activeSessionId,
          })
        } catch (error: any) {
          console.error('Failed to complete exclusive:', error)
          capture(DRIVER_EVENTS.EXCLUSIVE_COMPLETE_FAIL, {
            merchant_id: activeExclusive.id,
            error: error.message || 'Unknown error',
          })
          // Continue anyway - show preferences
        }
      }
    }

    // Transition to COMPLETE state and show preferences
    chargingState.transitionTo('COMPLETE')
    setShowPreferencesModal(true)
  }

  const handlePreferencesDone = () => {
    setShowPreferencesModal(false)
    // Reset state and return to discovery
    clearExclusive()
    chargingState.transitionTo(appChargingState === 'CHARGING_ACTIVE' ? 'CHARGING_ACTIVE' : 'PRE_CHARGING')
    setAppChargingState(appChargingState === 'CHARGING_ACTIVE' ? 'CHARGING_ACTIVE' : 'PRE_CHARGING')
  }

  // Dev toggle removed — charging state is automatic via location/Tesla API

  // Dev console logging
  useEffect(() => {
    if (import.meta.env.DEV) {
      console.group('Nerava Integration')
      console.log('Mock mode:', isMockMode())
      console.log('API base URL:', import.meta.env.VITE_API_BASE_URL || 'http://localhost:8001')
      console.log('Location permission:', locationPermission)
      console.log('Location fix:', locationFix)
      console.log('Coordinates:', coordinates)
      console.log('Browse mode:', browseMode)
      console.log('Effective coordinates:', effectiveCoordinates)
      console.log('Intent request:', intentRequest)
      console.log('App charging state:', appChargingState)
      console.log('Charging state machine:', chargingState.state)
      console.log('Session ID:', sessionId)
      console.log('Intent capture loading:', intentLoading)
      console.log('Intent data:', intentData)
      console.log('Intent error:', intentError)
      console.groupEnd()
    }
  }, [locationPermission, locationFix, coordinates, browseMode, effectiveCoordinates, intentRequest, appChargingState, sessionId, intentLoading, intentData, intentError])

  // Auto-enter browse mode when location is denied (never block the UI)
  useEffect(() => {
    if ((locationPermission === 'denied' || locationFix === 'error') && !browseMode) {
      setBrowseMode(true)
    }
  }, [locationPermission, locationFix, browseMode])

  // Never show location denied screen — always fall through to browse mode
  const showLocationDenied = false

  const handleTryAgain = () => {
    requestLocationPermission()
  }

  const handleBrowseChargers = () => {
    setBrowseMode(true)
  }

  // If location denied and not in browse mode, show recovery screen
  if (showLocationDenied) {
    return (
      <LocationDeniedScreen
        onTryAgain={handleTryAgain}
        onBrowseChargers={handleBrowseChargers}
      />
    )
  }

  // If exclusive is active and user is on stations tab, show exclusive view
  if (activeExclusive && currentTab === 'stations') {
    return (
      <>
        <ExclusiveActiveView
          merchant={activeExclusive}
          remainingMinutes={remainingMinutes}
          remainingSeconds={remainingSeconds}
          onArrived={handleArrived}
          onCancel={handleCancelExclusive}
          onExpired={() => {
            clearExclusive()
            chargingState.transitionTo('CHARGING_ACTIVE')
            setAppChargingState('CHARGING_ACTIVE')
          }}
          onToggleLike={handleToggleLike}
          onShare={() => {
            const text = `Check out ${activeExclusive.name} on Nerava! ${activeExclusive.exclusiveOffer ? `They're offering: ${activeExclusive.exclusiveOffer}` : ''}`
            const url = `https://app.nerava.network`
            if (navigator.share) {
              navigator.share({ title: activeExclusive.name, text, url }).catch(() => {})
            } else {
              navigator.clipboard.writeText(`${text} ${url}`).then(() => {
                alert('Link copied to clipboard!')
              }).catch(() => {})
            }
          }}
          isLiked={isFavorite(activeExclusive.id)}
        />
        <ArrivalConfirmationModal
          isOpen={showArrivalModal}
          merchantName={activeExclusive.name}
          merchantId={activeExclusive.id}
          exclusiveBadge={activeExclusive.badge}
          exclusiveSessionId={activeExclusiveData?.exclusive_session?.id || localExclusiveSessionId || undefined}
          lat={effectiveCoordinates?.lat}
          lng={effectiveCoordinates?.lng}
          onDone={handleArrivalDone}
        />
        <CompletionFeedbackModal
          isOpen={showCompletionModal}
          onContinue={handleCompletionContinue}
        />
        <PreferencesModal
          isOpen={showPreferencesModal}
          onClose={handlePreferencesDone}
        />
      </>
    )
  }

  // Discovery View - Hidden when exclusive is active
  return (
    <>
      <StateTransitionToast
        show={showTransitionToast}
        onHide={() => setShowTransitionToast(false)}
      />
      <div className="bg-white text-[#050505] w-full flex flex-col overflow-hidden" style={{ height: 'var(--app-height, 100dvh)', minHeight: 'var(--app-height, 100dvh)' }}>
        {/* Stations Tab — Full-bleed map */}
        {currentTab === 'stations' && (appChargingState === 'PRE_CHARGING' || appChargingState === 'CHARGING_ACTIVE') ? (
          <>
            {/* Native bridge error banner */}
            {nativeBridgeError && (
              <ErrorBanner
                message={nativeBridgeError}
                onRetry={() => setNativeBridgeError(null)}
              />
            )}

            {/* Inline error for activation/location failures */}
            <InlineError
              message={inlineError}
              onDismiss={() => setInlineError(null)}
              className="mx-4 mb-2"
            />

            {false ? (
              null
            ) : (
              <DiscoveryView
                chargers={chargersSource}
                merchants={searchResults ? [] : (intentData?.merchants || [])}
                userLat={effectiveCoordinates?.lat}
                userLng={effectiveCoordinates?.lng}
                isLoading={intentLoading || isSearching}
                hasError={hasApiError && !useDemoData && !useMockData}
                searchQuery={searchQuery}
                onSearchChange={(q) => {
                  setSearchQuery(q)
                  // Clear search results when text is cleared
                  if (!q.trim()) {
                    handleClearSearch()
                  }
                  if (q.trim().length >= 2) {
                    capture(DRIVER_EVENTS.SEARCH_QUERY, { query: q })
                  }
                }}
                selectedFilters={primaryFilters}
                onFilterToggle={handleFilterToggle}
                onChargerSelect={(charger) => {
                  setSelectedCharger({
                    id: charger.id,
                    name: charger.name,
                    network_name: charger.network_name,
                    lat: charger.lat,
                    lng: charger.lng,
                  })
                }}
                onMerchantSelect={(placeId, photoUrl) => {
                  const params = new URLSearchParams()
                  if (photoUrl) params.set('photo', photoUrl)
                  const queryString = params.toString()
                  navigate(`/merchant/${placeId}${queryString ? `?${queryString}` : ''}`)
                }}
                onRefresh={() => {
                  if (searchResults) {
                    handleClearSearch()
                  }
                  refetchIntent()
                }}
                likedMerchants={Array.from(likedMerchants)}
                onToggleLike={handleToggleLike}
                activeSession={sessionPolling.isActive ? {
                  sessionId: sessionPolling.sessionId,
                  durationMinutes: sessionPolling.durationMinutes,
                  kwhDelivered: sessionPolling.kwhDelivered,
                  onTap: () => {
                    capture(DRIVER_EVENTS.CHARGING_ACTIVITY_OPENED)
                    setShowSessionActivity(true)
                  },
                } : null}
                onSearchSubmit={handleSearchSubmit}
                onClearSearch={handleClearSearch}
                searchLocation={searchLocation}
                isSearching={isSearching}
                vehicle={teslaStatus ? {
                  connected: teslaStatus.connected,
                  name: localStorage.getItem('nerava_vehicle_nickname') || teslaStatus.vehicle_name || (teslaStatus.connected ? 'My Tesla' : undefined),
                  vin: teslaStatus.vin || undefined,
                  vehicleModel: teslaStatus.vehicle_model || undefined,
                  vehicleYear: teslaStatus.vehicle_year ?? null,
                  exteriorColor: teslaStatus.exterior_color ?? null,
                  batteryPercent: sessionPolling.batteryLevel ?? teslaStatus.battery_level ?? null,
                  isCharging: sessionPolling.isActive,
                  durationMinutes: sessionPolling.durationMinutes,
                  minutesToFull: sessionPolling.minutesToFull,
                  kwhDelivered: sessionPolling.kwhDelivered,
                  onTap: () => {
                    setShowVehiclePage(true)
                  },
                  onConnect: async () => {
                    try {
                      const { api } = await import('../../services/api')
                      const response = await api.get<{ authorization_url: string }>('/v1/auth/tesla/connect')
                      window.location.href = response.authorization_url
                    } catch (e) {
                      console.error('Failed to start Tesla connection:', e)
                    }
                  },
                } : isAuthenticated ? {
                  connected: false,
                  onTap: () => {},
                  onConnect: async () => {
                    try {
                      const { api } = await import('../../services/api')
                      const response = await api.get<{ authorization_url: string }>('/v1/auth/tesla/connect')
                      window.location.href = response.authorization_url
                    } catch (e) {
                      console.error('Failed to start Tesla connection:', e)
                    }
                  },
                } : null}
                onSearchArea={handleSearchArea}
                walletBalanceCents={isAuthenticated ? walletBalance : undefined}
                onWalletTap={() => setCurrentTab('wallet')}
              />
            )}

            {/* Check-in Success Toast — floating */}
            {checkedIn && checkedInMerchantName && (
              <div className="absolute top-16 left-3 right-3 z-[2001]">
                <div className="bg-green-600/90 backdrop-blur-md rounded-2xl px-4 py-3 flex items-center gap-3 shadow-lg">
                  <div className="w-8 h-8 bg-white/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <svg className="w-4 h-4 text-white" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" /></svg>
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-white">Checked in at {checkedInMerchantName}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Incentive Toast — floating */}
            {incentiveToast !== null && (
              <div className="absolute top-16 left-3 right-3 z-[2001]">
                <div className="bg-green-600/90 backdrop-blur-md rounded-2xl px-4 py-3 flex items-center gap-3 shadow-lg">
                  <div className="w-8 h-8 bg-white/20 rounded-full flex items-center justify-center flex-shrink-0">
                    <svg className="w-4 h-4 text-white" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" /></svg>
                  </div>
                  <p className="flex-1 text-sm font-medium text-white">
                    You earned ${(incentiveToast / 100).toFixed(2)} from charging!
                  </p>
                  <button
                    onClick={() => { setIncentiveToast(null); sessionPolling.clearIncentive() }}
                    className="text-white/70 hover:text-white p-1"
                    aria-label="Dismiss"
                  >
                    <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" /></svg>
                  </button>
                </div>
              </div>
            )}
          </>
        ) : currentTab === 'stations' ? (
          <div className="flex-1 overflow-hidden" />
        ) : null}

        {/* Wallet Tab */}
        {currentTab === 'wallet' && (
          <div className="flex-1 overflow-hidden">
            {!isAuthenticated ? (
              <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
                <div className="w-16 h-16 bg-blue-50 rounded-full flex items-center justify-center mb-4">
                  <svg className="w-8 h-8 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1" /></svg>
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Sign in to view your wallet</h3>
                <p className="text-sm text-gray-500 mb-6">Earn rewards from charging sessions and withdraw to your bank.</p>
                <button
                  onClick={() => setShowLoginModal(true)}
                  className="px-6 py-3 bg-blue-600 text-white text-sm font-semibold rounded-xl"
                >
                  Sign In
                </button>
                <p className="text-xs text-gray-400 mt-4 text-center">
                  Join drivers charging at supported stations. Get paid after every verified session.
                </p>
              </div>
            ) : (
              <WalletModal
                isOpen={true}
                asPage={true}
                onClose={() => setCurrentTab('stations')}
                balance={walletBalance}
                pendingBalance={walletPending}
                stripeOnboardingComplete={walletData?.stripe_onboarding_complete ?? false}
                recentTransactions={
                  sessionsData?.sessions
                    ?.filter((s) => s.incentive && s.incentive.amount_cents > 0)
                    .map((s) => ({
                      id: s.id,
                      type: 'credit' as const,
                      description: `Charging reward${s.charger_network ? ` \u2022 ${s.charger_network}` : ''}`,
                      amount: s.incentive!.amount_cents,
                      timestamp: s.incentive!.granted_at || s.session_end || s.session_start || new Date().toISOString(),
                    })) || []
                }
                onBalanceChanged={() => refetchWallet()}
                userEmail={(() => { try { const u = JSON.parse(localStorage.getItem('nerava_user') || '{}'); return u.email || ''; } catch { return ''; } })()}
              />
            )}
          </div>
        )}

        {/* Account Tab */}
        {currentTab === 'account' && (
          <div className="flex-1 overflow-y-auto">
            <AccountPage
              onClose={() => setCurrentTab('stations')}
              onViewActivity={() => {
                capture(DRIVER_EVENTS.CHARGING_ACTIVITY_OPENED)
                setShowSessionActivity(true)
              }}
              onViewVehicle={() => setShowVehiclePage(true)}
              onChargerSelect={(chargerId) => {
                setCurrentTab('stations')
                setSelectedCharger({
                  id: chargerId,
                  name: '',
                  network_name: '',
                  lat: 0,
                  lng: 0,
                })
              }}
            />
          </div>
        )}

        {/* Bottom Tab Bar */}
        <TabBar
          activeTab={currentTab}
          onTabChange={setCurrentTab}
          walletBalance={isAuthenticated ? walletBalance : undefined}
          showTeslaPrompt={isAuthenticated && !walletData?.stripe_onboarding_complete}
        />
      </div>

      {/* Charger Detail Bottom Sheet */}
      {selectedCharger && (
        <ChargerDetailSheet
          chargerId={selectedCharger.id}
          chargerName={selectedCharger.name}
          networkName={selectedCharger.network_name}
          lat={selectedCharger.lat}
          lng={selectedCharger.lng}
          userLat={effectiveCoordinates?.lat}
          userLng={effectiveCoordinates?.lng}
          onClose={() => setSelectedCharger(null)}
          isCharging={sessionPolling.isActive}
          isAuthenticated={isAuthenticated}
          onLoginRequired={() => setShowLoginModal(true)}
          onViewSession={() => {
            capture(DRIVER_EVENTS.CHARGING_ACTIVITY_OPENED)
            setShowSessionActivity(true)
          }}
          onClaimActivated={() => {
            setSelectedCharger(null)
            // Force refetch so wallet shows the claim card immediately
            queryClient.invalidateQueries({ queryKey: ['active-exclusive'] })
            setCurrentTab('wallet')
          }}
        />
      )}

      {/* Merchant Details Modal */}
      {selectedMerchant && (
        <MerchantDetailModal
          merchant={selectedMerchant}
          isCharging={chargingState.state === 'CHARGING_ACTIVE' || chargingState.state === 'EXCLUSIVE_ACTIVE'}
          isInChargerRadius={isInChargerRadius}
          onClose={handleCloseMerchantDetails}
          onToggleLike={handleToggleLike}
          onActivateExclusive={handleActivateExclusive}
          likedMerchants={likedMerchants}
        />
      )}

      {/* Activation Modal (OTP) */}
      <ActivateExclusiveModal
        isOpen={showActivateModal}
        onClose={handleOTPClose}
        onSuccess={handleOTPSuccess}
      />

      {/* Preferences Modal */}
      <PreferencesModal
        isOpen={showPreferencesModal}
        onClose={handlePreferencesDone}
      />

      {/* Session Activity Screen */}
      {showSessionActivity && (
        <SessionActivityScreen
          onClose={() => setShowSessionActivity(false)}
          isActive={sessionPolling.isActive}
          durationMinutes={sessionPolling.durationMinutes}
          kwhDelivered={sessionPolling.kwhDelivered}
        />
      )}


      {/* Vehicle Page */}
      {showVehiclePage && (
        <VehiclePage
          onClose={() => setShowVehiclePage(false)}
          isCharging={sessionPolling.isActive}
          durationMinutes={sessionPolling.durationMinutes}
          kwhDelivered={sessionPolling.kwhDelivered}
          minutesToFull={sessionPolling.minutesToFull}
        />
      )}

      {/* Active EV Code Overlay */}
      {showEVCodeOverlay && activeEVSession && (
        <div className="fixed inset-0 z-[3000] bg-white flex flex-col" style={{ height: 'var(--app-height, 100dvh)' }}>
          <header className="bg-white border-b border-[#E4E6EB] flex-shrink-0 px-4 py-3 flex items-center">
            <button
              onClick={() => setShowEVCodeOverlay(false)}
              className="flex items-center text-gray-600"
            >
              <svg className="w-5 h-5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
              Back
            </button>
          </header>

          <div className="flex-1 flex flex-col items-center justify-center p-6">
            <div className="w-full max-w-sm">
              <div className="p-6 bg-gradient-to-b from-green-50 to-white rounded-xl border border-green-200">
                <div className="text-center">
                  <div className="text-5xl mb-4">⚡</div>
                  <h2 className="text-xl font-bold text-gray-900 mb-2">Charging Verified!</h2>
                  <p className="text-gray-600 mb-2">
                    {activeEVSession.merchant_name || 'Active Session'}
                  </p>
                  <p className="text-sm text-gray-500 mb-6">
                    {(() => {
                      const expiresStr = activeEVSession.expires_at.endsWith('Z')
                        ? activeEVSession.expires_at
                        : activeEVSession.expires_at + 'Z'
                      const mins = Math.max(0, Math.round((new Date(expiresStr).getTime() - Date.now()) / 60000))
                      return `${mins} minutes remaining`
                    })()}
                  </p>

                  <div className="bg-white rounded-2xl border-2 border-blue-500 p-6 mb-6">
                    <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Your EV Code</p>
                    <p className="text-4xl font-mono font-bold text-blue-600 tracking-wider">
                      {activeEVSession.code}
                    </p>
                  </div>

                  <p className="text-sm text-gray-500">
                    Show this code to the merchant to redeem your reward
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Login Modal (triggered by auth-required actions) */}
      <LoginModal
        isOpen={showLoginModal}
        onClose={() => setShowLoginModal(false)}
        onSuccess={() => {
          setShowLoginModal(false)
          setIsAuthenticated(true)
        }}
      />

      {/* Analytics Debug Panel (dev only) */}
      <AnalyticsDebugPanel />
    </>
  )
}
