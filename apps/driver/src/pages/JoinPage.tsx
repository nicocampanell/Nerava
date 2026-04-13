import { useEffect } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { saveReferralCode } from '../utils/referralStorage'

export default function JoinPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const ref = searchParams.get('ref')

  useEffect(() => {
    if (ref) {
      saveReferralCode(ref)
    }
  }, [ref])

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-start pt-16 px-6 text-center">
      <img src="/nerava-bolt.jpg" alt="Nerava" className="w-16 h-16 mb-4" />

      <h1 className="text-3xl font-bold text-gray-900 mb-2">
        You've been invited to Nerava
      </h1>
      <p className="text-[#65676B] mb-8 max-w-sm">
        Earn rewards while charging your EV, or grow your business with EV drivers nearby.
      </p>

      {ref && (
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-4 py-2 mb-6">
          <p className="text-sm text-[#1877F2] font-medium">
            Referral code <span className="font-bold">{ref}</span> applied — you'll both earn $2.50 after your first charge
          </p>
        </div>
      )}

      <div className="w-full max-w-sm space-y-3">
        <button
          onClick={() => navigate('/')}
          className="w-full py-4 bg-[#1877F2] text-white font-semibold rounded-xl hover:bg-[#166FE5] active:scale-[0.98] transition-all"
        >
          I'm a Driver
        </button>

        <a
          href="https://merchant.nerava.network/claim"
          className="block w-full py-4 bg-white text-[#1877F2] font-semibold rounded-xl border-2 border-[#1877F2] hover:bg-blue-50 active:scale-[0.98] transition-all text-center"
        >
          I'm a Merchant
        </a>
      </div>

      <p className="text-xs text-[#65676B] mt-6 mb-2">or download the app</p>
      <div className="flex items-center justify-center gap-4 mt-0">
        <a
          href="https://apps.apple.com/us/app/nerava/id6759253986"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-sm text-[#65676B] hover:text-[#050505] transition-colors"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">
            <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
          </svg>
          App Store
        </a>
        <span className="text-gray-300">|</span>
        <a
          href="https://play.google.com/store/apps/details?id=network.nerava.app"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-sm text-[#65676B] hover:text-[#050505] transition-colors"
        >
          <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">
            <path d="M3 20.5v-17c0-.59.34-1.11.84-1.35L13.69 12l-9.85 9.85c-.5-.24-.84-.76-.84-1.35m13.81-5.38L6.05 21.34l8.49-8.49 2.27 2.27m3.35-4.31c.34.27.56.69.56 1.19s-.22.92-.56 1.19l-2.29 1.32-2.5-2.5 2.5-2.5 2.29 1.3M6.05 2.66l10.76 6.22-2.27 2.27-8.49-8.49z"/>
          </svg>
          Google Play
        </a>
      </div>

      <p className="text-xs text-[#65676B] mt-8">
        By continuing, you agree to Nerava's Terms of Service and Privacy Policy.
      </p>
    </div>
  )
}
