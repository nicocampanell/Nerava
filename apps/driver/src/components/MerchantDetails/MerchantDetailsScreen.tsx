import { useState, useEffect, useCallback } from 'react'
import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useMerchantDetails, useActivateExclusive, useVerifyVisit, useCompleteExclusive, useVoteAmenity, useRequestToJoin, useClaimReward, useUploadReceipt, useLoyaltyProgress, claimLoyaltyReward, ApiError } from '../../services/api'
import { FEATURE_FLAGS } from '../../config/featureFlags'
import { RefuelIntentModal, type RefuelDetails } from '../RefuelIntentModal'
import { SpotSecuredModal } from '../SpotSecuredModal'
import { generateReservationId } from '../../utils/reservationId'
import { HeroImageHeader } from './HeroImageHeader'
import { DistanceCard } from './DistanceCard'
import { HoursCard } from './HoursCard'
import { ExclusiveOfferCard } from './ExclusiveOfferCard'
import { RequestToJoinSheet } from './RequestToJoinSheet'
import { ClaimRewardSheet } from './ClaimRewardSheet'
import { ReceiptUploadModal } from './ReceiptUploadModal'
import { ReceiptResultModal } from './ReceiptResultModal'
import { SocialProofBadge } from '../shared/SocialProofBadge'
import { AmenityVotes } from '../shared/AmenityVotes'
import { PreferencesModal } from '../Preferences/PreferencesModal'
import { ActivateExclusiveModal } from '../ActivateExclusiveModal/ActivateExclusiveModal'
import { ExclusiveActivatedModal } from '../ExclusiveActivated/ExclusiveActivatedModal'
import { VerificationCodeModal } from '../VerificationCode/VerificationCodeModal'
import { ExclusiveCompletedModal } from '../ExclusiveCompleted/ExclusiveCompletedModal'
import { Button } from '../shared/Button'
import { InlineError } from '../shared/InlineError'
import { MerchantDetailsSkeleton } from '../shared/Skeleton'
import { ThumbsUp, ThumbsDown, MapPin, Phone, Globe, Store } from 'lucide-react'
import { openExternalUrl } from '../../utils/openExternal'
import { useFavorites } from '../../contexts/FavoritesContext'
import { capture, DRIVER_EVENTS } from '../../analytics'
import type { ReceiptUploadResponse } from '../../types'

// Flow states
type FlowState =
  | 'idle'              // Initial state, no exclusive active
  | 'activated'         // Just activated, showing ExclusiveActivatedModal
  | 'walking'           // User started walking, showing active state view
  | 'at_merchant'       // User clicked "I'm at the Merchant", showing VerificationCodeModal
  | 'preferences'       // Showing preferences modal
  | 'completed'         // Showing completion modal

export function MerchantDetailsScreen() {
  const { merchantId } = useParams<{ merchantId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const sessionId = searchParams.get('session_id') || undefined
  const chargerId = searchParams.get('charger_id') || 'canyon_ridge_tesla'
  const photoFromNav = searchParams.get('photo') || undefined

  const { data: merchantData, isLoading, error } = useMerchantDetails(merchantId || null, sessionId)
  const activateExclusive = useActivateExclusive()
  const verifyVisit = useVerifyVisit()
  const completeExclusive = useCompleteExclusive()
  const voteAmenityMutation = useVoteAmenity()
  const requestToJoin = useRequestToJoin()
  const claimRewardMutation = useClaimReward()
  const uploadReceiptMutation = useUploadReceipt()

  // Loyalty progress
  const merchantIdForLoyalty = merchantData?.merchant?.id || merchantId || null
  const { data: loyaltyProgress, refetch: refetchLoyalty } = useLoyaltyProgress(merchantIdForLoyalty)

  // Merchant rewards state
  const [showRequestSheet, setShowRequestSheet] = useState(false)
  const [showClaimSheet, setShowClaimSheet] = useState(false)
  const [showReceiptUpload, setShowReceiptUpload] = useState(false)
  const [showReceiptResult, setShowReceiptResult] = useState(false)
  const [receiptResult, setReceiptResult] = useState<ReceiptUploadResponse | null>(null)
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null)
  const [activeClaimRemaining, setActiveClaimRemaining] = useState(0)

  // V3: Validate merchant data has required fields
  useEffect(() => {
    if (merchantData && !merchantData.merchant.place_id) {
      console.warn('[V3] Merchant missing place_id, sending merchant_place_id=null')
    }
  }, [merchantData])

  // Flow state management
  const [flowState, setFlowState] = useState<FlowState>('idle')
  const [showActivateModal, setShowActivateModal] = useState(false)
  const [exclusiveSessionId, setExclusiveSessionId] = useState<string | null>(null)
  const [remainingSeconds, setRemainingSeconds] = useState(3600) // 60 minutes default
  const [verificationCode, setVerificationCode] = useState<string | null>(null)
  const [isAuthenticated, setIsAuthenticated] = useState(() => !!localStorage.getItem('access_token'))
  const [showShareToast, setShowShareToast] = useState(false)

  // Favorites context
  const { toggleFavorite, isFavorite } = useFavorites()

  const handleFavorite = useCallback(async () => {
    if (!merchantId) return
    capture(DRIVER_EVENTS.MERCHANT_FAVORITED, {
      merchant_id: merchantId,
      action: isFavorite(merchantId) ? 'unfavorite' : 'favorite',
    })
    await toggleFavorite(merchantId, merchantData?.merchant?.name)
  }, [merchantId, toggleFavorite, isFavorite, merchantData])

  const handleShare = useCallback(async () => {
    if (!merchantId || !merchantData) return
    const url = `https://app.nerava.network/merchant/${merchantId}`
    const shareData = {
      title: merchantData.merchant.name,
      text: `Check out ${merchantData.merchant.name} on Nerava!`,
      url,
    }

    if (navigator.share && navigator.canShare?.(shareData)) {
      try {
        await navigator.share(shareData)
        capture(DRIVER_EVENTS.MERCHANT_SHARED, {
          merchant_id: merchantId,
          method: 'native',
        })
        return
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
      }
    }

    try {
      await navigator.clipboard.writeText(url)
      capture(DRIVER_EVENTS.MERCHANT_SHARED, {
        merchant_id: merchantId,
        method: 'clipboard',
      })
      setShowShareToast(true)
      setTimeout(() => setShowShareToast(false), 2000)
    } catch (err) {
      console.error('Failed to copy to clipboard:', err)
    }
  }, [merchantId, merchantData])

  // V3: Intent capture state (only used when SECURE_A_SPOT_V3 is enabled)
  const [showRefuelIntentModal, setShowRefuelIntentModal] = useState(false)
  const [showSpotSecuredModal, setShowSpotSecuredModal] = useState(false)
  const [refuelDetails, setRefuelDetails] = useState<RefuelDetails | null>(null)
  const [reservationId, setReservationId] = useState<string | null>(null)
  const [showCompactIntentSummary, setShowCompactIntentSummary] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)

  // Amenity voting state
  const [userAmenityVotes, setUserAmenityVotes] = useState<{
    bathroom: 'up' | 'down' | null
    wifi: 'up' | 'down' | null
  }>({ bathroom: null, wifi: null })
  const [localAmenityCounts, setLocalAmenityCounts] = useState<{
    bathroom: { upvotes: number; downvotes: number }
    wifi: { upvotes: number; downvotes: number }
  } | null>(null)
  const [showAmenityVoteModal, setShowAmenityVoteModal] = useState(false)
  const [selectedAmenity, setSelectedAmenity] = useState<'bathroom' | 'wifi' | null>(null)

  // Check for previous intent in localStorage for progressive disclosure
  useEffect(() => {
    if (FEATURE_FLAGS.LIVE_COORDINATION_UI_V1 && FEATURE_FLAGS.SECURE_A_SPOT_V3) {
      try {
        const stored = localStorage.getItem('nerava_last_intent')
        if (stored) {
          const previousIntent: RefuelDetails = JSON.parse(stored)
          setRefuelDetails(previousIntent)
          setShowCompactIntentSummary(true)
        }
      } catch {
        // Invalid JSON, ignore
      }
    }
  }, [])

  // Load amenity votes from localStorage and initialize counts
  useEffect(() => {
    if (!merchantId) return

    // Load user votes from localStorage
    try {
      const storedVotes = localStorage.getItem(`nerava_amenity_votes_${merchantId}`)
      if (storedVotes) {
        setUserAmenityVotes(JSON.parse(storedVotes))
      }
    } catch {
      // Invalid JSON, ignore
    }

    // Initialize amenity counts (default to 0 if not provided by API)
    // Always show amenities (even with 0 votes) so users can vote
    const defaultAmenities = {
      bathroom: { upvotes: 0, downvotes: 0 },
      wifi: { upvotes: 0, downvotes: 0 },
    }
    const apiAmenities = merchantData?.merchant.amenities || defaultAmenities
    setLocalAmenityCounts(apiAmenities)
  }, [merchantId, merchantData])

  // Calculate remaining minutes for display
  const remainingMinutes = Math.ceil(remainingSeconds / 60)

  // Countdown timer when exclusive is active
  useEffect(() => {
    if (flowState !== 'idle' && flowState !== 'completed' && remainingSeconds > 0) {
      const interval = setInterval(() => {
        setRemainingSeconds(prev => Math.max(0, prev - 1))
      }, 1000)
      return () => clearInterval(interval)
    }
  }, [flowState, remainingSeconds])

  const handleActivateExclusive = useCallback(async () => {
    if (!merchantId) {
      setInlineError('Missing merchant ID. Please go back and try again.')
      return
    }

    // Get user location for exclusive activation
    let lat: number | undefined
    let lng: number | undefined
    let accuracy_m: number | undefined

    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 5000,
          maximumAge: 60000
        })
      })
      lat = position.coords.latitude
      lng = position.coords.longitude
      accuracy_m = position.coords.accuracy || undefined
      console.log('[Demo] Activating with location:', lat, lng)
    } catch (err) {
      console.log('[Demo] Location unavailable, activating without location:', err)
    }

    try {
      const idempotencyKey = crypto.randomUUID()
      const response = await activateExclusive.mutateAsync({
        request: {
          merchant_id: merchantId,
          merchant_place_id: merchantId,
          charger_id: chargerId,
          lat: lat ?? null,  // V3: null when unavailable, never 0
          lng: lng ?? null,  // V3: null when unavailable, never 0
          accuracy_m,
        },
        idempotencyKey,
      })

      setExclusiveSessionId(response.exclusive_session.id)
      setRemainingSeconds(response.exclusive_session.remaining_seconds)
      setShowActivateModal(false)
      setFlowState('activated') // Show the ExclusiveActivatedModal
    } catch (err) {
      console.error('Failed to activate exclusive:', err)
      setInlineError('Failed to activate exclusive. Please try again.')
    }
  }, [merchantId, chargerId, activateExclusive])

  const handleStartWalking = () => {
    setFlowState('walking')
  }

  const handleViewDetails = () => {
    setFlowState('walking')
  }

  const handleImAtMerchant = async () => {
    if (!exclusiveSessionId) {
      setInlineError('No active exclusive session. Please activate first.')
      return
    }

    try {
      // Get current location for verification
      let lat: number | undefined
      let lng: number | undefined
      try {
        const position = await new Promise<GeolocationPosition>((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(resolve, reject, {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 0
          })
        })
        lat = position.coords.latitude
        lng = position.coords.longitude
      } catch {
        // Location failed, proceed without it
        console.log('Could not get location for verification')
      }

      // Call verify endpoint to get verification code
      const response = await verifyVisit.mutateAsync({
        exclusive_session_id: exclusiveSessionId,
        lat,
        lng,
      })

      setVerificationCode(response.verification_code)
      setFlowState('at_merchant')
    } catch (err) {
      setInlineError('Failed to verify visit: ' + (err instanceof Error ? err.message : 'Unknown error'))
    }
  }

  const handleVerificationDone = () => {
    // Show preferences modal only once per session
    const hasSeenPreferences = sessionStorage.getItem('preferences_modal_shown')
    if (!hasSeenPreferences) {
      setFlowState('preferences')
      sessionStorage.setItem('preferences_modal_shown', 'true')
    } else {
      setFlowState('completed')
    }
  }

  const handlePreferencesClose = () => {
    setFlowState('completed')
  }

  const handleCompletedContinue = async (feedback?: { thumbsUp: boolean }) => {
    // Complete the exclusive session with feedback
    if (exclusiveSessionId) {
      try {
        await completeExclusive.mutateAsync({
          exclusive_session_id: exclusiveSessionId,
          feedback: feedback ? { thumbs_up: feedback.thumbsUp } : undefined,
        })
      } catch (err) {
        console.error('Failed to complete exclusive:', err)
      }
    }

    // Navigate back to main view
    navigate('/wyc')
  }

  const handleAddToSessions = async () => {
    // Get location for session context
    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 60000
        })
      })
      console.log('Got location:', position.coords.latitude, position.coords.longitude)
    } catch (err) {
      console.warn('Location not available:', err)
    }

    // Proceed to authentication check
    if (!isAuthenticated) {
      setShowActivateModal(true)
    } else {
      await handleActivateExclusive()
    }
  }

  // ============================================
  // V3: "Secure a Spot" flow handlers
  // Only active when FEATURE_FLAGS.SECURE_A_SPOT_V3 is true
  // ============================================

  const handleSecureSpot = () => {
    // If we have a previous intent and compact summary is showing, use it directly
    if (FEATURE_FLAGS.LIVE_COORDINATION_UI_V1 && showCompactIntentSummary && refuelDetails) {
      // Use existing refuelDetails, proceed to authentication
      if (!isAuthenticated) {
        setShowActivateModal(true)
      } else {
        handleActivateWithIntent(refuelDetails)
      }
    } else {
      // Show intent capture modal first
      setShowRefuelIntentModal(true)
    }
  }

  const handleIntentConfirm = (details: RefuelDetails) => {
    setRefuelDetails(details)
    setShowRefuelIntentModal(false)
    setShowCompactIntentSummary(false) // Hide compact summary when new intent is confirmed

    // Proceed to authentication if needed
    if (!isAuthenticated) {
      setShowActivateModal(true)
    } else {
      handleActivateWithIntent(details)
    }
  }

  const handleActivateWithIntent = async (details: RefuelDetails) => {
    if (!merchantId || !merchantData) {
      setInlineError('Missing merchant data. Please go back and try again.')
      return
    }

    // Get location (OPTIONAL for V3)
    let lat: number | null = null
    let lng: number | null = null
    let accuracy_m: number | undefined

    try {
      const position = await new Promise<GeolocationPosition>((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 5000,
          maximumAge: 60000
        })
      })
      lat = position.coords.latitude
      lng = position.coords.longitude
      accuracy_m = position.coords.accuracy || undefined
      console.log('[V3] Location acquired:', { lat, lng, accuracy_m })
    } catch (err) {
      // V3: Location is optional, proceed without it
      console.log('[V3] Location unavailable, proceeding with null:', err)
    }

    try {
      const idempotencyKey = crypto.randomUUID()
      const response = await activateExclusive.mutateAsync({
        request: {
          merchant_id: merchantId,
          // CORRECT: Use actual place_id from merchant data, not merchantId
          merchant_place_id: merchantData.merchant.place_id ?? null,
          charger_id: chargerId,
          lat,  // V3: Can be null
          lng,  // V3: Can be null
          accuracy_m,
          // V3: Intent capture fields
          intent: details.intent,
          party_size: details.partySize,
          needs_power_outlet: details.needsPowerOutlet,
          is_to_go: details.isToGo,
        },
        idempotencyKey,
      })

      const sessionId = response.exclusive_session.id
      setExclusiveSessionId(sessionId)
      setRemainingSeconds(response.exclusive_session.remaining_seconds)

      // Generate Reservation ID (V3: client-side only, informational)
      // IMPORTANT: Persist to localStorage keyed by session ID to survive remounts
      const storageKey = `reservation_id_${sessionId}`
      let id = localStorage.getItem(storageKey)
      if (!id) {
        id = generateReservationId(merchantData.merchant.name)
        localStorage.setItem(storageKey, id)
      }
      setReservationId(id)

      setShowActivateModal(false)
      setShowSpotSecuredModal(true)
    } catch (err) {
      console.error('[V3] Failed to secure spot:', err)

      // Clear intent state on error so user can retry
      setRefuelDetails(null)

      if (err instanceof ApiError) {
        if (err.status === 400) {
          setInlineError('Invalid request. Please check your selections and try again.')
        } else if (err.status === 401) {
          setInlineError('Authentication required. Please sign in again.')
          setIsAuthenticated(false)
          setShowActivateModal(true)
        } else if (err.status >= 500) {
          setInlineError('Server error. Please try again in a moment.')
        } else {
          setInlineError('Failed to secure spot. Please try again.')
        }
      } else {
        setInlineError('Network error. Please check your connection and try again.')
      }
    }
  }

  const handleSpotSecuredContinue = () => {
    // Close modal and transition to walking state
    setShowSpotSecuredModal(false)
    setFlowState('walking')
    // V4 TODO: Open sessions modal or navigate to sessions route
  }

  // V3: Cleanup reservation ID from localStorage when session expires or completes
  useEffect(() => {
    if (remainingSeconds !== null && remainingSeconds <= 0) {
      if (exclusiveSessionId) {
        const storageKey = `reservation_id_${exclusiveSessionId}`
        localStorage.removeItem(storageKey)
      }
    }
  }, [remainingSeconds, exclusiveSessionId])

  // Cleanup when transitioning out of session state
  useEffect(() => {
    if (flowState === 'idle' && exclusiveSessionId) {
      const storageKey = `reservation_id_${exclusiveSessionId}`
      localStorage.removeItem(storageKey)
    }
  }, [flowState, exclusiveSessionId])

  const handleGetDirections = () => {
    if (merchantData?.actions.get_directions_url) {
      openExternalUrl(merchantData.actions.get_directions_url)
    }
  }

  // Amenity voting handler
  const handleAmenityVote = async (amenity: 'bathroom' | 'wifi', voteType: 'up' | 'down') => {
    if (!merchantId || !localAmenityCounts) return

    const previousVote = userAmenityVotes[amenity]
    // Toggle vote if same type clicked, otherwise change vote
    const newVote = previousVote === voteType ? null : voteType

    // Optimistic update: update UI immediately
    const updatedVotes = {
      ...userAmenityVotes,
      [amenity]: newVote,
    }
    setUserAmenityVotes(updatedVotes)

    // Update local counts optimistically
    const newCounts = { ...localAmenityCounts }
    const amenityData = { ...newCounts[amenity] }

    // Remove previous vote if exists
    if (previousVote === 'up') {
      amenityData.upvotes = Math.max(0, amenityData.upvotes - 1)
    } else if (previousVote === 'down') {
      amenityData.downvotes = Math.max(0, amenityData.downvotes - 1)
    }

    // Add new vote if not removing
    if (newVote === 'up') {
      amenityData.upvotes += 1
    } else if (newVote === 'down') {
      amenityData.downvotes += 1
    }

    newCounts[amenity] = amenityData
    setLocalAmenityCounts(newCounts)

    // Check feature flag for API usage
    const useApi = import.meta.env.VITE_USE_AMENITY_VOTES_API === 'true'

    if (useApi && merchantId) {
      try {
        // Call API
        const response = await voteAmenityMutation.mutateAsync({
          merchantId,
          amenity,
          voteType,
        })

        // Update counts from API response
        setLocalAmenityCounts({
          ...newCounts,
          [amenity]: {
            upvotes: response.upvotes,
            downvotes: response.downvotes,
          },
        })
      } catch (error) {
        // API failed: rollback optimistic update and use localStorage fallback
        console.warn('[AmenityVote] API call failed, falling back to localStorage:', error)

        // Rollback optimistic update
        setUserAmenityVotes(userAmenityVotes)
        setLocalAmenityCounts(localAmenityCounts)
      }
    } else {
      // Feature flag disabled: use localStorage only
      localStorage.setItem(`nerava_amenity_votes_${merchantId}`, JSON.stringify(updatedVotes))
    }

    // Close modal
    setShowAmenityVoteModal(false)
  }

  if (isLoading) {
    return <MerchantDetailsSkeleton />
  }

  if (error || !merchantData) {
    return (
      <div className="flex items-center justify-center bg-gray-50 p-4" style={{ height: 'var(--app-height, 100dvh)' }}>
        <div className="text-center">
          <p className="text-gray-900 font-medium mb-2">Merchant not found</p>
          <p className="text-gray-600 text-sm">{error?.message || 'Unknown error'}</p>
        </div>
      </div>
    )
  }

  const photoUrls = (merchantData.merchant as any).photo_urls
  const walkTime = merchantData.moment.label
  const isExclusive = merchantData.perk?.badge === 'Exclusive'
  const isActiveState = flowState === 'walking'

  return (
    <div className="bg-white overflow-y-auto" style={{ height: 'var(--app-height, 100dvh)' }}>
      {/* Hero image */}
      <HeroImageHeader
        photoUrls={photoUrls}
        photoUrl={merchantData.merchant.photo_url || photoFromNav}
        merchantName={merchantData.merchant.name}
        category={merchantData.merchant.category}
        walkTime={isActiveState ? `${remainingMinutes} minutes remaining` : walkTime}
        isExclusive={isExclusive}
        isExclusiveActive={isActiveState}
        isFavorited={merchantId ? isFavorite(merchantId) : false}
        onClose={() => navigate(-1)}
        onFavorite={handleFavorite}
        onShare={handleShare}
      />

      {/* Content */}
      <div className="px-4 py-6 space-y-5">
        {/* Merchant name and category */}
        <div>
          <h1 className="text-3xl font-bold text-gray-900 leading-tight mb-2">{merchantData.merchant.name}</h1>
          <p className="text-base text-gray-600">{merchantData.merchant.category}</p>

          {/* Contact info: address, phone, website */}
          <div className="mt-3 space-y-2">
            {merchantData.merchant.address && (
              <div className="flex items-start gap-2 text-sm text-gray-600">
                <MapPin className="w-4 h-4 mt-0.5 flex-shrink-0 text-[#65676B]" />
                <span>{merchantData.merchant.address}</span>
              </div>
            )}
            {(merchantData.merchant as any).phone && (
              <div className="flex items-center gap-2 text-sm">
                <Phone className="w-4 h-4 flex-shrink-0 text-[#65676B]" />
                <a
                  href={`tel:${(merchantData.merchant as any).phone}`}
                  className="text-[#1877F2] hover:underline"
                >
                  {(merchantData.merchant as any).phone}
                </a>
              </div>
            )}
            {(merchantData.merchant as any).website && (
              <div className="flex items-center gap-2 text-sm">
                <Globe className="w-4 h-4 flex-shrink-0 text-[#65676B]" />
                <button
                  onClick={() => openExternalUrl((merchantData.merchant as any).website)}
                  className="text-[#1877F2] hover:underline truncate text-left"
                >
                  {(() => {
                    try {
                      return new URL((merchantData.merchant as any).website).hostname.replace('www.', '')
                    } catch {
                      return (merchantData.merchant as any).website
                    }
                  })()}
                </button>
              </div>
            )}
          </div>

          {/* Claim listing link */}
          <div className="mt-3">
            <button
              onClick={() => {
                const merchantId = merchantData.merchant.id
                const driverPublicId = localStorage.getItem('public_id') || ''
                const claimUrl = `https://merchant.nerava.network/claim/${merchantId}${driverPublicId ? `?ref=${driverPublicId}` : ''}`
                openExternalUrl(claimUrl)
                capture(DRIVER_EVENTS.MERCHANT_CLICKED, { action: 'claim_listing', merchant_id: merchantId })
              }}
              className="flex items-center gap-1.5 text-xs text-[#65676B] hover:text-[#1877F2] transition-colors"
            >
              <Store className="w-3.5 h-3.5" />
              <span>Own this business? <span className="text-[#1877F2] underline">Claim your listing</span></span>
            </button>
          </div>

          {/* Social Proof Badge and Amenity Votes */}
          <div className="mt-3 flex items-start justify-between gap-3">
            <SocialProofBadge
              neravaSessionsCount={(merchantData.merchant as any).neravaSessionsCount}
              activeDriversCount={(merchantData.merchant as any).activeDriversCount}
            />
            <AmenityVotes
              bathroom={localAmenityCounts?.bathroom || { upvotes: 0, downvotes: 0 }}
              wifi={localAmenityCounts?.wifi || { upvotes: 0, downvotes: 0 }}
              interactive={false}
              userVotes={userAmenityVotes}
              onAmenityClick={(amenity) => {
                setSelectedAmenity(amenity)
                setShowAmenityVoteModal(true)
              }}
            />
          </div>
        </div>

        {/* Walk instruction when active */}
        {isActiveState && (
          <div className="bg-gray-50 rounded-xl p-4 text-center">
            <p className="text-gray-700">
              Walk to {merchantData.merchant.name} and show this screen
            </p>
          </div>
        )}

        {/* Exclusive Offer Card */}
        {merchantData.perk && (
          <ExclusiveOfferCard
            title={merchantData.perk.title}
            description={merchantData.perk.description}
          />
        )}

        {/* Loyalty Punch Cards */}
        {loyaltyProgress && loyaltyProgress.length > 0 && (
          <div className="space-y-3">
            {loyaltyProgress.map((card) => (
              <div key={card.card_id} className="bg-white rounded-2xl p-4 border border-gray-100">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-gray-900">{card.program_name}</span>
                  <span className="text-xs text-gray-500">
                    {card.visit_count}/{card.visits_required} visits
                  </span>
                </div>
                <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-2">
                  <div
                    className="h-full bg-green-500 rounded-full transition-all"
                    style={{ width: `${Math.min(100, (card.visit_count / card.visits_required) * 100)}%` }}
                  />
                </div>
                {card.reward_unlocked && !card.reward_claimed && (
                  <button
                    className="w-full mt-1 py-2 bg-green-600 text-white text-sm font-medium rounded-xl"
                    onClick={async () => {
                      try {
                        await claimLoyaltyReward(card.card_id)
                        refetchLoyalty()
                      } catch {}
                    }}
                  >
                    Claim Reward — {card.reward_description || `$${(card.reward_cents / 100).toFixed(2)} off`}
                  </button>
                )}
                {card.reward_claimed && (
                  <p className="text-xs text-green-600 font-medium">Reward claimed</p>
                )}
                {!card.reward_unlocked && card.reward_description && (
                  <p className="text-xs text-gray-500">{card.visits_required - card.visit_count} more visits for: {card.reward_description}</p>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Distance card */}
        <DistanceCard
          distanceMiles={merchantData.moment.distance_miles}
          walkTimeLabel={walkTime}
          momentCopy={merchantData.moment.moment_copy}
        />

        {/* Hours card */}
        <HoursCard hoursText={merchantData.merchant.hours_today ?? undefined} />

        {/* Description */}
        {merchantData.merchant.description && (
          <div className="text-sm text-gray-700 leading-relaxed">
            {merchantData.merchant.description}
          </div>
        )}

        {/* Get Directions button */}
        <Button
          variant="secondary"
          className="w-full"
          onClick={handleGetDirections}
        >
          Get Directions
        </Button>

        {/* Merchant Reward CTA — three states based on reward_state */}
        {flowState === 'idle' && merchantData.reward_state && (() => {
          const rs = merchantData.reward_state
          const hasExclusive = !!merchantData.perk

          // State 1: Active reward with claim already made — show upload receipt CTA
          if (rs.active_claim_id && rs.active_claim_status === 'claimed') {
            return (
              <Button
                variant="primary"
                className="w-full"
                onClick={() => {
                  setActiveClaimId(rs.active_claim_id!)
                  setActiveClaimRemaining(
                    rs.active_claim_expires_at
                      ? Math.max(0, Math.floor((new Date(rs.active_claim_expires_at + 'Z').getTime() - Date.now()) / 1000))
                      : 7200
                  )
                  setShowReceiptUpload(true)
                }}
              >
                Upload Receipt
              </Button>
            )
          }

          // State 2: Active reward with receipt uploaded — show status
          if (rs.active_claim_id && (rs.active_claim_status === 'receipt_uploaded' || rs.active_claim_status === 'approved')) {
            return (
              <div className="bg-green-50 rounded-xl px-4 py-3 text-center text-sm text-green-700 font-medium">
                {rs.active_claim_status === 'approved' ? 'Receipt verified! Reward earned.' : 'Receipt under review...'}
              </div>
            )
          }

          // State 3: Has active reward, not yet claimed — show claim CTA
          if (rs.has_active_reward && !rs.active_claim_id && hasExclusive) {
            return (
              <Button
                variant="primary"
                className="w-full"
                onClick={() => setShowClaimSheet(true)}
              >
                Claim Reward
              </Button>
            )
          }

          // State 4: No active reward, non-partner merchant — show Request to Join CTA
          if (!hasExclusive && !rs.user_has_requested) {
            return (
              <Button
                variant="primary"
                className="w-full bg-[#050505] hover:bg-[#1a1a1a]"
                onClick={() => {
                  if (!localStorage.getItem('access_token')) {
                    setShowActivateModal(true)
                  } else {
                    setShowRequestSheet(true)
                  }
                }}
              >
                Request to Join Nerava
              </Button>
            )
          }

          // State 5: Already requested — show confirmation
          if (!hasExclusive && rs.user_has_requested) {
            return (
              <div className="bg-[#F0F2F5] rounded-xl px-4 py-3 text-center text-sm text-[#65676B]">
                You've requested this merchant{rs.join_request_count > 1 ? ` along with ${rs.join_request_count - 1} other driver${rs.join_request_count > 2 ? 's' : ''}` : ''}.
                We'll notify you when they join!
              </div>
            )
          }

          return null
        })()}

        {/* Compact Intent Summary - Progressive disclosure when previous intent exists */}
        {FEATURE_FLAGS.LIVE_COORDINATION_UI_V1 && FEATURE_FLAGS.SECURE_A_SPOT_V3 && showCompactIntentSummary && refuelDetails && flowState === 'idle' && merchantData.wallet.can_add && (
          <div className="bg-[#F7F8FA] rounded-2xl p-4 mb-3 border border-[#E4E6EB]">
            <div className="flex items-center justify-between">
              <div className="flex-1">
                <p className="text-xs text-[#65676B] mb-1">Your intent</p>
                <p className="text-sm font-medium text-[#050505]">
                  {refuelDetails.intent === 'eat'
                    ? `Dining, Party of ${refuelDetails.partySize || 2}`
                    : refuelDetails.intent === 'work'
                    ? `Work Session${refuelDetails.needsPowerOutlet ? ' + Power' : ''}`
                    : `Quick Stop${refuelDetails.isToGo ? ' (To-Go)' : ''}`
                  }
                </p>
              </div>
              <button
                onClick={() => {
                  setShowCompactIntentSummary(false)
                  setShowRefuelIntentModal(true)
                }}
                className="text-sm text-[#1877F2] font-medium hover:underline ml-4"
              >
                Change
              </button>
            </div>
          </div>
        )}

        {/* Inline error — replaces browser alert() */}
        <InlineError
          message={inlineError}
          onDismiss={() => setInlineError(null)}
        />

        {/* Main action button based on state — reward CTAs take priority over old exclusive flow */}
        {flowState === 'idle' && (() => {
          const rs = merchantData.reward_state
          const hasExclusive = !!merchantData.perk

          // Priority 1: Active claim needing receipt upload
          if (rs?.active_claim_id && rs.active_claim_status === 'claimed') {
            return null // Already rendered above in reward CTA section
          }

          // Priority 2: Active claim with receipt uploaded/approved
          if (rs?.active_claim_id && (rs.active_claim_status === 'receipt_uploaded' || rs.active_claim_status === 'approved')) {
            return null // Already rendered above
          }

          // Priority 3: Has active reward, not yet claimed — already rendered above
          if (rs?.has_active_reward && !rs?.active_claim_id && hasExclusive) {
            return null // Already rendered above
          }

          // Priority 4: Non-partner merchant — already rendered above
          if (rs && !hasExclusive && !rs.user_has_requested) {
            return null // Already rendered above
          }

          // Priority 5: Already requested — already rendered above
          if (rs && !hasExclusive && rs.user_has_requested) {
            return null // Already rendered above
          }

          // Fallback: old exclusive flow (only if no reward state or wallet.can_add)
          if (merchantData.wallet.can_add) {
            return (
              <Button
                variant="primary"
                className="w-full"
                onClick={FEATURE_FLAGS.SECURE_A_SPOT_V3 ? handleSecureSpot : handleAddToSessions}
                disabled={activateExclusive.isPending}
              >
                {activateExclusive.isPending
                  ? (FEATURE_FLAGS.SECURE_A_SPOT_V3 ? 'Securing...' : 'Activating...')
                  : (FEATURE_FLAGS.SECURE_A_SPOT_V3 ? 'Secure a Spot' : 'Activate Exclusive')
                }
              </Button>
            )
          }

          return null
        })()}

        {isActiveState && (
          <Button
            variant="primary"
            className="w-full"
            onClick={handleImAtMerchant}
            disabled={verifyVisit.isPending}
          >
            {verifyVisit.isPending ? 'Verifying...' : "I'm at the Merchant"}
          </Button>
        )}
      </div>

      {/* Modals based on flow state */}

      {/* OTP Activate Modal */}
      <ActivateExclusiveModal
        isOpen={showActivateModal}
        onClose={() => setShowActivateModal(false)}
        onSuccess={async () => {
          setIsAuthenticated(true)
          setShowActivateModal(false)
          // V3: Use intent flow if flag enabled and intent was captured
          if (FEATURE_FLAGS.SECURE_A_SPOT_V3 && refuelDetails) {
            await handleActivateWithIntent(refuelDetails)
          } else {
            await handleActivateExclusive()
          }
        }}
      />

      {/* Exclusive Activated Modal */}
      {flowState === 'activated' && merchantData && (
        <ExclusiveActivatedModal
          merchantName={merchantData.merchant.name}
          perkTitle={merchantData.perk?.title ?? 'Exclusive Offer'}
          remainingMinutes={remainingMinutes}
          onStartWalking={handleStartWalking}
          onViewDetails={handleViewDetails}
        />
      )}

      {/* Verification Code Modal */}
      {flowState === 'at_merchant' && verificationCode && merchantData && (
        <VerificationCodeModal
          merchantName={merchantData.merchant.name}
          verificationCode={verificationCode}
          onDone={handleVerificationDone}
        />
      )}

      {/* Preferences Modal */}
      <PreferencesModal
        isOpen={flowState === 'preferences'}
        onClose={handlePreferencesClose}
      />

      {/* Exclusive Completed Modal */}
      {flowState === 'completed' && (
        <ExclusiveCompletedModal
          onContinue={handleCompletedContinue}
        />
      )}

      {/* V3: Refuel Intent Modal (only when feature flag enabled) */}
      {FEATURE_FLAGS.SECURE_A_SPOT_V3 && (
        <RefuelIntentModal
          merchantName={merchantData?.merchant.name || ''}
          isOpen={showRefuelIntentModal}
          onClose={() => setShowRefuelIntentModal(false)}
          onConfirm={handleIntentConfirm}
        />
      )}

      {/* V3: Spot Secured Modal (only when feature flag enabled) */}
      {FEATURE_FLAGS.SECURE_A_SPOT_V3 && refuelDetails && reservationId && merchantData && (
        <SpotSecuredModal
          merchantName={merchantData.merchant.name}
          merchantBadge={merchantData.perk?.badge}
          refuelDetails={refuelDetails}
          remainingMinutes={remainingMinutes}
          reservationId={reservationId}
          isOpen={showSpotSecuredModal}
          onContinue={handleSpotSecuredContinue}
        />
      )}

      {/* Amenity Vote Modal */}
      {showAmenityVoteModal && selectedAmenity && localAmenityCounts && merchantData && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[3000] p-4">
          <div className="bg-white rounded-3xl p-8 max-w-sm w-full shadow-2xl">
            {/* Title */}
            <h2 className="text-xl text-center mb-4">
              Rate {selectedAmenity === 'bathroom' ? 'Bathroom' : 'WiFi'}
            </h2>

            {/* Description */}
            <p className="text-center text-[#65676B] mb-6">
              How was the {selectedAmenity === 'bathroom' ? 'bathroom' : 'WiFi'} at {merchantData.merchant.name}?
            </p>

            {/* Vote Buttons */}
            <div className="flex gap-3 mb-6">
              <button
                onClick={() => handleAmenityVote(selectedAmenity, 'up')}
                className={`flex-1 py-4 rounded-2xl font-medium transition-all flex items-center justify-center gap-2 ${
                  userAmenityVotes[selectedAmenity] === 'up'
                    ? 'bg-green-100 text-green-700 border-2 border-green-500'
                    : 'bg-[#F7F8FA] text-[#050505] border-2 border-[#E4E6EB] hover:border-green-500'
                }`}
                aria-label="Vote good"
              >
                <ThumbsUp className="w-5 h-5" />
                Good
              </button>
              <button
                onClick={() => handleAmenityVote(selectedAmenity, 'down')}
                className={`flex-1 py-4 rounded-2xl font-medium transition-all flex items-center justify-center gap-2 ${
                  userAmenityVotes[selectedAmenity] === 'down'
                    ? 'bg-red-100 text-red-700 border-2 border-red-500'
                    : 'bg-[#F7F8FA] text-[#050505] border-2 border-[#E4E6EB] hover:border-red-500'
                }`}
                aria-label="Vote bad"
              >
                <ThumbsDown className="w-5 h-5" />
                Bad
              </button>
            </div>

            {/* Cancel Button */}
            <button
              onClick={() => setShowAmenityVoteModal(false)}
              className="w-full py-3 bg-white border border-[#E4E6EB] text-[#65676B] rounded-2xl font-medium hover:bg-[#F7F8FA] active:scale-98 transition-all"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Share Toast */}
      {showShareToast && (
        <div className="fixed bottom-24 left-1/2 -translate-x-1/2 bg-[#050505] text-white px-4 py-2 rounded-full flex items-center gap-2 shadow-lg z-[3000]">
          <span className="text-sm font-medium">Link copied!</span>
        </div>
      )}

      {/* Request to Join Sheet */}
      {merchantData && (
        <RequestToJoinSheet
          isOpen={showRequestSheet}
          merchantName={merchantData.merchant.name}
          requestCount={merchantData.reward_state?.join_request_count ?? 0}
          onClose={() => setShowRequestSheet(false)}
          onSubmit={async (tags) => {
            const placeId = merchantData.merchant.place_id || merchantId || ''
            await requestToJoin.mutateAsync({
              placeId,
              merchantName: merchantData.merchant.name,
              interestTags: tags,
            })
          }}
        />
      )}

      {/* Claim Reward Sheet */}
      {merchantData && (
        <ClaimRewardSheet
          isOpen={showClaimSheet}
          merchantName={merchantData.merchant.name}
          rewardDescription={merchantData.perk?.title || 'EV Driver Reward'}
          onClose={() => setShowClaimSheet(false)}
          onClaim={async () => {
            const resp = await claimRewardMutation.mutateAsync({
              merchantName: merchantData.merchant.name,
              placeId: merchantData.merchant.place_id || undefined,
              merchantId: merchantData.merchant.id,
              rewardDescription: merchantData.perk?.title,
            })
            setActiveClaimId(resp.id)
            setActiveClaimRemaining(resp.remaining_seconds)
            setShowClaimSheet(false)
            setShowReceiptUpload(true)
          }}
        />
      )}

      {/* Receipt Upload Modal */}
      {merchantData && activeClaimId && (
        <ReceiptUploadModal
          isOpen={showReceiptUpload}
          merchantName={merchantData.merchant.name}
          claimId={activeClaimId}
          remainingSeconds={activeClaimRemaining}
          onClose={() => setShowReceiptUpload(false)}
          onUpload={async (imageBase64) => {
            const result = await uploadReceiptMutation.mutateAsync({
              claimId: activeClaimId,
              imageBase64,
            })
            setReceiptResult(result)
            setShowReceiptUpload(false)
            setShowReceiptResult(true)
          }}
        />
      )}

      {/* Receipt Result Modal */}
      {merchantData && receiptResult && (
        <ReceiptResultModal
          isOpen={showReceiptResult}
          result={receiptResult}
          merchantName={merchantData.merchant.name}
          onClose={() => setShowReceiptResult(false)}
        />
      )}
    </div>
  )
}
