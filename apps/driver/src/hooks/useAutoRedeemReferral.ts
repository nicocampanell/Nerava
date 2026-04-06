import { useEffect } from 'react'
import { getReferralCode, clearReferralCode } from '../utils/referralStorage'
import { redeemReferralCode } from '../services/api'

/**
 * Listens for successful auth events and auto-redeems any stored referral code.
 * Covers all auth paths: phone OTP, email OTP, Google, Apple, Tesla.
 */
export function useAutoRedeemReferral() {
  useEffect(() => {
    const handleAuthChanged = async () => {
      const code = getReferralCode()
      if (!code) return

      try {
        await redeemReferralCode(code)
        clearReferralCode()
      } catch {
        // Silently fail — code may be invalid, expired, or self-referral.
        // Clear anyway to avoid retrying on every auth event.
        clearReferralCode()
      }
    }

    window.addEventListener('nerava:auth-changed', handleAuthChanged)
    return () => window.removeEventListener('nerava:auth-changed', handleAuthChanged)
  }, [])
}
