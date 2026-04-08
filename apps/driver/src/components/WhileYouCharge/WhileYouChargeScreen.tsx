// WhileYouCharge Screen matching Figma exactly
import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChargingActivePill } from './ChargingActivePill'
import { FEATURE_FLAGS } from '../../config/featureFlags'
import { isDemoMode, isMockMode } from '../../services/api'
import { FeaturedMerchantCard } from './FeaturedMerchantCard'
import { SecondaryMerchantCard } from './SecondaryMerchantCard'
import { Carousel } from '../shared/Carousel'
import { useMerchantsForCharger } from '../../services/api'
import { MerchantCardSkeleton } from '../shared/Skeleton'
import SponsorRewardCard from '../SponsorRewards/SponsorRewardCard'
import type { MerchantSummary } from '../../types'

// Carousel item type with id for keying
type CarouselMerchant = MerchantSummary & { id: string }

export function WhileYouChargeScreen() {
  const navigate = useNavigate()

  // For Canyon Ridge charger, use the actual charger ID
  // In production, this would come from location check or charger selection
  const chargerId = 'canyon_ridge_tesla'
  
  // Fetch merchants for charger (charging state)
  const { data: fetchedMerchants = [], isLoading: merchantsLoading, refetch: refetchMerchants } = useMerchantsForCharger(
    chargerId,
    { state: 'charging', open_only: false }
  )

  // Combine primary + secondary for carousel (primary first, then secondary)
  const merchants = useMemo<CarouselMerchant[]>(() => {
    // Filter out merchants without place_id
    const validMerchants = fetchedMerchants.filter(m => m.place_id)

    // Find primary merchant
    const primary = validMerchants.find(m => m.is_primary)
    // Get secondary merchants (non-primary, limit to 2)
    const secondary = validMerchants.filter(m => !m.is_primary).slice(0, 2)

    const all: CarouselMerchant[] = []
    if (primary) {
      all.push({
        ...primary,
        place_id: primary.place_id!,
        id: primary.place_id!,
        distance_m: primary.distance_m ?? 0,
        types: primary.types ?? [],
      })
    }
    all.push(...secondary.map(m => ({
      ...m,
      place_id: m.place_id!,
      id: m.place_id!,
      distance_m: m.distance_m ?? 0,
      types: m.types ?? [],
    })))
    return all
  }, [fetchedMerchants])

  const handleMerchantClick = (placeId: string) => {
    navigate(`/merchant/${placeId}`)
  }

  const handleToggleToPreCharging = () => {
    navigate('/pre-charging')
  }

  return (
    <div className="h-[100dvh] max-h-[100dvh] bg-white flex flex-col overflow-hidden">
      {/* Header - Matching Figma: 60px height, 20px horizontal padding */}
      <header className="bg-white px-5 h-[60px] flex-shrink-0 flex items-center justify-between border-b border-[#E4E6EB] border-t-0 border-l-0 border-r-0">
        {/* Logo */}
        <div className="flex items-center gap-1.5">
          <img 
            src="/nerava-logo.png" 
            alt="Nerava" 
            className="h-6 w-auto"
          />
        </div>
        
        {/* Right side: Charging Active pill + Dev toggle */}
        <div className="flex items-center gap-2">
          {/* Dev control: Toggle to Pre-Charging - only show in demo/dev mode */}
          {(isDemoMode() || isMockMode()) && (
            <button
              onClick={handleToggleToPreCharging}
              className="px-2 py-1 text-xs text-[#656A6B] hover:text-[#050505] underline"
              title="Switch to Pre-Charging state"
            >
              Pre-Charging
            </button>
          )}
          <ChargingActivePill />
        </div>
      </header>

      {/* Main content - Matching Figma padding: 24px horizontal, 16px top */}
      <main className="flex-1 flex flex-col overflow-y-auto min-h-0">
        <div className="flex-1 px-6 pt-3 pb-2 flex flex-col min-h-0">
          {/* Title section - More compact for mobile */}
          <div className="mb-3 space-y-0.5 flex-shrink-0">
            {/* Heading 1: Reduced size for mobile - single line */}
            <h2 
              className="text-2xl font-medium leading-7 text-[#050505] text-center whitespace-nowrap"
              style={{ letterSpacing: '0.395px', whiteSpace: 'nowrap' }}
            >
              {FEATURE_FLAGS.LIVE_COORDINATION_UI_V1 ? 'Lock in your spot while you charge' : 'What to do while you charge'}
            </h2>
            
            {/* Subtitle: 14px Regular, line-height 20px, letter-spacing -0.15px, center-aligned */}
            <p 
              className="text-xs font-normal leading-4 text-[#656A6B] text-center"
              style={{ letterSpacing: '-0.15px' }}
            >
              Curated access, active while charging
            </p>
          </div>

          {/* Carousel - Constrained to fit viewport */}
          <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
            {merchantsLoading ? (
              <div className="grid gap-4 px-5">
                {[...Array(3)].map((_, i) => <MerchantCardSkeleton key={i} />)}
              </div>
            ) : merchants.length > 0 ? (
              <Carousel<CarouselMerchant>
                items={merchants}
                renderPrimary={(merchant) => (
                  <FeaturedMerchantCard
                    merchant={merchant}
                    onClick={() => handleMerchantClick(merchant.place_id)}
                  />
                )}
                renderSecondary={(merchant) => (
                  <SecondaryMerchantCard
                    merchant={merchant}
                    onClick={() => handleMerchantClick(merchant.place_id)}
                  />
                )}
              />
            ) : (
              <div className="flex flex-col items-center justify-center py-12 px-6">
                <div className="w-20 h-20 bg-[#F7F8FA] rounded-full flex items-center justify-center mb-4">
                  <svg className="w-10 h-10 text-[#656A6B]" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                </div>
                <h3 className="text-lg font-medium text-[#050505] mb-1">No experiences yet</h3>
                <p className="text-sm text-[#656A6B] text-center mb-4">
                  {FEATURE_FLAGS.LIVE_COORDINATION_UI_V1
                    ? 'No live experiences are available right now. Check back soon.'
                    : 'No merchants available at this location.'}
                </p>
                <button
                  onClick={() => refetchMerchants()}
                  className="px-6 py-2.5 bg-[#1877F2] text-white text-sm font-medium rounded-full hover:bg-[#166FE5] active:scale-[0.98] transition-all"
                >
                  Refresh
                </button>
              </div>
            )}

            {/* Sponsor Partner Rewards */}
            <div className="mt-4 px-1 pb-4">
              <SponsorRewardCard isCharging={true} />
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
