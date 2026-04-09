import { useState } from 'react'
import { ArrowLeft, Copy, Share2, Gift, Check, Users, Zap } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { usePublicStats } from '../../services/api'

interface ShareNeravaProps {
  onClose: () => void
  referralCode: string
}

function formatCompact(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return n.toLocaleString()
}

export function ShareNerava({ onClose, referralCode }: ShareNeravaProps) {
  const [copied, setCopied] = useState(false)
  const { data: stats } = usePublicStats()
  const referralLink = `https://app.nerava.network/join?ref=${referralCode}`

  const handleCopyLink = async () => {
    try {
      // navigator.clipboard requires HTTPS — fallback for HTTP dev servers
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(referralLink)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = referralLink
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy:', err)
    }
  }

  const handleShareLink = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'Join Nerava',
          text: 'Get exclusive offers while charging your EV!',
          url: referralLink,
        })
      } catch (err) {
        // User cancelled or share failed
        console.log('Share cancelled')
      }
    } else {
      handleCopyLink()
    }
  }

  return (
    <div className="fixed inset-0 bg-white z-[3000] flex flex-col">
      {/* Header */}
      <header className="flex items-center p-4 border-b border-[#E4E6EB]">
        <button onClick={onClose} className="p-2 -ml-2 hover:bg-gray-100 rounded-full">
          <ArrowLeft className="w-6 h-6" />
        </button>
        <div className="flex-1" />
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-8">
        {/* QR Code Section */}
        <div className="text-center mb-8">
          {/* QR Icon Placeholder */}
          <div className="w-20 h-20 bg-blue-50 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-10 h-10 text-[#1877F2]" viewBox="0 0 24 24" fill="currentColor">
              <path d="M3 3h6v6H3V3zm2 2v2h2V5H5zm8-2h6v6h-6V3zm2 2v2h2V5h-2zM3 13h6v6H3v-6zm2 2v2h2v-2H5zm13-2h3v2h-3v-2zm0 4h3v4h-4v-3h1v2h2v-1h-2v-2zm-4 0h2v2h-2v-2zm2 2h2v2h-2v-2zm-2 2h2v2h-2v-2z"/>
            </svg>
          </div>

          <h1 className="text-2xl font-bold mb-2">Share Nerava</h1>
          <p className="text-[#65676B]">Share with EV drivers or merchants</p>
        </div>

        {/* QR Code Card */}
        <div className="bg-gray-50 rounded-2xl p-6 mb-4">
          {/* Real QR Code */}
          <div className="w-48 h-48 bg-white rounded-xl mx-auto mb-4 flex items-center justify-center border border-[#E4E6EB] p-3">
            <QRCodeSVG
              value={referralLink}
              size={168}
              bgColor="#ffffff"
              fgColor="#1877F2"
              level="M"
            />
          </div>
          <p className="text-center text-sm text-[#65676B]">Scan to join Nerava</p>

          {/* Referral Code */}
          <div className="mt-4 bg-white rounded-xl p-4 border border-[#E4E6EB]">
            <p className="text-center text-sm text-[#65676B] mb-1">Referral Code</p>
            <p className="text-center text-xl font-bold text-[#1877F2] tracking-wider">{referralCode}</p>
          </div>
        </div>

        {/* Rewards Info */}
        <div className="bg-blue-50 rounded-2xl p-4 mb-6 border border-blue-100">
          <div className="flex items-center gap-2 mb-3">
            <Gift className="w-5 h-5 text-[#1877F2]" />
            <span className="font-semibold text-[#1877F2]">Referral Rewards</span>
          </div>
          <ul className="space-y-1 text-sm text-[#050505]">
            <li>• Driver referral: Both get $2.50 credit</li>
            <li>• Merchant referral: Free month premium</li>
          </ul>
        </div>

        {/* Network Stats (social proof) */}
        {stats && stats.total_drivers > 0 && (
          <div className="bg-gray-50 rounded-2xl p-4 mb-6">
            <p className="text-xs text-[#65676B] uppercase tracking-wide font-medium mb-3">Nerava Network</p>
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <div className="flex items-center justify-center gap-1 mb-1">
                  <Users className="w-3.5 h-3.5 text-[#1877F2]" />
                </div>
                <p className="text-lg font-bold text-gray-900">{formatCompact(stats.total_drivers)}</p>
                <p className="text-xs text-[#65676B]">Drivers</p>
              </div>
              <div>
                <div className="flex items-center justify-center gap-1 mb-1">
                  <Zap className="w-3.5 h-3.5 text-amber-500" />
                </div>
                <p className="text-lg font-bold text-gray-900">{formatCompact(stats.total_sessions)}</p>
                <p className="text-xs text-[#65676B]">Sessions</p>
              </div>
              <div>
                <div className="flex items-center justify-center gap-1 mb-1">
                  <Gift className="w-3.5 h-3.5 text-green-500" />
                </div>
                <p className="text-lg font-bold text-gray-900">${formatCompact(stats.total_earned_cents / 100)}</p>
                <p className="text-xs text-[#65676B]">Earned</p>
              </div>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="space-y-3">
          <button
            onClick={handleCopyLink}
            className="w-full py-4 bg-[#1877F2] text-white font-semibold rounded-xl flex items-center justify-center gap-2 hover:bg-[#166FE5] active:scale-[0.98] transition-all"
          >
            {copied ? (
              <>
                <Check className="w-5 h-5" />
                Copied!
              </>
            ) : (
              <>
                <Copy className="w-5 h-5" />
                Copy Referral Link
              </>
            )}
          </button>

          <button
            onClick={handleShareLink}
            className="w-full py-4 bg-white text-[#1877F2] font-semibold rounded-xl border-2 border-[#1877F2] flex items-center justify-center gap-2 hover:bg-blue-50 active:scale-[0.98] transition-all"
          >
            <Share2 className="w-5 h-5" />
            Share Link
          </button>
        </div>
      </div>
    </div>
  )
}
