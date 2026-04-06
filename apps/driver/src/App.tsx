import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom'
import { DriverSessionProvider } from './contexts/DriverSessionContext'
import { FavoritesProvider } from './contexts/FavoritesContext'
import { DriverHome } from './components/DriverHome/DriverHome'
import { OnboardingGate } from './components/Onboarding/OnboardingGate'
import { OfflineBanner } from './components/shared/OfflineBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { SessionExpiredModal } from './components/SessionExpiredModal'
import { useViewportHeight } from './hooks/useViewportHeight'
import { useAutoRedeemReferral } from './hooks/useAutoRedeemReferral'

// Lazy-loaded route components for code splitting
const TeslaCallbackScreen = lazy(() => import('./components/TeslaLogin/TeslaCallbackScreen').then(m => ({ default: m.TeslaCallbackScreen })))
const TeslaConnectedScreen = lazy(() => import('./components/TeslaLogin/TeslaConnectedScreen').then(m => ({ default: m.TeslaConnectedScreen })))
const VehicleSelectScreen = lazy(() => import('./components/TeslaLogin/VehicleSelectScreen').then(m => ({ default: m.VehicleSelectScreen })))
const EVHome = lazy(() => import('./components/EVHome/EVHome').then(m => ({ default: m.EVHome })))
const EVOrderFlow = lazy(() => import('./components/EVOrder/EVOrderFlow').then(m => ({ default: m.EVOrderFlow })))
const PhoneCheckinScreen = lazy(() => import('./components/PhoneCheckin').then(m => ({ default: m.PhoneCheckinScreen })))
const WhileYouChargeScreen = lazy(() => import('./components/WhileYouCharge/WhileYouChargeScreen').then(m => ({ default: m.WhileYouChargeScreen })))
const MerchantDetailsScreen = lazy(() => import('./components/MerchantDetails/MerchantDetailsScreen').then(m => ({ default: m.MerchantDetailsScreen })))
const EarningsScreen = lazy(() => import('./components/Earnings/EarningsScreen').then(m => ({ default: m.EarningsScreen })))
const MerchantArrivalScreen = lazy(() => import('./components/EVArrival/MerchantArrivalScreen').then(m => ({ default: m.MerchantArrivalScreen })))
const PreChargingScreen = lazy(() => import('./components/PreCharging/PreChargingScreen').then(m => ({ default: m.PreChargingScreen })))
const ClaimDetailsScreen = lazy(() => import('./components/ClaimDetails/ClaimDetailsScreen').then(m => ({ default: m.ClaimDetailsScreen })))
const JoinPage = lazy(() => import('./pages/JoinPage'))

function NotFoundScreen() {
  const navigate = useNavigate()
  return (
    <div className="flex flex-col items-center justify-center h-screen bg-white px-6 text-center">
      <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mb-6">
        <svg className="w-8 h-8 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Page not found</h1>
      <p className="text-gray-500 mb-8">The page you're looking for doesn't exist or has been moved.</p>
      <button
        onClick={() => navigate('/')}
        className="px-6 py-3 bg-[#1877F2] text-white font-semibold rounded-xl hover:bg-[#166FE5] transition-colors"
      >
        Go Home
      </button>
    </div>
  )
}


function App() {
  useViewportHeight()
  useAutoRedeemReferral()

  // Set basename for React Router - Vite provides BASE_URL from base config
  // BASE_URL is '/app/' when built with VITE_PUBLIC_BASE=/app/, '/' in dev
  const basename = import.meta.env.BASE_URL || '/app'

  return (
    <ErrorBoundary>
    <FavoritesProvider>
      <DriverSessionProvider>
        <OfflineBanner />
        <BrowserRouter basename={basename}>
        <OnboardingGate>
          <Suspense fallback={<div className="flex items-center justify-center h-screen"><div className="w-8 h-8 border-4 border-[#1877F2] border-t-transparent rounded-full animate-spin" /></div>}>
          <Routes>
            {/* Tesla OAuth callback and vehicle selection */}
            <Route path="/tesla-callback" element={<TeslaCallbackScreen />} />
            <Route path="/tesla-connected" element={<TeslaConnectedScreen />} />
            <Route path="/select-vehicle" element={<VehicleSelectScreen />} />
            {/* Phone check-in route (from SMS link) */}
            <Route path="/s/:token" element={<PhoneCheckinScreen />} />
            {/* Main driver app route */}
            <Route path="/" element={<DriverHome />} />
            <Route path="/driver" element={<DriverHome />} />
            {/* EV-specific routes */}
            <Route path="/ev-home" element={<EVHome />} />
            <Route path="/ev-order" element={<EVOrderFlow />} />
            {/* Legacy routes for backward compatibility */}
            <Route path="/wyc" element={<WhileYouChargeScreen />} />
            <Route path="/pre-charging" element={<PreChargingScreen />} />
            {/* Phase 0 phone-first EV arrival flow */}
            <Route path="/m/:merchantId" element={<MerchantArrivalScreen />} />
            {/* Claim details (from wallet active claim card) */}
            <Route path="/claim/:sessionId" element={<ClaimDetailsScreen />} />
            {/* Earnings / transaction history */}
            <Route path="/earnings" element={<EarningsScreen />} />
            {/* Merchant details route */}
            <Route path="/merchant/:merchantId" element={<MerchantDetailsScreen />} />
            {/* Referral join route */}
            <Route path="/join" element={<JoinPage />} />
            {/* 404 catch-all */}
            <Route path="*" element={<NotFoundScreen />} />
          </Routes>
          </Suspense>
        </OnboardingGate>
      </BrowserRouter>
      <SessionExpiredModal />
    </DriverSessionProvider>
    </FavoritesProvider>
    </ErrorBoundary>
  )
}

export default App
