import { useState, useRef, useCallback, useEffect } from 'react'
import { X, Navigation, Zap, MapPin, Shield, Users, DollarSign, Phone, Globe, Coffee, ShoppingBag, Utensils, Dumbbell, TreePine, Popcorn, CircleDollarSign, Gift, UserPlus, ExternalLink } from 'lucide-react'
import { useChargerDetail, useDriverCampaigns, useActivateExclusive, useRequestToJoin, startDriverOrder } from '../../services/api'
import type { ChargerDetailNearbyMerchant, DriverCampaign } from '../../services/api'
import { capture, DRIVER_EVENTS } from '../../analytics'
import { openExternalUrl } from '../../utils/openExternal'
import { useNativeBridge } from '../../hooks/useNativeBridge'

type SheetState = 'peek' | 'expanded' | 'dismissed'
type Tab = 'overview' | 'rewards' | 'nearby'

interface ChargerDetailSheetProps {
  chargerId: string
  chargerName: string
  networkName?: string
  lat?: number
  lng?: number
  userLat?: number
  userLng?: number
  onClose: () => void
  isCharging: boolean
  onViewSession?: () => void
  isAuthenticated?: boolean
  onLoginRequired?: () => void
  onClaimActivated?: (sessionId: string) => void
}

const NETWORK_COLORS: Record<string, string> = {
  Tesla: '#E31937',
  ChargePoint: '#10B981',
  'Electrify America': '#2563EB',
  EVgo: '#1877F2',
  Blink: '#14B8A6',
}

function getNetworkColor(network?: string | null): string {
  if (network && NETWORK_COLORS[network]) return NETWORK_COLORS[network]
  return '#1877F2'
}

function formatDistance(meters: number): string {
  if (meters < 1000) return `${Math.round(meters)}m`
  return `${(meters / 1609.34).toFixed(1)} mi`
}

function getScoreLabel(score: number): { label: string; color: string; bg: string } {
  if (score >= 80) return { label: 'Excellent', color: 'text-green-700', bg: 'bg-green-50' }
  if (score >= 60) return { label: 'Good', color: 'text-yellow-700', bg: 'bg-yellow-50' }
  if (score >= 40) return { label: 'Fair', color: 'text-orange-700', bg: 'bg-orange-50' }
  return { label: 'Poor', color: 'text-red-700', bg: 'bg-red-50' }
}

const CATEGORY_ICONS: Record<string, typeof Coffee> = {
  cafe: Coffee,
  coffee_shop: Coffee,
  restaurant: Utensils,
  food: Utensils,
  bakery: Utensils,
  store: ShoppingBag,
  shopping: ShoppingBag,
  shopping_mall: ShoppingBag,
  gym: Dumbbell,
  fitness: Dumbbell,
  park: TreePine,
  movie_theater: Popcorn,
}

function getCategoryIcon(category?: string | null): typeof Coffee {
  if (!category) return ShoppingBag
  const key = category.toLowerCase().replace(/\s+/g, '_')
  for (const [k, icon] of Object.entries(CATEGORY_ICONS)) {
    if (key.includes(k)) return icon
  }
  return ShoppingBag
}

export function ChargerDetailSheet({
  chargerId,
  chargerName,
  networkName,
  lat,
  lng,
  userLat,
  userLng,
  onClose,
  isCharging,
  onViewSession,
  isAuthenticated = false,
  onLoginRequired,
  onClaimActivated,
}: ChargerDetailSheetProps) {
  const { data: detail, isLoading } = useChargerDetail(chargerId, userLat, userLng)
  const { data: campaignsData } = useDriverCampaigns(userLat, userLng, chargerId)
  const campaigns = campaignsData?.campaigns ?? []
  const [sheetState, setSheetState] = useState<SheetState>('peek')
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [actionMerchant, setActionMerchant] = useState<ChargerDetailNearbyMerchant | null>(null)
  // Claim flow state
  const [claimingMerchant, setClaimingMerchant] = useState<ChargerDetailNearbyMerchant | null>(null)
  const [claimState, setClaimState] = useState<'idle' | 'confirming' | 'success' | 'error'>('idle')
  const [claimedSession, setClaimedSession] = useState<{id: string, merchantName: string} | null>(null)
  const [claimError, setClaimError] = useState<string | null>(null)
  // Request to join state
  const [requestingMerchant, setRequestingMerchant] = useState<ChargerDetailNearbyMerchant | null>(null)
  const [requestSent, setRequestSent] = useState<Set<string>>(new Set())
  const activateExclusive = useActivateExclusive()
  const requestToJoin = useRequestToJoin()
  const { openInAppBrowser } = useNativeBridge()
  // Toast state for "Opening [merchant name]..."
  const [orderToast, setOrderToast] = useState<string | null>(null)
  const sheetRef = useRef<HTMLDivElement>(null)
  const dragStartY = useRef(0)
  const dragStartTranslate = useRef(0)
  const isDragging = useRef(false)

  useEffect(() => {
    capture(DRIVER_EVENTS.CHARGER_DETAIL_VIEWED, {
      charger_id: chargerId,
      charger_name: chargerName,
      network: networkName,
    })
  }, [chargerId, chargerName, networkName])

  // Auto-expand after a short delay
  useEffect(() => {
    const timer = setTimeout(() => setSheetState('expanded'), 300)
    return () => clearTimeout(timer)
  }, [])

  const networkColor = getNetworkColor(detail?.network_name ?? networkName)
  const connectors = detail?.connector_types ?? []
  const rewardCents = detail?.active_reward_cents ?? 0

  // Sheet height percentages
  const getTranslateY = useCallback((state: SheetState) => {
    switch (state) {
      case 'peek': return 60 // 60% from top = 40% visible
      case 'expanded': return 8 // 8% from top = 92% visible
      case 'dismissed': return 100
    }
  }, [])

  // Drag handlers — only on the drag handle
  const handleDragStart = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    const clientY = 'touches' in e ? e.touches[0].clientY : e.clientY
    dragStartY.current = clientY
    dragStartTranslate.current = getTranslateY(sheetState)
    isDragging.current = true
  }, [sheetState, getTranslateY])

  const handleDragMove = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    if (!isDragging.current || !sheetRef.current) return
    const clientY = 'touches' in e ? e.touches[0].clientY : e.clientY
    const deltaPercent = ((clientY - dragStartY.current) / window.innerHeight) * 100
    const newTranslate = Math.max(8, Math.min(100, dragStartTranslate.current + deltaPercent))
    sheetRef.current.style.transition = 'none'
    sheetRef.current.style.transform = `translateY(${newTranslate}%)`
  }, [])

  const handleDragEnd = useCallback((e: React.TouchEvent | React.MouseEvent) => {
    if (!isDragging.current || !sheetRef.current) return
    isDragging.current = false
    const clientY = 'changedTouches' in e ? e.changedTouches[0].clientY : e.clientY
    const deltaPercent = ((clientY - dragStartY.current) / window.innerHeight) * 100

    sheetRef.current.style.transition = ''

    if (deltaPercent > 30) {
      // Dragged down significantly
      if (sheetState === 'expanded') {
        setSheetState('peek')
      } else {
        setSheetState('dismissed')
        setTimeout(onClose, 300)
      }
    } else if (deltaPercent < -20) {
      // Dragged up
      setSheetState('expanded')
    } else {
      // Snap back
      sheetRef.current.style.transform = `translateY(${getTranslateY(sheetState)}%)`
    }
  }, [sheetState, onClose, getTranslateY])

  const handleGetDirections = () => {
    const destLat = detail?.lat ?? lat
    const destLng = detail?.lng ?? lng
    capture(DRIVER_EVENTS.CHARGER_DIRECTIONS_CLICKED, {
      charger_id: chargerId,
      charger_name: chargerName,
    })
    if (destLat && destLng) {
      openExternalUrl(`https://www.google.com/maps/dir/?api=1&destination=${destLat},${destLng}`)
    }
  }

  // Merchant action sheet handlers
  const handleMerchantCall = (m: ChargerDetailNearbyMerchant) => {
    if (m.phone) openExternalUrl(`tel:${m.phone}`)
    setActionMerchant(null)
  }

  const handleMerchantWebsite = (m: ChargerDetailNearbyMerchant) => {
    if (m.website) openExternalUrl(m.website)
    setActionMerchant(null)
  }

  const handleMerchantDirections = (m: ChargerDetailNearbyMerchant) => {
    if (m.lat && m.lng) {
      openExternalUrl(`https://www.google.com/maps/dir/?api=1&destination=${m.lat},${m.lng}`)
    }
    setActionMerchant(null)
  }

  // Claim reward handler
  const handleClaimReward = async (m: ChargerDetailNearbyMerchant) => {
    if (!isAuthenticated) {
      setActionMerchant(null)
      onLoginRequired?.()
      return
    }
    setActionMerchant(null)
    setClaimingMerchant(m)
    setClaimState('confirming')
  }

  const handleConfirmClaim = async () => {
    if (!claimingMerchant) return
    setClaimError(null)
    const idempotencyKey = crypto.randomUUID()
    try {
      const result = await activateExclusive.mutateAsync({
        request: {
          merchant_id: claimingMerchant.place_id,
          merchant_place_id: claimingMerchant.place_id,
          charger_id: chargerId,
          lat: userLat ?? 0,
          lng: userLng ?? 0,
        },
        idempotencyKey,
      })
      capture(DRIVER_EVENTS.EXCLUSIVE_ACTIVATE_SUCCESS, {
        merchant_name: claimingMerchant.name,
        charger_id: chargerId,
      })
      setClaimedSession({
        id: result.exclusive_session?.id || '',
        merchantName: claimingMerchant.name,
      })
      setClaimState('success')
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Something went wrong. Please try again.'
      setClaimError(message)
      setClaimState('error')
    }
  }

  // Request to join handler
  const handleRequestToJoin = async (m: ChargerDetailNearbyMerchant) => {
    if (!isAuthenticated) {
      setActionMerchant(null)
      onLoginRequired?.()
      return
    }
    setActionMerchant(null)
    setRequestingMerchant(m)
    try {
      await requestToJoin.mutateAsync({
        placeId: m.place_id,
        merchantName: m.name,
      })
      setRequestSent((prev) => new Set(prev).add(m.place_id))
      capture(DRIVER_EVENTS.MERCHANT_CLICKED, {
        action: 'request_to_join',
        merchant_name: m.name,
        place_id: m.place_id,
      })
    } catch {
      // Silently handle - may already be requested
      setRequestSent((prev) => new Set(prev).add(m.place_id))
    }
    setTimeout(() => setRequestingMerchant(null), 2000)
  }

  // "Order from the car" handler
  const handleOrderFromCar = async (m: ChargerDetailNearbyMerchant) => {
    if (!m.ordering_url) return
    setActionMerchant(null)

    // Fire-and-forget: notify backend (don't block the user if it fails)
    startDriverOrder({
      merchant_id: m.place_id,
      ordering_url: m.ordering_url,
    }).catch(() => {
      // Silently handle — the order tracking is best-effort
    })

    // PostHog event
    capture(DRIVER_EVENTS.IN_APP_BROWSER_OPENED, {
      merchant_name: m.name,
      ordering_url: m.ordering_url,
      place_id: m.place_id,
    })

    // Show toast
    setOrderToast(m.name)
    setTimeout(() => setOrderToast(null), 3000)

    // Open the ordering URL via native bridge (falls back to window.open)
    openInAppBrowser(m.ordering_url)
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[2999]"
        onClick={() => {
          if (sheetState === 'expanded') {
            setSheetState('peek')
          } else {
            setSheetState('dismissed')
            setTimeout(onClose, 300)
          }
        }}
      />

      {/* Sheet */}
      <div
        ref={sheetRef}
        className="fixed inset-x-0 bottom-0 z-[3000] bg-white rounded-t-3xl shadow-2xl flex flex-col"
        style={{
          height: '92vh',
          transform: `translateY(${getTranslateY(sheetState)}%)`,
          transition: 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
        }}
      >
        {/* Drag handle */}
        <div
          className="flex-shrink-0 pt-3 pb-2 cursor-grab active:cursor-grabbing touch-none"
          onTouchStart={handleDragStart}
          onTouchMove={handleDragMove}
          onTouchEnd={handleDragEnd}
          onMouseDown={handleDragStart}
          onMouseMove={handleDragMove}
          onMouseUp={handleDragEnd}
        >
          <div className="w-10 h-1 bg-gray-300 rounded-full mx-auto" />
        </div>

        {/* Header */}
        <div className="flex-shrink-0 px-5 pb-3">
          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: networkColor }} />
                <h2 className="text-lg font-semibold text-[#050505] truncate">{detail?.name ?? chargerName}</h2>
              </div>
              <p className="text-sm text-[#65676B]">
                {detail?.network_name ?? networkName ?? 'Charging Station'}
                {detail?.distance_m != null && ` · ${formatDistance(detail.distance_m)}`}
                {detail?.drive_time_min != null && ` · ${detail.drive_time_min} min`}
              </p>

              {/* Live drivers */}
              {detail && detail.drivers_charging_now > 0 && (
                <div className="flex items-center gap-1.5 mt-1">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                  </span>
                  <span className="text-xs font-medium text-green-700">
                    {detail.drivers_charging_now} charging now
                  </span>
                </div>
              )}
            </div>

            <button
              onClick={() => {
                setSheetState('dismissed')
                setTimeout(onClose, 300)
              }}
              className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 ml-3 flex-shrink-0"
            >
              <X className="w-4 h-4 text-gray-500" />
            </button>
          </div>

          {/* Quick action row */}
          <div className="flex gap-2 mt-3">
            <button
              onClick={handleGetDirections}
              className="flex-1 flex items-center justify-center gap-1.5 py-2 bg-[#1877F2] text-white rounded-xl text-sm font-medium active:scale-[0.98] transition-transform"
            >
              <Navigation className="w-3.5 h-3.5" />
              Directions
            </button>
            {isCharging && onViewSession && (
              <button
                onClick={onViewSession}
                className="flex-1 flex items-center justify-center gap-1.5 py-2 bg-green-500 text-white rounded-xl text-sm font-medium active:scale-[0.98] transition-transform"
              >
                <Zap className="w-3.5 h-3.5" />
                View Session
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex-shrink-0 flex border-b border-gray-100 px-5">
          {(['overview', 'rewards', 'nearby'] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`flex-1 py-2.5 text-sm font-medium capitalize transition-colors relative ${
                activeTab === tab ? 'text-[#1877F2]' : 'text-[#65676B]'
              }`}
            >
              {tab}
              {activeTab === tab && (
                <div className="absolute bottom-0 left-2 right-2 h-0.5 bg-[#1877F2] rounded-full" />
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="bg-gray-50 rounded-2xl p-4 animate-pulse h-16" />
              ))}
            </div>
          ) : activeTab === 'overview' ? (
            <OverviewTab detail={detail} connectors={connectors} rewardCents={rewardCents} />
          ) : activeTab === 'rewards' ? (
            <RewardsTab rewardCents={rewardCents} detail={detail} campaigns={campaigns} isCharging={isCharging} />
          ) : (
            <NearbyTab
              merchants={detail?.nearby_merchants ?? []}
              onMerchantTap={setActionMerchant}
            />
          )}
        </div>
      </div>

      {/* Merchant Action Sheet */}
      {actionMerchant && (
        <MerchantActionSheet
          merchant={actionMerchant}
          isCharging={isCharging}
          onCall={() => handleMerchantCall(actionMerchant)}
          onWebsite={() => handleMerchantWebsite(actionMerchant)}
          onDirections={() => handleMerchantDirections(actionMerchant)}
          onClaimReward={() => handleClaimReward(actionMerchant)}
          onRequestToJoin={() => handleRequestToJoin(actionMerchant)}
          onOrderFromCar={() => handleOrderFromCar(actionMerchant)}
          alreadyRequested={requestSent.has(actionMerchant.place_id)}
          onClose={() => setActionMerchant(null)}
        />
      )}

      {/* Claim Confirmation Modal */}
      {(claimState === 'confirming' || claimState === 'success' || claimState === 'error') && claimingMerchant && (
        <ClaimConfirmModal
          merchant={claimingMerchant}
          isCharging={isCharging}
          isLoading={activateExclusive.isPending}
          onConfirm={handleConfirmClaim}
          onClose={() => {
            if (claimState === 'success' && claimedSession) {
              onClaimActivated?.(claimedSession.id)
            }
            setClaimState('idle')
            setClaimingMerchant(null)
            setClaimedSession(null)
            setClaimError(null)
          }}
          claimState={claimState}
          claimError={claimError}
          onViewWallet={() => {
            if (claimedSession) {
              onClaimActivated?.(claimedSession.id)
            }
            setClaimState('idle')
            setClaimingMerchant(null)
            setClaimedSession(null)
            setClaimError(null)
          }}
        />
      )}


      {/* Request to Join Confirmation */}
      {requestingMerchant && (
        <div className="fixed top-12 inset-x-4 z-[3200] bg-[#1877F2] text-white rounded-2xl p-4 flex items-center gap-3 animate-slide-down shadow-lg">
          <UserPlus className="w-6 h-6 flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold">Request sent for {requestingMerchant.name}</p>
            <p className="text-xs opacity-90">We'll notify them that drivers want them on Nerava</p>
          </div>
        </div>
      )}

      {/* Order from the car toast */}
      {orderToast && (
        <div className="fixed top-12 inset-x-4 z-[3200] bg-emerald-600 text-white rounded-2xl p-4 flex items-center gap-3 animate-slide-down shadow-lg">
          <ExternalLink className="w-6 h-6 flex-shrink-0" />
          <div>
            <p className="text-sm font-semibold">Opening {orderToast}...</p>
            <p className="text-xs opacity-90">Place your order and pick it up while you charge</p>
          </div>
        </div>
      )}
    </>
  )
}

// ─── Overview Tab ────────────────────────────────────────────────────────────

function OverviewTab({ detail, connectors, rewardCents }: { detail: ReturnType<typeof useChargerDetail>['data']; connectors: string[]; rewardCents: number }) {
  if (!detail) return null

  return (
    <div className="space-y-3">
      {/* Network & Power */}
      <InfoRow
        icon={<Zap className="w-4 h-4 text-[#1877F2]" />}
        iconBg="bg-[#1877F2]/10"
        title="Power"
        value={[
          detail.num_evse ? `${detail.num_evse} stalls` : null,
          detail.power_kw ? `${detail.power_kw} kW` : null,
        ].filter(Boolean).join(' · ') || 'Check station'}
      />

      {/* Pricing with reward badge */}
      {detail.pricing_per_kwh != null && (
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 bg-green-100 rounded-full flex items-center justify-center flex-shrink-0">
            <DollarSign className="w-4 h-4 text-green-600" />
          </div>
          <div className="flex-1 min-w-0 py-0.5">
            <p className="text-xs text-[#65676B]">Pricing</p>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-[#050505]">
                ${detail.pricing_per_kwh.toFixed(2)}/kWh{detail.pricing_source === 'network_average' ? ' (avg)' : ''}
              </p>
              {rewardCents > 0 && (
                <span className="inline-flex items-center gap-0.5 px-2 py-0.5 bg-[#1877F2] text-white text-xs font-bold rounded-full">
                  <CircleDollarSign className="w-3 h-3" />
                  {(rewardCents / 100).toFixed(0)} reward
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Address */}
      {detail.address && (
        <InfoRow
          icon={<MapPin className="w-4 h-4 text-[#1877F2]" />}
          iconBg="bg-[#1877F2]/10"
          title="Address"
          value={[detail.address, detail.city, detail.state].filter(Boolean).join(', ')}
        />
      )}

      {/* Connectors */}
      {connectors.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {connectors.map((c) => (
            <span key={c} className="px-3 py-1.5 bg-gray-50 border border-gray-200 rounded-full text-xs font-medium text-[#050505]">
              {c}
            </span>
          ))}
        </div>
      )}

      {/* Nerava Score */}
      {detail.nerava_score != null && (
        <InfoRow
          icon={<Shield className="w-4 h-4 text-purple-600" />}
          iconBg="bg-purple-100"
          title="Nerava Score"
          value={`${getScoreLabel(detail.nerava_score).label} (${Math.round(detail.nerava_score)})`}
        />
      )}

      {/* Community */}
      {detail.total_sessions_30d > 0 && (
        <InfoRow
          icon={<Users className="w-4 h-4 text-purple-600" />}
          iconBg="bg-purple-100"
          title="Community"
          value={`${detail.total_sessions_30d} sessions · ${detail.unique_drivers_30d} drivers this month${detail.avg_duration_min > 0 ? ` · avg ${detail.avg_duration_min} min` : ''}`}
        />
      )}
    </div>
  )
}

// ─── Rewards Tab ─────────────────────────────────────────────────────────────

function RewardsTab({ rewardCents, detail, campaigns, isCharging }: { rewardCents: number; detail: ReturnType<typeof useChargerDetail>['data']; campaigns: DriverCampaign[]; isCharging: boolean }) {
  const hasCampaigns = campaigns.length > 0

  // No early return — partner offer campaigns (with offer_url) render dynamically below

  return (
    <div className="space-y-3">
      {/* Campaign cards (skip partner offer campaigns — they render separately below) */}
      {campaigns.filter((c) => !c.offer_url).map((c) => (
        <div
          key={c.id}
          className="bg-white border border-[#E4E6EB] rounded-2xl p-4 relative overflow-hidden"
        >
          {/* Blue accent bar */}
          <div className="absolute top-0 left-0 w-1 h-full bg-[#1877F2]" />

          <div className="flex items-start justify-between">
            <div className="flex-1 min-w-0 ml-2">
              <div className="flex items-center gap-2 mb-1">
                {c.sponsor_logo_url ? (
                  <img src={c.sponsor_logo_url} alt="" className="w-6 h-6 rounded-full object-cover" />
                ) : (
                  <div className="w-6 h-6 bg-[#1877F2]/10 rounded-full flex items-center justify-center">
                    <Zap className="w-3.5 h-3.5 text-[#1877F2]" />
                  </div>
                )}
                <span className="text-xs font-medium text-[#65676B]">{c.sponsor_name}</span>
              </div>
              <p className="text-sm font-semibold text-[#050505]">{c.name}</p>
              {c.description && (
                <p className="text-xs text-[#65676B] mt-0.5 line-clamp-2">{c.description}</p>
              )}
              {c.end_date && (
                <p className="text-xs text-[#65676B] mt-1">
                  Ends {new Date(c.end_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </p>
              )}
            </div>

            {/* Reward amount */}
            <div className="flex-shrink-0 ml-3 text-right">
              <div className="inline-flex items-center gap-1 px-3 py-1.5 bg-[#1877F2] rounded-xl">
                <CircleDollarSign className="w-4 h-4 text-white" />
                <span className="text-lg font-bold text-white">
                  {(c.reward_cents / 100).toFixed(c.reward_cents % 100 === 0 ? 0 : 2)}
                </span>
              </div>
              <p className="text-[10px] text-[#65676B] mt-1">per session</p>
            </div>
          </div>

          {/* Eligibility badge */}
          {!c.eligible && (
            <div className="mt-2 ml-2 inline-flex items-center px-2 py-0.5 bg-orange-50 border border-orange-200 rounded-full text-xs text-orange-700">
              Not eligible yet
            </div>
          )}
        </div>
      ))}

      {/* Partner Offer cards — campaigns with an offer_url open an external link */}
      {campaigns.filter((c) => c.offer_url).map((c) => (
        <button
          key={`offer-${c.id}`}
          onClick={() => {
            if (!isCharging || !c.offer_url) return
            capture(DRIVER_EVENTS.MERCHANT_CLICKED, { sponsor: c.sponsor_name, type: 'partner_offer' })
            openExternalUrl(c.offer_url)
          }}
          disabled={!isCharging}
          className={`w-full rounded-2xl border p-4 text-left transition-all relative overflow-hidden ${
            isCharging
              ? 'border-emerald-200 bg-emerald-50 active:scale-[0.98] cursor-pointer'
              : 'border-[#E4E6EB] bg-gray-50 opacity-60 cursor-not-allowed'
          }`}
        >
          <div className="absolute top-0 left-0 w-1 h-full bg-emerald-500" />
          <div className="flex items-start justify-between ml-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                {c.sponsor_logo_url ? (
                  <img src={c.sponsor_logo_url} alt="" className="w-6 h-6 rounded-full object-cover" />
                ) : (
                  <div className="w-6 h-6 bg-emerald-600 rounded-full flex items-center justify-center">
                    <Zap className="w-3.5 h-3.5 text-white" />
                  </div>
                )}
                <span className="text-xs font-medium text-[#65676B]">{c.sponsor_name}</span>
                <span className="text-[10px] font-medium text-emerald-700 bg-emerald-100 px-1.5 py-0.5 rounded-full">
                  Partner Offer
                </span>
              </div>
              <p className="text-sm font-semibold text-[#050505]">{c.name}</p>
              <p className="text-xs text-[#65676B] mt-0.5">
                {isCharging
                  ? (c.description || 'Tap to claim your partner offer')
                  : 'Start charging to unlock this reward'}
              </p>
            </div>
            <div className="flex-shrink-0 ml-3">
              <div className="inline-flex items-center gap-1 px-3 py-1.5 bg-emerald-600 rounded-xl">
                <span className="text-sm font-bold text-white">
                  {c.reward_cents > 0
                    ? `$${(c.reward_cents / 100).toFixed(c.reward_cents % 100 === 0 ? 0 : 2)}`
                    : 'Offer'}
                </span>
              </div>
            </div>
          </div>
        </button>
      ))}

      {/* Fallback: show generic reward if campaigns didn't load but active_reward_cents exists */}
      {!hasCampaigns && rewardCents > 0 && (
        <div className="bg-gradient-to-br from-[#1877F2] to-[#0D5BC6] rounded-2xl p-5 text-white">
          <div className="flex items-center gap-2 mb-2">
            <CircleDollarSign className="w-5 h-5" />
            <span className="text-sm font-medium opacity-90">Active Reward</span>
          </div>
          <p className="text-3xl font-bold">${(rewardCents / 100).toFixed(2)}</p>
          <p className="text-sm opacity-80 mt-1">per qualifying session at this charger</p>
        </div>
      )}

      {/* Earning potential */}
      {(hasCampaigns || rewardCents > 0) && detail?.pricing_per_kwh != null && (
        <div className="bg-green-50 rounded-2xl p-4">
          <p className="text-sm font-medium text-green-800">Charge here to earn</p>
          <p className="text-xs text-green-700 mt-1">
            Complete a qualifying charging session to receive the reward directly in your Nerava wallet.
          </p>
        </div>
      )}
    </div>
  )
}

// ─── Nearby Tab ─────────────────────────────────────────────────────────────

function NearbyTab({
  merchants,
  onMerchantTap,
}: {
  merchants: ChargerDetailNearbyMerchant[]
  onMerchantTap: (m: ChargerDetailNearbyMerchant) => void
}) {
  if (merchants.length === 0) {
    return (
      <div className="text-center py-8">
        <div className="w-14 h-14 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
          <MapPin className="w-7 h-7 text-gray-400" />
        </div>
        <p className="text-sm font-medium text-[#050505]">No nearby places</p>
        <p className="text-xs text-[#65676B] mt-1">We haven't found merchants near this charger yet</p>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {merchants.map((m) => {
        const Icon = getCategoryIcon(m.category)
        return (
          <button
            key={m.place_id}
            onClick={() => onMerchantTap(m)}
            className="w-full flex items-center gap-3 py-3 px-1 active:bg-gray-50 rounded-xl transition-colors text-left"
          >
            <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center flex-shrink-0">
              <Icon className="w-5 h-5 text-gray-600" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-[#050505] truncate">{m.name}</p>
              <p className="text-xs text-[#65676B]">
                {m.walk_time_min} min walk
                {m.category && ` · ${m.category}`}
              </p>
            </div>
            {m.has_exclusive && m.exclusive_title && (
              <span className="flex-shrink-0 px-2 py-1 bg-amber-50 border border-amber-200 rounded-full text-xs font-medium text-amber-700">
                {m.exclusive_title}
              </span>
            )}
            {m.has_exclusive && !m.exclusive_title && (
              <span className="flex-shrink-0 px-2 py-1 bg-amber-50 border border-amber-200 rounded-full text-xs font-medium text-amber-700">
                Deal
              </span>
            )}
            {!m.has_exclusive && (m.join_request_count ?? 0) > 0 && (
              <span className="flex-shrink-0 px-2 py-1 bg-blue-50 border border-blue-200 rounded-full text-xs font-medium text-blue-600">
                {m.join_request_count} requested
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ─── Shared Components ───────────────────────────────────────────────────────

function InfoRow({ icon, iconBg, title, value }: { icon: React.ReactNode; iconBg: string; title: string; value: string }) {
  return (
    <div className="flex items-start gap-3">
      <div className={`w-9 h-9 ${iconBg} rounded-full flex items-center justify-center flex-shrink-0`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0 py-0.5">
        <p className="text-xs text-[#65676B]">{title}</p>
        <p className="text-sm font-medium text-[#050505]">{value}</p>
      </div>
    </div>
  )
}

function MerchantActionSheet({
  merchant,
  isCharging,
  onCall,
  onWebsite,
  onDirections,
  onClaimReward,
  onRequestToJoin,
  onOrderFromCar,
  alreadyRequested,
  onClose,
}: {
  merchant: ChargerDetailNearbyMerchant
  isCharging: boolean
  onCall: () => void
  onWebsite: () => void
  onDirections: () => void
  onClaimReward: () => void
  onRequestToJoin: () => void
  onOrderFromCar: () => void
  alreadyRequested: boolean
  onClose: () => void
}) {
  const isNerava = merchant.is_nerava_merchant || merchant.has_exclusive
  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-[3100]" onClick={onClose} />
      <div className="fixed bottom-0 inset-x-0 z-[3101] bg-white rounded-t-2xl animate-slide-up">
        <div className="pt-3 pb-2">
          <div className="w-10 h-1 bg-gray-300 rounded-full mx-auto" />
        </div>
        <div className="px-5 pb-2">
          <p className="text-base font-semibold text-[#050505]">{merchant.name}</p>
          <p className="text-xs text-[#65676B]">{merchant.walk_time_min} min walk</p>
        </div>
        <div className="px-5 pb-6 space-y-1" style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 1.5rem)' }}>
          {/* Primary action: Claim Reward or Request to Join */}
          {isNerava ? (
            <button
              onClick={onClaimReward}
              className="w-full flex items-center gap-3 py-3 px-3 rounded-xl bg-green-50 active:bg-green-100 transition-colors"
            >
              <Gift className="w-5 h-5 text-green-600" />
              <div className="flex-1 text-left">
                <span className="text-sm font-semibold text-green-700">Claim Reward</span>
                {merchant.exclusive_title && (
                  <p className="text-xs text-green-600">{merchant.exclusive_title}</p>
                )}
                {!isCharging && (
                  <p className="text-xs text-orange-500">Plug in to claim</p>
                )}
              </div>
            </button>
          ) : (
            <button
              onClick={onRequestToJoin}
              disabled={alreadyRequested}
              className={`w-full flex items-center gap-3 py-3 px-3 rounded-xl transition-colors ${
                alreadyRequested ? 'bg-blue-50 opacity-60' : 'bg-blue-50 active:bg-blue-100'
              }`}
            >
              <UserPlus className="w-5 h-5 text-[#1877F2]" />
              <div className="flex-1 text-left">
                <span className="text-sm font-semibold text-[#1877F2]">
                  {alreadyRequested ? 'Requested' : 'Request to Join Nerava'}
                </span>
                {(merchant.join_request_count ?? 0) > 0 && (
                  <p className="text-xs text-[#65676B]">
                    {merchant.join_request_count} driver{(merchant.join_request_count ?? 0) !== 1 ? 's' : ''} requested
                  </p>
                )}
              </div>
            </button>
          )}

          {/* Order from the car — shown only when merchant has an ordering URL */}
          {merchant.ordering_url && (
            <button
              onClick={onOrderFromCar}
              className="w-full flex items-center gap-3 py-3 px-3 rounded-xl bg-emerald-50 active:bg-emerald-100 transition-colors"
            >
              <ExternalLink className="w-5 h-5 text-emerald-600" />
              <div className="flex-1 text-left">
                <span className="text-sm font-semibold text-emerald-700">Order from the car</span>
                <p className="text-xs text-emerald-600">Place your order now, pick up while you charge</p>
              </div>
            </button>
          )}

          {merchant.phone && (
            <button
              onClick={onCall}
              className="w-full flex items-center gap-3 py-3 px-3 rounded-xl active:bg-gray-50 transition-colors"
            >
              <Phone className="w-5 h-5 text-[#1877F2]" />
              <span className="text-sm font-medium">Call</span>
            </button>
          )}
          {merchant.website && (
            <button
              onClick={onWebsite}
              className="w-full flex items-center gap-3 py-3 px-3 rounded-xl active:bg-gray-50 transition-colors"
            >
              <Globe className="w-5 h-5 text-[#1877F2]" />
              <span className="text-sm font-medium">Visit Website</span>
            </button>
          )}
          <button
            onClick={onDirections}
            className="w-full flex items-center gap-3 py-3 px-3 rounded-xl active:bg-gray-50 transition-colors"
          >
            <Navigation className="w-5 h-5 text-[#1877F2]" />
            <span className="text-sm font-medium">Get Directions</span>
          </button>
          <button
            onClick={onClose}
            className="w-full py-3 text-sm font-medium text-[#65676B] rounded-xl active:bg-gray-50 transition-colors mt-1"
          >
            Cancel
          </button>
        </div>
      </div>
    </>
  )
}

// ─── Claim Confirmation Modal ────────────────────────────────────────────────

function ClaimConfirmModal({
  merchant,
  isCharging,
  isLoading,
  onConfirm,
  onClose,
  claimState,
  claimError,
  onViewWallet,
}: {
  merchant: ChargerDetailNearbyMerchant
  isCharging: boolean
  isLoading: boolean
  onConfirm: () => void
  onClose: () => void
  claimState: 'confirming' | 'success' | 'error'
  claimError: string | null
  onViewWallet: () => void
}) {
  return (
    <>
      <div className="fixed inset-0 bg-black/50 z-[3200]" onClick={claimState === 'confirming' || claimState === 'error' ? onClose : undefined} />
      <div className="fixed inset-x-4 top-1/2 -translate-y-1/2 z-[3201] bg-white rounded-3xl p-6 max-w-md mx-auto shadow-xl">

        {claimState === 'success' && (
          <div className="text-center py-4">
            <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <svg className="w-8 h-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-[#050505] mb-2">Reward claimed!</h3>
            <p className="text-sm text-[#65676B] mb-6">
              Walk over to {merchant.name} and enjoy your reward. Check your wallet for details.
            </p>
            <button
              onClick={onViewWallet}
              className="w-full py-3 text-sm font-semibold text-white bg-[#1877F2] rounded-xl active:scale-[0.98] transition-transform"
            >
              View Wallet
            </button>
          </div>
        )}

        {claimState === 'error' && (
          <div>
            <h3 className="text-lg font-semibold text-[#050505] mb-3">Claim failed</h3>
            <div className="bg-red-50 border border-red-200 rounded-xl p-3 mb-4">
              <p className="text-sm text-red-700">{claimError || 'Something went wrong. Please try again.'}</p>
            </div>
            <div className="flex gap-3">
              <button onClick={onClose} className="flex-1 py-3 text-sm font-medium text-[#65676B] bg-gray-100 rounded-xl">
                Close
              </button>
              <button onClick={onConfirm} className="flex-1 py-3 text-sm font-semibold text-white bg-green-600 rounded-xl active:scale-[0.98] transition-transform">
                Try Again
              </button>
            </div>
          </div>
        )}

        {claimState === 'confirming' && (
          <>
            <h3 className="text-lg font-semibold text-[#050505] mb-2">Claim reward at {merchant.name}?</h3>

            {!isCharging && (
              <div className="bg-orange-50 border border-orange-200 rounded-xl p-3 mb-4">
                <p className="text-sm text-orange-700">You need to be actively charging to claim this reward.</p>
              </div>
            )}

            {isCharging && (
              <>
                <p className="text-sm text-[#65676B] mb-4">
                  Walk over to {merchant.name} ({merchant.walk_time_min} min walk) and we'll track your visit.
                  {merchant.exclusive_title && (
                    <span className="font-medium text-green-700"> Reward: {merchant.exclusive_title}</span>
                  )}
                </p>
                <div className="bg-green-50 border border-green-200 rounded-xl p-3 mb-4 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-green-600" />
                  <p className="text-sm text-green-700">Charging verified — you're eligible</p>
                </div>
              </>
            )}

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 py-3 text-sm font-medium text-[#65676B] bg-gray-100 rounded-xl"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                disabled={!isCharging || isLoading}
                className="flex-1 py-3 text-sm font-semibold text-white bg-green-600 rounded-xl disabled:opacity-50 active:scale-[0.98] transition-transform"
              >
                {isLoading ? 'Claiming...' : 'Claim Now'}
              </button>
            </div>
          </>
        )}

      </div>
    </>
  )
}
