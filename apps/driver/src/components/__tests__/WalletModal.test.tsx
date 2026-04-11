import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { WalletModal } from '../Wallet/WalletModal'
import type { Transaction } from '../Wallet/WalletModal'

// Mock the API module
vi.mock('../../services/api', () => ({
  createStripeAccount: vi.fn(),
  createStripeAccountLink: vi.fn(),
  requestWithdrawal: vi.fn(),
  checkStripeStatus: vi.fn().mockResolvedValue({ onboarding_complete: true }),
  useReferralStats: () => ({ data: null, isLoading: false, error: null }),
  useActiveExclusive: () => ({ data: null, isLoading: false }),
}))

import {
  createStripeAccount,
  createStripeAccountLink,
  requestWithdrawal,
} from '../../services/api'

const mockedCreateStripeAccount = vi.mocked(createStripeAccount)
const mockedCreateStripeAccountLink = vi.mocked(createStripeAccountLink)
const mockedRequestWithdrawal = vi.mocked(requestWithdrawal)

describe('WalletModal', () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    balance: 5000, // $50.00
    pendingBalance: 200, // $2.00
    stripeOnboardingComplete: true,
    recentTransactions: [] as Transaction[],
    onBalanceChanged: vi.fn(),
    userEmail: 'test@example.com',
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // --- Test 1: Renders balance and pending balance ---
  it('displays formatted balance and pending amount', () => {
    render(<MemoryRouter><WalletModal {...defaultProps} /></MemoryRouter>)

    expect(screen.getByText('$50.00')).toBeInTheDocument()
    expect(screen.getByText('+ $2.00 pending')).toBeInTheDocument()
    expect(screen.getByText('My Wallet')).toBeInTheDocument()
  })

  // --- Test 2: Shows "Connect Your Bank" button when Stripe not onboarded ---
  it('shows Connect Your Bank button when stripeOnboardingComplete is false', () => {
    render(
      <MemoryRouter>
        <WalletModal
          {...defaultProps}
          stripeOnboardingComplete={false}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('Connect Your Bank')).toBeInTheDocument()
    expect(screen.queryByText('Withdraw to Bank')).not.toBeInTheDocument()
  })

  // --- Test 3: Shows "Withdraw to Bank" button when Stripe is onboarded ---
  it('shows Withdraw to Bank button when Stripe onboarding is complete', () => {
    render(<MemoryRouter><WalletModal {...defaultProps} /></MemoryRouter>)

    expect(screen.getByText('Withdraw to Bank')).toBeInTheDocument()
    expect(screen.queryByText('Connect Your Bank')).not.toBeInTheDocument()
  })

  // --- Test 4: Successful withdrawal flow ---
  it('completes withdrawal flow: click Withdraw -> enter amount -> Confirm -> success', async () => {
    const user = userEvent.setup()

    mockedRequestWithdrawal.mockResolvedValueOnce({
      payout_id: 'po_123',
      status: 'pending',
      amount_cents: 5000,
    })

    render(<MemoryRouter><WalletModal {...defaultProps} /></MemoryRouter>)

    // Click Withdraw to Bank
    await user.click(screen.getByText('Withdraw to Bank'))

    // Amount input should appear pre-filled with full balance
    const input = screen.getByRole('spinbutton')
    expect(input).toBeInTheDocument()
    expect((input as HTMLInputElement).value).toBe('50.00')

    // Click Confirm
    await user.click(screen.getByText('Confirm'))

    // Should show processing then success
    await waitFor(() => {
      expect(screen.getByText('Withdrawal submitted')).toBeInTheDocument()
    })

    expect(screen.getByText('$50.00 is on its way to your bank')).toBeInTheDocument()
    expect(mockedRequestWithdrawal).toHaveBeenCalledWith(5000)
    expect(defaultProps.onBalanceChanged).toHaveBeenCalledTimes(1)
  })

  // --- Test 5: Withdrawal error shows error state and retry button ---
  it('shows error state with Retry button when withdrawal fails', async () => {
    const user = userEvent.setup()

    mockedRequestWithdrawal.mockRejectedValueOnce(new Error('Insufficient balance'))

    render(<MemoryRouter><WalletModal {...defaultProps} /></MemoryRouter>)

    await user.click(screen.getByText('Withdraw to Bank'))
    await user.click(screen.getByText('Confirm'))

    await waitFor(() => {
      expect(screen.getByText('Withdrawal failed')).toBeInTheDocument()
    })

    expect(screen.getByText('Insufficient balance')).toBeInTheDocument()
    expect(screen.getByText('Retry')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  // --- Test 6: Recent transactions render correctly ---
  it('renders recent transaction items with correct formatting', () => {
    const transactions: Transaction[] = [
      {
        id: 'tx-1',
        type: 'credit',
        description: 'Charging reward',
        amount: 150,
        timestamp: new Date().toISOString(),
      },
      {
        id: 'tx-2',
        type: 'withdrawal',
        description: 'Bank withdrawal',
        amount: 2000,
        timestamp: new Date(Date.now() - 86400000).toISOString(), // 1 day ago
      },
    ]

    render(
      <MemoryRouter>
        <WalletModal
          {...defaultProps}
          recentTransactions={transactions}
        />
      </MemoryRouter>
    )

    expect(screen.getByText('Recent Activity')).toBeInTheDocument()
    expect(screen.getByText('Charging reward')).toBeInTheDocument()
    expect(screen.getByText('Bank withdrawal')).toBeInTheDocument()
    expect(screen.getByText('+$1.50')).toBeInTheDocument()
    expect(screen.getByText('-$20.00')).toBeInTheDocument()
  })
})
