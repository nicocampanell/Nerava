import { Trophy } from 'lucide-react'
import { useLeaderboard } from '../../services/api'

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

export function LeaderboardCard() {
  const { data, isLoading } = useLeaderboard(10)

  if (isLoading || !data || data.entries.length === 0) return null

  return (
    <div className="mx-4 mb-4 bg-white rounded-2xl border border-gray-100 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100">
        <Trophy className="w-4 h-4 text-amber-500" />
        <span className="text-sm font-semibold text-gray-900">Top Earners</span>
      </div>

      <div className="divide-y divide-gray-50">
        {data.entries.slice(0, 5).map((entry) => (
          <div
            key={entry.rank}
            className={`flex items-center gap-3 px-4 py-2.5 ${entry.is_current_user ? 'bg-blue-50' : ''}`}
          >
            <span className={`w-6 text-center text-sm font-bold ${
              entry.rank === 1 ? 'text-amber-500' :
              entry.rank === 2 ? 'text-gray-400' :
              entry.rank === 3 ? 'text-amber-700' :
              'text-gray-300'
            }`}>
              {entry.rank}
            </span>
            <span className={`flex-1 text-sm ${entry.is_current_user ? 'font-bold text-[#1877F2]' : 'text-gray-700'}`}>
              {entry.display_name}
            </span>
            <span className="text-sm font-semibold text-green-600">
              {formatCents(entry.total_earned_cents)}
            </span>
          </div>
        ))}
      </div>

      {data.current_user_rank && !data.entries.some(e => e.is_current_user) && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-blue-50 border-t border-gray-100">
          <span className="w-6 text-center text-sm font-bold text-[#1877F2]">
            {data.current_user_rank}
          </span>
          <span className="flex-1 text-sm font-bold text-[#1877F2]">You</span>
          <span className="text-sm font-semibold text-green-600">
            {formatCents(data.current_user_earned_cents || 0)}
          </span>
        </div>
      )}
    </div>
  )
}
