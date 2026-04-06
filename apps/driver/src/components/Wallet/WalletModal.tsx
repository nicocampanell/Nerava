import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, TrendingUp, TrendingDown, Clock, Loader2, ExternalLink, CheckCircle, AlertCircle, Gift } from 'lucide-react'
import {
  createStripeAccount,
  createStripeAccountLink,
  requestWithdrawal,
  checkStripeStatus,
  useActiveExclusive,
  useReferralStats,
} from '../../services/api'
import { BankLinkFlow } from './BankLinkFlow'
import { ClaimActiveCard } from './ClaimActiveCard'

export interface Transaction {
  id: string
  type: 'credit' | 'withdrawal'
  description: string
  amount: number
  timestamp: string
}

interface WalletModalProps {
  isOpen: boolean
  onClose: () => void
  balance: number
  pendingBalance: number
  stripeOnboardingComplete: boolean
  recentTransactions: Transaction[]
  onBalanceChanged: () => void
  userEmail?: string
  payoutProvider?: string  // "stripe" or "dwolla"
  bankVerified?: boolean
  asPage?: boolean  // Render as full page instead of modal overlay
}

const MINIMUM_WITHDRAWAL_CENTS = 100 // $1 minimum
const FEE_THRESHOLD_CENTS = 2000 // $20 — withdrawals below this incur a processing fee
const STRIPE_FIXED_FEE_CENTS = 25 // $0.25
const STRIPE_PERCENT_FEE = 0.0025 // 0.25%

function calcWithdrawalFee(amountCents: number): number {
  if (amountCents >= FEE_THRESHOLD_CENTS) return 0
  return STRIPE_FIXED_FEE_CENTS + Math.round(amountCents * STRIPE_PERCENT_FEE)
}

type WithdrawStep = 'idle' | 'amount' | 'confirming' | 'processing' | 'success' | 'error'

export function WalletModal({
  isOpen,
  onClose,
  balance,
  pendingBalance,
  stripeOnboardingComplete: initialStripeComplete,
  recentTransactions,
  onBalanceChanged,
  userEmail,
  payoutProvider = 'stripe',
  bankVerified = false,
  asPage = false,
}: WalletModalProps) {
  const navigate = useNavigate()
  const [withdrawStep, setWithdrawStep] = useState<WithdrawStep>('idle')
  const [withdrawAmount, setWithdrawAmount] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [connectingBank, setConnectingBank] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [stripeOnboardingComplete, setStripeOnboardingComplete] = useState(initialStripeComplete)
  const [checkingStripeStatus, setCheckingStripeStatus] = useState(false)
  const openedStripeRef = useRef(false)

  // Sync prop changes
  useEffect(() => {
    setStripeOnboardingComplete(initialStripeComplete)
  }, [initialStripeComplete])

  // When the app comes back to foreground after opening Stripe in Safari,
  // check if onboarding is now complete
  useEffect(() => {
    if (!isOpen) return

    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && openedStripeRef.current) {
        openedStripeRef.current = false
        setCheckingStripeStatus(true)
        checkStripeStatus()
          .then((status) => {
            if (status.onboarding_complete) {
              setStripeOnboardingComplete(true)
              onBalanceChanged() // Refresh wallet data
            }
          })
          .catch(() => {})
          .finally(() => setCheckingStripeStatus(false))
      }
    }

    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [isOpen, onBalanceChanged])

  // Referral stats
  const { data: referralStats } = useReferralStats()

  // Active claim data
  const { data: activeExclusiveData } = useActiveExclusive()
  const activeClaim = activeExclusiveData?.exclusive_session ?? null
  const [claimRemaining, setClaimRemaining] = useState(0)

  useEffect(() => {
    if (!activeClaim) { setClaimRemaining(0); return }
    const update = () => {
      const expiresAt = new Date(activeClaim.expires_at).getTime()
      setClaimRemaining(Math.max(0, Math.floor((expiresAt - Date.now()) / 1000)))
    }
    update()
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [activeClaim])

  if (!isOpen) return null

  const formatCurrency = (cents: number) => `$${(cents / 100).toFixed(2)}`

  const formatTimeAgo = (timestamp: string) => {
    const date = new Date(timestamp)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
    const diffDays = Math.floor(diffHours / 24)
    if (diffHours < 1) return 'Just now'
    if (diffHours < 24) return `${diffHours}h ago`
    return `${diffDays}d ago`
  }

  const canWithdraw = balance >= MINIMUM_WITHDRAWAL_CENTS

  const handleConnectBank = async () => {
    setConnectingBank(true)
    setErrorMessage('')
    try {
      const appUrl = window.location.origin
      // Create Express account first
      await createStripeAccount(userEmail || '')
      // Then get the onboarding link
      const { url } = await createStripeAccountLink(
        `${appUrl}/?stripe_return=true`,
        `${appUrl}/?stripe_refresh=true`,
      )
      // Open in Safari via native bridge — Google blocks OAuth in WKWebView
      if ((window as any).neravaNative?.openExternalUrl) {
        openedStripeRef.current = true
        ;(window as any).neravaNative.openExternalUrl(url)
        setConnectingBank(false)
      } else {
        window.location.href = url
      }
    } catch (e: any) {
      setErrorMessage(e?.message || 'Failed to start card setup')
      setConnectingBank(false)
    }
  }

  const handleStartWithdraw = () => {
    setWithdrawAmount((balance / 100).toFixed(2))
    setWithdrawStep('amount')
    setErrorMessage('')
  }

  const parsedAmountCents = Math.round(parseFloat(withdrawAmount || '0') * 100)
  const feeCents = calcWithdrawalFee(parsedAmountCents)
  const totalDebit = parsedAmountCents + feeCents
  const amountValid = parsedAmountCents >= MINIMUM_WITHDRAWAL_CENTS && totalDebit <= balance

  const handleConfirmWithdraw = async () => {
    if (!amountValid) return
    setSubmitting(true)
    setWithdrawStep('processing')
    setErrorMessage('')
    try {
      await requestWithdrawal(parsedAmountCents)
      setWithdrawStep('success')
      onBalanceChanged()
    } catch (e: any) {
      const msg = e?.message || 'Withdrawal failed'
      // If backend says account not set up, redirect to onboarding
      if (msg.includes('not set up') || msg.includes('not complete')) {
        setWithdrawStep('idle')
        handleConnectBank()
        return
      }
      setErrorMessage(msg)
      setWithdrawStep('error')
    } finally {
      setSubmitting(false)
    }
  }

  const resetWithdraw = () => {
    setWithdrawStep('idle')
    setWithdrawAmount('')
    setErrorMessage('')
  }

  const outerClass = asPage
    ? "flex flex-col h-full bg-white"
    : "fixed inset-0 z-[3000] bg-black/50 flex items-end justify-center sm:items-center"
  const innerClass = asPage
    ? "flex-1 flex flex-col overflow-hidden"
    : "bg-white w-full max-w-md rounded-t-3xl sm:rounded-3xl max-h-[90vh] overflow-hidden flex flex-col"

  return (
    <div className={outerClass}>
      <div
        className={innerClass}
        style={asPage ? undefined : { paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-[#E4E6EB]">
          <div className="flex items-center gap-2">
            <svg className="w-6 h-6 text-[#1877F2]" viewBox="0 0 24 24" fill="currentColor">
              <path d="M21 18v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1" />
              <path d="M16 12h5v2h-5a1 1 0 0 1 0-2z" />
            </svg>
            <span className="text-lg font-semibold">My Wallet</span>
          </div>
          {!asPage && (
            <button
              onClick={() => { resetWithdraw(); onClose() }}
              className="p-2 hover:bg-gray-100 rounded-full transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Balance Card */}
          <div className="bg-[#1877F2] rounded-2xl p-5 text-white">
            <p className="text-sm opacity-90">Your Balance</p>
            <p className="text-4xl font-bold mt-1">{formatCurrency(balance)}</p>
            {pendingBalance > 0 && (
              <p className="text-sm opacity-80 mt-1">+ {formatCurrency(pendingBalance)} pending</p>
            )}

            {/* Withdraw / Connect Bank Flow */}
            {withdrawStep === 'idle' && (
              <>
                {payoutProvider === 'dwolla' ? (
                  /* Dwolla: bank linked via Plaid */
                  bankVerified ? (
                    <button
                      onClick={handleStartWithdraw}
                      disabled={!canWithdraw}
                      className="w-full mt-4 py-3 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                    >
                      Withdraw to Card
                    </button>
                  ) : (
                    <div className="mt-4 bg-white/10 rounded-xl p-3">
                      <BankLinkFlow onLinkComplete={onBalanceChanged} />
                    </div>
                  )
                ) : (
                  /* Stripe: existing flow */
                  stripeOnboardingComplete ? (
                    <button
                      onClick={handleStartWithdraw}
                      disabled={!canWithdraw}
                      className="w-full mt-4 py-3 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                    >
                      Withdraw to Card
                    </button>
                  ) : (
                    <button
                      onClick={handleConnectBank}
                      disabled={connectingBank || checkingStripeStatus}
                      className="w-full mt-4 py-3 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
                    >
                      {connectingBank || checkingStripeStatus ? (
                        <><Loader2 className="w-4 h-4 animate-spin" /> {checkingStripeStatus ? 'Checking...' : 'Setting up...'}</>
                      ) : (
                        <><ExternalLink className="w-4 h-4" /> Connect Your Card</>
                      )}
                    </button>
                  )
                )}
                <p className="text-center text-sm opacity-80 mt-2">
                  {(payoutProvider === 'dwolla' ? bankVerified : stripeOnboardingComplete)
                    ? `Minimum ${formatCurrency(MINIMUM_WITHDRAWAL_CENTS)}`
                    : 'Required for withdrawals'}
                </p>
              </>
            )}

            {/* Amount Input */}
            {withdrawStep === 'amount' && (
              <div className="mt-4 space-y-3">
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[#1877F2] font-semibold text-lg">$</span>
                  <input
                    type="number"
                    step="0.01"
                    min={MINIMUM_WITHDRAWAL_CENTS / 100}
                    max={balance / 100}
                    value={withdrawAmount}
                    onChange={(e) => setWithdrawAmount(e.target.value)}
                    className="w-full py-3 pl-8 pr-4 bg-white text-[#1877F2] font-semibold rounded-xl text-lg focus:outline-none focus:ring-2 focus:ring-white/50"
                    autoFocus
                  />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={resetWithdraw}
                    className="flex-1 py-2.5 bg-white/20 text-white font-medium rounded-xl hover:bg-white/30 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleConfirmWithdraw}
                    disabled={!amountValid || submitting}
                    className="flex-1 py-2.5 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-1"
                  >
                    {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                    Confirm
                  </button>
                </div>
                {feeCents > 0 && parsedAmountCents >= MINIMUM_WITHDRAWAL_CENTS && (
                  <p className="text-center text-sm opacity-90">
                    {formatCurrency(feeCents)} processing fee &middot; {formatCurrency(totalDebit)} total from balance
                  </p>
                )}
                {!amountValid && withdrawAmount && (
                  <p className="text-center text-sm opacity-80">
                    {parsedAmountCents < MINIMUM_WITHDRAWAL_CENTS
                      ? `Minimum ${formatCurrency(MINIMUM_WITHDRAWAL_CENTS)}`
                      : totalDebit > balance
                        ? `Insufficient balance (need ${formatCurrency(totalDebit)} incl. fee)`
                        : `Maximum ${formatCurrency(balance)}`}
                  </p>
                )}
              </div>
            )}

            {/* Processing */}
            {withdrawStep === 'processing' && (
              <div className="mt-4 flex flex-col items-center gap-2 py-3">
                <Loader2 className="w-6 h-6 animate-spin" />
                <p className="text-sm opacity-90">Processing withdrawal...</p>
              </div>
            )}

            {/* Success */}
            {withdrawStep === 'success' && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-col items-center gap-2 py-2">
                  <CheckCircle className="w-8 h-8" />
                  <p className="font-semibold">Withdrawal submitted</p>
                  <p className="text-sm opacity-80">
                    {formatCurrency(parsedAmountCents)} is on its way to your card
                    {feeCents > 0 && ` (${formatCurrency(feeCents)} fee applied)`}
                  </p>
                </div>
                <button
                  onClick={resetWithdraw}
                  className="w-full py-2.5 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 transition-colors"
                >
                  Done
                </button>
              </div>
            )}

            {/* Error */}
            {withdrawStep === 'error' && (
              <div className="mt-4 space-y-3">
                <div className="flex flex-col items-center gap-2 py-2">
                  <AlertCircle className="w-8 h-8" />
                  <p className="font-semibold">Withdrawal failed</p>
                  <p className="text-sm opacity-80 text-center">{errorMessage}</p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={resetWithdraw}
                    className="flex-1 py-2.5 bg-white/20 text-white font-medium rounded-xl hover:bg-white/30 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleConfirmWithdraw}
                    className="flex-1 py-2.5 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-gray-50 transition-colors"
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Active Claim Card */}
          {activeClaim && claimRemaining > 0 && (
            <ClaimActiveCard session={activeClaim} remainingSeconds={claimRemaining} onTap={onClose} />
          )}

          {/* Referral Stats Card */}
          {referralStats && referralStats.total_referrals > 0 && (
            <div className="bg-blue-50 rounded-xl p-4 border border-blue-100">
              <div className="flex items-center gap-2 mb-3">
                <Gift className="w-5 h-5 text-[#1877F2]" />
                <span className="font-semibold text-[#1877F2]">Referral Rewards</span>
              </div>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div>
                  <p className="text-xl font-bold text-gray-900">{referralStats.total_referrals}</p>
                  <p className="text-xs text-[#65676B]">Friends joined</p>
                </div>
                <div>
                  <p className="text-xl font-bold text-green-600">{formatCurrency(referralStats.total_earned_cents)}</p>
                  <p className="text-xs text-[#65676B]">Earned</p>
                </div>
                <div>
                  <p className="text-xl font-bold text-amber-500">{referralStats.pending_count}</p>
                  <p className="text-xs text-[#65676B]">Pending</p>
                </div>
              </div>
            </div>
          )}

          {/* Error banner (for connect bank failures) */}
          {errorMessage && withdrawStep === 'idle' && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-sm text-red-700">
              {errorMessage}
            </div>
          )}

          {/* How it works */}
          <div className="bg-gray-50 rounded-xl p-4">
            <p className="font-semibold mb-3">How it works:</p>
            <ul className="space-y-2 text-sm text-[#65676B]">
              <li className="flex items-start gap-2">
                <span className="text-[#1877F2]">•</span>
                Earn rewards from charging sessions
              </li>
              <li className="flex items-start gap-2">
                <span className="text-[#1877F2]">•</span>
                Sponsored incentives at eligible locations
              </li>
              <li className="flex items-start gap-2">
                <span className="text-[#1877F2]">•</span>
                Withdraw anytime to your card
              </li>
            </ul>
          </div>

          {/* Recent Activity */}
          {recentTransactions.length === 0 && (
            <p className="text-sm text-[#65676B] text-center py-4">
              Your charging rewards will appear here
            </p>
          )}
          {recentTransactions.length > 0 && (
            <div>
              <h3 className="font-semibold mb-3">Recent Activity</h3>
              <div className="space-y-3">
                {recentTransactions.map((tx) => (
                  <div key={tx.id} className="flex items-center gap-3">
                    <div
                      className={`w-10 h-10 rounded-full flex items-center justify-center ${
                        tx.type === 'credit' ? 'bg-green-100' : 'bg-red-100'
                      }`}
                    >
                      {tx.type === 'credit' ? (
                        <TrendingUp className="w-5 h-5 text-green-600" />
                      ) : (
                        <TrendingDown className="w-5 h-5 text-red-600" />
                      )}
                    </div>
                    <div className="flex-1">
                      <p className="font-medium">{tx.description}</p>
                      <p className="text-xs text-[#65676B] flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatTimeAgo(tx.timestamp)}
                      </p>
                    </div>
                    <span
                      className={`font-semibold ${
                        tx.type === 'credit' ? 'text-green-600' : 'text-red-600'
                      }`}
                    >
                      {tx.type === 'credit' ? '+' : '-'}
                      {formatCurrency(tx.amount)}
                    </span>
                  </div>
                ))}
              </div>
              <button
                onClick={() => { onClose(); navigate('/earnings') }}
                className="mt-3 text-sm text-[#1877F2] font-medium hover:underline w-full text-center"
              >
                View all earnings
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
