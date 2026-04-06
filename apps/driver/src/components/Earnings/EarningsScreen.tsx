import { useNavigate } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import { useWalletBalance, useWalletLedger } from '../../services/api'
import { LeaderboardCard } from './LeaderboardCard'
import type { WalletLedgerEntry } from '../../services/api'

function formatCents(cents: number): string {
  const abs = Math.abs(cents)
  return `${cents < 0 ? '-' : ''}$${(abs / 100).toFixed(2)}`
}

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function LedgerEntry({ entry }: { entry: WalletLedgerEntry }) {
  const isCredit = entry.amount_cents > 0
  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">
          {entry.description || entry.transaction_type}
        </p>
        {entry.campaign_name && (
          <p className="text-xs text-gray-500 truncate">
            {entry.sponsor_name ? `${entry.sponsor_name} — ` : ''}{entry.campaign_name}
          </p>
        )}
        <p className="text-xs text-gray-400">{formatDate(entry.created_at)}</p>
      </div>
      <span className={`text-sm font-semibold ml-3 ${isCredit ? 'text-green-600' : 'text-red-500'}`}>
        {isCredit ? '+' : ''}{formatCents(entry.amount_cents)}
      </span>
    </div>
  )
}

export function EarningsScreen() {
  const navigate = useNavigate()
  const { data: balance } = useWalletBalance()
  const { data: ledger, isLoading } = useWalletLedger(100)

  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-white border-b border-gray-100 px-4 py-3 flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="p-1 -ml-1 rounded-lg hover:bg-gray-100">
          <ChevronLeft className="w-5 h-5 text-gray-700" />
        </button>
        <h1 className="text-lg font-semibold text-gray-900">Earnings</h1>
      </div>

      {/* Balance summary */}
      <div className="px-4 py-6 text-center bg-gradient-to-b from-blue-50 to-white">
        <p className="text-sm text-gray-500 mb-1">Total Earned</p>
        <p className="text-3xl font-bold text-gray-900">
          {balance ? formatCents(balance.total_earned_cents) : '$0.00'}
        </p>
        {balance && (
          <p className="text-sm text-gray-500 mt-2">
            Available: {formatCents(balance.available_cents)}
          </p>
        )}
      </div>

      {/* Leaderboard */}
      <LeaderboardCard />

      {/* Ledger list */}
      <div className="px-4 pb-8">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Transaction History
        </h2>
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-16 bg-gray-100 rounded-lg animate-pulse" />
            ))}
          </div>
        ) : ledger && ledger.entries.length > 0 ? (
          <div>
            {ledger.entries.map(entry => (
              <LedgerEntry key={entry.id} entry={entry} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-400 text-center py-8">
            No transactions yet. Earn rewards by charging at Nerava locations.
          </p>
        )}
      </div>
    </div>
  )
}
