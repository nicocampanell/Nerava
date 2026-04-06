/**
 * Referral code storage with 7-day expiry.
 * Replaces sessionStorage (which is lost on tab close) with localStorage + TTL.
 */

const STORAGE_KEY = 'nerava_referral_code'
const EXPIRY_MS = 7 * 24 * 60 * 60 * 1000 // 7 days

interface StoredReferral {
  code: string
  expires: number
}

export function saveReferralCode(code: string): void {
  const data: StoredReferral = {
    code,
    expires: Date.now() + EXPIRY_MS,
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
}

export function getReferralCode(): string | null {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (!raw) return null

  try {
    const data: StoredReferral = JSON.parse(raw)
    if (Date.now() > data.expires) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }
    return data.code
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export function clearReferralCode(): void {
  localStorage.removeItem(STORAGE_KEY)
}
