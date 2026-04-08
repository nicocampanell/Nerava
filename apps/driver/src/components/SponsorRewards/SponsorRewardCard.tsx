import { openExternalUrl } from '../../utils/openExternal'

interface SponsorRewardCardProps {
  isCharging: boolean
}

export default function SponsorRewardCard({ isCharging }: SponsorRewardCardProps) {
  const handleClick = () => {
    if (!isCharging) return
    openExternalUrl('https://evject.com/discount/nerava26')
  }

  return (
    <button
      onClick={handleClick}
      disabled={!isCharging}
      className={`w-full rounded-xl border p-4 text-left transition-all ${
        isCharging
          ? 'border-emerald-500/30 bg-emerald-50 shadow-sm active:scale-[0.98] cursor-pointer'
          : 'border-gray-200 bg-gray-50 opacity-60 cursor-not-allowed'
      }`}
    >
      <div className="flex items-center gap-3">
        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-emerald-600 text-white font-bold text-lg shrink-0">
          ⚡
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">10% Off EVject Chargers</span>
            <span className="text-[10px] font-medium text-emerald-700 bg-emerald-100 px-1.5 py-0.5 rounded-full">
              Partner Offer
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-0.5">
            {isCharging
              ? 'Tap to claim your discount on EVject charging equipment'
              : 'Start charging to unlock this reward'}
          </p>
        </div>
        <div className="shrink-0 text-gray-400">
          {isCharging ? (
            <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          ) : (
            <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          )}
        </div>
      </div>
    </button>
  )
}
