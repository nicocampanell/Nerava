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
    <div className="min-h-screen bg-white flex flex-col items-center justify-center px-6 text-center">
      <img src="/nerava-logo.png" alt="Nerava" className="w-24 h-24 mb-6" />

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

      <p className="text-xs text-[#65676B] mt-8">
        By continuing, you agree to Nerava's Terms of Service and Privacy Policy.
      </p>
    </div>
  )
}
