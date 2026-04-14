import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Heart, LogOut, ChevronRight, X, User, Mail, Car, LogIn, Bell, BellOff, Ruler, ExternalLink, HelpCircle, Zap, Trash2, AlertTriangle, MessageSquare, Loader2, Activity, Pencil, Phone, Check } from 'lucide-react'
import { useFavorites } from '../../contexts/FavoritesContext'
import { ShareNerava } from './ShareNerava'
import { LoginModal } from './LoginModal'
import { useQueryClient } from '@tanstack/react-query'
import { useTeslaStatus, useReferralCode, useChargerFavorites, removeVehicle, api } from '../../services/api'
import { ProfileCompletionCard } from './ProfileCompletionCard'
import { AccountStatsCard } from './AccountStatsCard'
import { VehicleConnectOptions } from './VehicleConnectOptions'

interface UserProfile {
  name?: string
  email?: string
  phone?: string
  vehicle?: string
  memberSince?: string
}

const NOTIFICATIONS_KEY = 'nerava_notifications_enabled'
const DISTANCE_UNIT_KEY = 'nerava_distance_unit'

interface AccountPageProps {
  onClose: () => void
  onViewActivity?: () => void
  onViewVehicle?: () => void
  onChargerSelect?: (chargerId: string) => void
}

export function AccountPage({ onClose, onViewActivity, onViewVehicle, onChargerSelect }: AccountPageProps) {
  const { favorites, favoriteDetails, toggleFavorite, getMerchantName } = useFavorites()
  const [userProfile, setUserProfile] = useState<UserProfile | null>(null)
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [showFavoritesList, setShowFavoritesList] = useState(false)
  const [favoritesTab, setFavoritesTab] = useState<'merchants' | 'chargers'>('merchants')
  const [showShareNerava, setShowShareNerava] = useState(false)
  const [showLoginModal, setShowLoginModal] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [showFeedback, setShowFeedback] = useState(false)
  const [feedbackText, setFeedbackText] = useState('')
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false)
  const [feedbackSent, setFeedbackSent] = useState(false)
  // Test push state removed for production
  const [editingProfile, setEditingProfile] = useState(false)
  const [editName, setEditName] = useState('')
  const [editEmail, setEditEmail] = useState('')
  const [profileSaving, setProfileSaving] = useState(false)
  const [showRemoveVehicle, setShowRemoveVehicle] = useState(false)
  const [removeVehicleLoading, setRemoveVehicleLoading] = useState(false)
  const [showConnectOptions, setShowConnectOptions] = useState(false)
  const [smartcarToast, setSmartcarToast] = useState<string | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  // Debug mock charging (restricted to admin account only)
  const [mockCharging, setMockCharging] = useState(() => localStorage.getItem('debug_mock_charging') === 'true')
  const isTestUser = (() => {
    try {
      const u = JSON.parse(localStorage.getItem('nerava_user') || '{}')
      return u.public_id === 'd537cd5a-f13b-4a12-a757-04b1b35d0749'
    } catch { return false }
  })()

  // Preferences state
  const [notificationsEnabled, setNotificationsEnabled] = useState(() => {
    return localStorage.getItem(NOTIFICATIONS_KEY) !== 'false'
  })
  const [distanceUnit, setDistanceUnit] = useState<'miles' | 'km'>(() => {
    return (localStorage.getItem(DISTANCE_UNIT_KEY) as 'miles' | 'km') || 'miles'
  })

  // Tesla connection status
  const { data: teslaStatus, isLoading: teslaLoading } = useTeslaStatus()

  // Charger favorites
  const { data: chargerFavData } = useChargerFavorites()

  const checkAuth = () => {
    const token = localStorage.getItem('access_token')
    const storedUser = localStorage.getItem('nerava_user')

    if (token && storedUser) {
      setIsAuthenticated(true)
      try {
        const user = JSON.parse(storedUser)
        setUserProfile({
          name: user.name || 'EV Driver',
          email: user.email,
          phone: user.phone ? `***-***-${user.phone.slice(-4)}` : undefined,
          vehicle: user.vehicle || 'Tesla Owner',
          memberSince: user.created_at ? new Date(user.created_at).toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : 'January 2024',
        })
      } catch {
        setUserProfile({ name: 'EV Driver' })
      }
    } else {
      setIsAuthenticated(false)
      setUserProfile(null)
    }
  }

  useEffect(() => {
    checkAuth()
  }, [])

  const handleLogout = () => {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('nerava_user')
    window.location.reload()
  }

  const handleViewMerchant = (merchantId: string) => {
    onClose()
    navigate(`/merchant/${merchantId}`)
  }

  const handleRemoveFavorite = async (e: React.MouseEvent, merchantId: string) => {
    e.stopPropagation()
    await toggleFavorite(merchantId)
  }

  // Get display name from favorites context (with fallback formatting)
  const formatMerchantId = (id: string) => getMerchantName(id)

  // Fetch unique referral code from backend
  const { data: referralData } = useReferralCode()
  const referralCode = referralData?.code || `NERAVA-${new Date().getFullYear()}`

  const handleLoginSuccess = () => {
    checkAuth()
    setShowLoginModal(false)
  }

  const handleStartEditProfile = () => {
    setEditName(userProfile?.name === 'EV Driver' ? '' : userProfile?.name || '')
    setEditEmail(userProfile?.email || '')
    setEditingProfile(true)
  }

  const handleSaveProfile = async () => {
    setProfileSaving(true)
    try {
      const updates: { email?: string; display_name?: string } = {}
      if (editName.trim()) updates.display_name = editName.trim()
      if (editEmail.trim()) updates.email = editEmail.trim()

      if (Object.keys(updates).length > 0) {
        await api.put('/v1/account/profile', updates)
        const stored = localStorage.getItem('nerava_user')
        if (stored) {
          const user = JSON.parse(stored)
          if (updates.display_name) user.name = updates.display_name
          if (updates.email) user.email = updates.email
          localStorage.setItem('nerava_user', JSON.stringify(user))
        }
        setUserProfile(prev => prev ? {
          ...prev,
          name: updates.display_name || prev.name,
          email: updates.email || prev.email,
        } : prev)
      }
      setEditingProfile(false)
    } catch (e) {
      console.error('Failed to update profile:', e)
    } finally {
      setProfileSaving(false)
    }
  }

  const handleToggleNotifications = useCallback(async () => {
    const newValue = !notificationsEnabled
    setNotificationsEnabled(newValue)
    localStorage.setItem(NOTIFICATIONS_KEY, String(newValue))
    // Sync to backend
    try {
      await api.put('/v1/account/preferences', { notifications_enabled: newValue })
    } catch {
      // Revert on failure
      setNotificationsEnabled(!newValue)
      localStorage.setItem(NOTIFICATIONS_KEY, String(!newValue))
    }
  }, [notificationsEnabled])

  const handleToggleDistanceUnit = () => {
    const newValue = distanceUnit === 'miles' ? 'km' : 'miles'
    setDistanceUnit(newValue)
    localStorage.setItem(DISTANCE_UNIT_KEY, newValue)
  }

  const handleConnectTesla = async () => {
    setShowConnectOptions(false)
    try {
      const { api } = await import('../../services/api')
      const response = await api.get<{ authorization_url: string }>('/v1/auth/tesla/connect')
      window.location.href = response.authorization_url
    } catch (e) {
      console.error('Failed to start Tesla connection:', e)
    }
  }

  const handleConnectSmartcar = async () => {
    setShowConnectOptions(false)
    try {
      const { api } = await import('../../services/api')
      const response = await api.get<{ url: string }>('/v1/ev/connect')
      window.location.href = response.url
    } catch {
      setSmartcarToast('Smartcar integration coming soon')
    }
  }

  const handleRemoveVehicle = async () => {
    if (!teslaStatus?.vehicle_id) return
    setRemoveVehicleLoading(true)
    try {
      await removeVehicle(teslaStatus.vehicle_id)
      setShowRemoveVehicle(false)
      queryClient.invalidateQueries({ queryKey: ['tesla-status'] })
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : 'Failed to remove vehicle'
      alert(message)
    } finally {
      setRemoveVehicleLoading(false)
    }
  }

  const handleDeleteAccount = async () => {
    if (deleteConfirmText !== 'DELETE') return
    setDeleteLoading(true)
    try {
      await api.post('/v1/account/delete', { confirmation: 'DELETE' })
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('nerava_user')
      window.location.reload()
    } catch {
      setDeleteLoading(false)
    }
  }

  const handleSubmitFeedback = async () => {
    if (!feedbackText.trim()) return
    setFeedbackSubmitting(true)
    try {
      await api.post('/v1/account/feedback', { message: feedbackText.trim() })
      setFeedbackSent(true)
      setTimeout(() => {
        setShowFeedback(false)
        setFeedbackText('')
        setFeedbackSent(false)
      }, 2000)
    } catch {
      // Silent fail
    } finally {
      setFeedbackSubmitting(false)
    }
  }


  if (showShareNerava) {
    return <ShareNerava onClose={() => setShowShareNerava(false)} referralCode={referralCode} />
  }

  return (
    <>
      <LoginModal
        isOpen={showLoginModal}
        onClose={() => setShowLoginModal(false)}
        onSuccess={handleLoginSuccess}
      />
    <div className="flex-1 flex flex-col bg-white min-h-0">
      <header className="flex items-center p-4 border-b border-[#E4E6EB]">
        {showFavoritesList ? (
          <button onClick={() => setShowFavoritesList(false)} className="p-2 -ml-2 hover:bg-gray-100 rounded-full">
            <ArrowLeft className="w-6 h-6" />
          </button>
        ) : (
          <div className="w-10" />
        )}
        <h1 className="flex-1 text-center font-semibold text-lg">
          {showFavoritesList ? 'Favorites' : 'Account'}
        </h1>
        <div className="w-10" />
      </header>

      {showFavoritesList ? (
        <div className="flex-1 overflow-y-auto flex flex-col">
          {/* Tabs */}
          <div className="flex border-b border-[#E4E6EB]">
            <button
              onClick={() => setFavoritesTab('merchants')}
              className={`flex-1 py-3 text-sm font-medium text-center transition-colors ${
                favoritesTab === 'merchants'
                  ? 'text-[#1877F2] border-b-2 border-[#1877F2]'
                  : 'text-[#65676B]'
              }`}
            >
              Merchants ({favorites.size})
            </button>
            <button
              onClick={() => setFavoritesTab('chargers')}
              className={`flex-1 py-3 text-sm font-medium text-center transition-colors ${
                favoritesTab === 'chargers'
                  ? 'text-[#1877F2] border-b-2 border-[#1877F2]'
                  : 'text-[#65676B]'
              }`}
            >
              Chargers ({chargerFavData?.favorites?.length || 0})
            </button>
          </div>

          {favoritesTab === 'merchants' ? (
            favorites.size === 0 ? (
              <div className="flex flex-col items-center justify-center flex-1 text-gray-500 p-8">
                <Heart className="w-12 h-12 mb-4 text-gray-300" />
                <p className="text-center">No favorite merchants yet</p>
                <p className="text-sm text-center mt-2">Tap the heart icon on any merchant to save it here</p>
              </div>
            ) : (
              <div className="divide-y">
                {Array.from(favorites).map((merchantId) => {
                  const details = favoriteDetails.get(merchantId)
                  return (
                    <div
                      key={merchantId}
                      onClick={() => handleViewMerchant(merchantId)}
                      className="flex items-center p-4 hover:bg-gray-50 active:bg-gray-100 cursor-pointer"
                    >
                      {details?.photo_url ? (
                        <img
                          src={details.photo_url}
                          alt={details.name}
                          className="w-10 h-10 rounded-full object-cover mr-3"
                        />
                      ) : (
                        <div className="w-10 h-10 bg-[#1877F2]/10 rounded-full flex items-center justify-center mr-3">
                          <Heart className="w-5 h-5 text-[#1877F2] fill-current" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="font-medium truncate">{formatMerchantId(merchantId)}</p>
                        <p className="text-sm text-gray-500">{details?.category || 'Tap to view details'}</p>
                      </div>
                      <button
                        onClick={(e) => handleRemoveFavorite(e, merchantId)}
                        className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-full transition-colors"
                      >
                        <X className="w-5 h-5" />
                      </button>
                      <ChevronRight className="w-5 h-5 text-gray-400 ml-1" />
                    </div>
                  )
                })}
              </div>
            )
          ) : (
            !chargerFavData?.favorites?.length ? (
              <div className="flex flex-col items-center justify-center flex-1 text-gray-500 p-8">
                <Zap className="w-12 h-12 mb-4 text-gray-300" />
                <p className="text-center">No favorite chargers yet</p>
                <p className="text-sm text-center mt-2">Tap the heart icon on any charger to save it here</p>
              </div>
            ) : (
              <div className="divide-y">
                {chargerFavData.favorites.map((chargerId) => (
                  <div
                    key={chargerId}
                    onClick={() => { onClose(); onChargerSelect?.(chargerId) }}
                    className="flex items-center p-4 hover:bg-gray-50 active:bg-gray-100 cursor-pointer"
                  >
                    <div className="w-10 h-10 bg-green-50 rounded-full flex items-center justify-center mr-3">
                      <Zap className="w-5 h-5 text-green-600" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">Charger</p>
                      <p className="text-sm text-gray-500">Tap to view details</p>
                    </div>
                    <ChevronRight className="w-5 h-5 text-gray-400" />
                  </div>
                ))}
              </div>
            )
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-4 pb-8 space-y-4">
          {/* Profile Card or Sign In */}
          {isAuthenticated && userProfile ? (
            <div className="bg-blue-50 rounded-2xl p-5 border border-blue-100">
              {editingProfile ? (
                <>
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="font-semibold text-[#050505]">Edit Profile</h3>
                    <button onClick={() => setEditingProfile(false)} className="p-1 hover:bg-blue-100 rounded-full">
                      <X className="w-5 h-5 text-[#65676B]" />
                    </button>
                  </div>
                  <div className="space-y-3">
                    <div>
                      <label className="text-xs font-medium text-[#65676B] mb-1 block">Display Name</label>
                      <input
                        type="text"
                        value={editName}
                        onChange={e => setEditName(e.target.value)}
                        placeholder="Your name"
                        className="w-full px-3 py-2.5 text-sm border border-blue-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-[#1877F2]"
                      />
                    </div>
                    <div>
                      <label className="text-xs font-medium text-[#65676B] mb-1 block">Email</label>
                      <input
                        type="email"
                        value={editEmail}
                        onChange={e => setEditEmail(e.target.value)}
                        placeholder="Email address"
                        className="w-full px-3 py-2.5 text-sm border border-blue-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-[#1877F2]"
                      />
                    </div>
                    {userProfile.phone && (
                      <div>
                        <label className="text-xs font-medium text-[#65676B] mb-1 block">Phone</label>
                        <div className="px-3 py-2.5 text-sm bg-gray-50 border border-gray-200 rounded-xl text-[#65676B]">
                          {userProfile.phone}
                        </div>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={handleSaveProfile}
                    disabled={profileSaving}
                    className="mt-4 w-full py-2.5 bg-[#1877F2] text-white text-sm font-semibold rounded-xl hover:bg-[#166FE5] disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
                  >
                    {profileSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                    Save Changes
                  </button>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-4 mb-4">
                    <div className="w-16 h-16 bg-[#1877F2] rounded-full flex items-center justify-center">
                      <User className="w-8 h-8 text-white" />
                    </div>
                    <div className="flex-1">
                      <h2 className="text-xl font-bold">{userProfile.name}</h2>
                      <p className="text-sm text-[#65676B]">Member since {userProfile.memberSince}</p>
                    </div>
                    <button
                      onClick={handleStartEditProfile}
                      className="p-2 hover:bg-blue-100 rounded-full transition-colors"
                      aria-label="Edit profile"
                    >
                      <Pencil className="w-5 h-5 text-[#1877F2]" />
                    </button>
                  </div>

                  {userProfile.phone && (
                    <div className="flex items-center gap-3 mb-2">
                      <Phone className="w-4 h-4 text-[#65676B]" />
                      <span className="text-sm text-[#050505]">{userProfile.phone}</span>
                    </div>
                  )}

                  {userProfile.email && (
                    <div className="flex items-center gap-3 mb-2">
                      <Mail className="w-4 h-4 text-[#65676B]" />
                      <span className="text-sm text-[#050505]">{userProfile.email}</span>
                    </div>
                  )}

                  {userProfile.vehicle && (
                    <div className="flex items-center gap-3">
                      <Car className="w-4 h-4 text-[#65676B]" />
                      <span className="text-sm text-[#050505]">{userProfile.vehicle}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <div className="bg-gradient-to-br from-[#1877F2] to-[#0d5bbf] rounded-2xl p-6 text-white">
              <div className="flex items-center gap-4 mb-4">
                <div className="w-16 h-16 bg-white/20 rounded-full flex items-center justify-center">
                  <User className="w-8 h-8 text-white" />
                </div>
                <div>
                  <h2 className="text-xl font-bold">Welcome to Nerava</h2>
                  <p className="text-sm text-white/80">Sign in to unlock all features</p>
                </div>
              </div>

              <ul className="text-sm text-white/90 space-y-2 mb-5">
                <li className="flex items-center gap-2">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Connect your Tesla account
                </li>
                <li className="flex items-center gap-2">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Earn rewards while charging
                </li>
                <li className="flex items-center gap-2">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  Save your favorite merchants
                </li>
              </ul>

              <button
                onClick={() => setShowLoginModal(true)}
                className="w-full py-3 bg-white text-[#1877F2] font-semibold rounded-xl hover:bg-white/90 transition-colors flex items-center justify-center gap-2"
              >
                <LogIn className="w-5 h-5" />
                Sign In
              </button>
            </div>
          )}

          {/* Profile Completion */}
          {isAuthenticated && (
            <ProfileCompletionCard
              currentEmail={userProfile?.email}
              currentName={userProfile?.name === 'EV Driver' ? null : userProfile?.name}
              onProfileUpdated={(updates) => {
                setUserProfile(prev => prev ? {
                  ...prev,
                  name: updates.display_name || prev.name,
                  email: updates.email || prev.email,
                } : prev)
              }}
            />
          )}

          {/* Account Stats */}
          {isAuthenticated && <AccountStatsCard />}

          {/* Connected Vehicles */}
          {isAuthenticated && (
            <div className="bg-gray-50 rounded-2xl border border-[#E4E6EB] overflow-hidden">
              <div className="p-4 border-b border-[#E4E6EB]">
                <h3 className="font-semibold text-sm text-[#65676B] uppercase tracking-wide">Connected Vehicles</h3>
              </div>
              <div className="p-4">
                {teslaLoading ? (
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-gray-200 rounded-full animate-pulse" />
                    <div className="flex-1">
                      <div className="h-4 bg-gray-200 rounded w-32 animate-pulse mb-1" />
                      <div className="h-3 bg-gray-200 rounded w-24 animate-pulse" />
                    </div>
                  </div>
                ) : teslaStatus?.connected ? (
                  <div className="space-y-2">
                    <button
                      onClick={onViewVehicle}
                      className="w-full flex items-center gap-3 text-left active:bg-gray-50 rounded-xl transition-colors"
                    >
                      <div className="w-10 h-10 bg-white rounded-full flex items-center justify-center border border-gray-100">
                        <img src="/tesla-t-logo.png" alt="Tesla" className="w-6 h-6 object-contain" />
                      </div>
                      <div className="flex-1">
                        <p className="font-medium text-[#050505]">
                          {teslaStatus.vehicle_name || teslaStatus.vehicle_model || 'Tesla'}
                        </p>
                        <p className="text-sm text-[#65676B]">
                          {teslaStatus.vehicle_name && teslaStatus.vehicle_model && teslaStatus.vehicle_model !== teslaStatus.vehicle_name
                            ? teslaStatus.vehicle_model
                            : 'Connected'}
                          {teslaStatus.vin ? ` \u00B7 VIN \u2022\u2022\u2022${teslaStatus.vin.slice(-4)}` : ''}
                        </p>
                      </div>
                      <ChevronRight className="w-5 h-5 text-gray-400" />
                    </button>
                    <button
                      onClick={() => setShowRemoveVehicle(true)}
                      className="w-full flex items-center justify-center gap-1.5 py-2 text-sm text-red-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Remove Vehicle
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowConnectOptions(true)}
                    className="w-full flex items-center gap-3 p-1 hover:bg-gray-100 rounded-xl transition-colors"
                  >
                    <div className="w-10 h-10 bg-blue-50 rounded-full flex items-center justify-center">
                      <Zap className="w-5 h-5 text-blue-600" />
                    </div>
                    <div className="flex-1 text-left">
                      <p className="font-medium text-[#050505]">Connect Vehicle</p>
                      <p className="text-sm text-[#65676B]">Verify charging and earn rewards</p>
                    </div>
                    <ChevronRight className="w-5 h-5 text-gray-400" />
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Charging Activity */}
          {isAuthenticated && onViewActivity && (
            <button
              onClick={onViewActivity}
              className="w-full p-4 bg-gray-50 rounded-2xl flex items-center gap-3 hover:bg-gray-100 active:bg-gray-200 transition-colors border border-[#E4E6EB]"
            >
              <div className="w-10 h-10 bg-green-50 rounded-full flex items-center justify-center">
                <Activity className="w-5 h-5 text-green-600" />
              </div>
              <div className="flex-1 text-left">
                <p className="font-medium">Charging Activity</p>
                <p className="text-sm text-[#65676B]">Sessions, stats & rewards</p>
              </div>
              <ChevronRight className="w-5 h-5 text-gray-400" />
            </button>
          )}

          {/* Favorites */}
          <button
            onClick={() => setShowFavoritesList(true)}
            className="w-full p-4 bg-gray-50 rounded-2xl flex items-center gap-3 hover:bg-gray-100 active:bg-gray-200 transition-colors border border-[#E4E6EB]"
          >
            <div className="w-10 h-10 bg-red-50 rounded-full flex items-center justify-center">
              <Heart className="w-5 h-5 text-red-500" />
            </div>
            <div className="flex-1 text-left">
              <p className="font-medium">Favorites</p>
              <p className="text-sm text-[#65676B]">{favorites.size} saved</p>
            </div>
            <ChevronRight className="w-5 h-5 text-gray-400" />
          </button>

          {/* Share Nerava */}
          <button
            onClick={() => setShowShareNerava(true)}
            className="w-full p-4 bg-blue-50 rounded-2xl flex items-center gap-3 hover:bg-blue-100 active:bg-blue-200 transition-colors border border-blue-100"
          >
            <div className="w-10 h-10 bg-[#1877F2]/20 rounded-full flex items-center justify-center">
              <svg className="w-5 h-5 text-[#1877F2]" viewBox="0 0 24 24" fill="currentColor">
                <path d="M3 3h6v6H3V3zm2 2v2h2V5H5zm8-2h6v6h-6V3zm2 2v2h2V5h-2zM3 13h6v6H3v-6zm2 2v2h2v-2H5zm8-2h2v2h-2v-2zm2 0h2v2h-2v-2zm2 0h2v2h-2v-2zm-4 4h2v2h-2v-2zm2 0h2v2h-2v-2zm2 0h2v2h-2v-2zm-4 2h2v2h-2v-2zm2 0h2v2h-2v-2zm2 0h2v2h-2v-2z"/>
              </svg>
            </div>
            <div className="flex-1 text-left">
              <p className="font-medium text-[#1877F2]">Share Nerava</p>
              <p className="text-sm text-[#1877F2]/70">Earn rewards for referrals</p>
            </div>
            <ChevronRight className="w-5 h-5 text-[#1877F2]" />
          </button>

          {/* Preferences */}
          <div className="bg-gray-50 rounded-2xl border border-[#E4E6EB] overflow-hidden">
            <div className="p-4 border-b border-[#E4E6EB]">
              <h3 className="font-semibold text-sm text-[#65676B] uppercase tracking-wide">Preferences</h3>
            </div>
            {/* Notifications toggle */}
            <div className="flex items-center justify-between p-4 border-b border-[#E4E6EB]">
              <div className="flex items-center gap-3">
                {notificationsEnabled ? (
                  <Bell className="w-5 h-5 text-[#65676B]" />
                ) : (
                  <BellOff className="w-5 h-5 text-[#65676B]" />
                )}
                <div>
                  <p className="font-medium text-[#050505]">Notifications</p>
                  <p className="text-sm text-[#65676B]">Charging alerts and offers</p>
                </div>
              </div>
              <button
                onClick={handleToggleNotifications}
                className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
                  notificationsEnabled ? 'bg-[#1877F2]' : 'bg-gray-300'
                }`}
                role="switch"
                aria-checked={notificationsEnabled}
                aria-label="Toggle notifications"
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform ${
                    notificationsEnabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            {/* Distance unit toggle */}
            <div className="flex items-center justify-between p-4">
              <div className="flex items-center gap-3">
                <Ruler className="w-5 h-5 text-[#65676B]" />
                <div>
                  <p className="font-medium text-[#050505]">Distance Unit</p>
                  <p className="text-sm text-[#65676B]">
                    {distanceUnit === 'miles' ? 'Miles' : 'Kilometers'}
                  </p>
                </div>
              </div>
              <button
                onClick={handleToggleDistanceUnit}
                className="px-3 py-1.5 bg-white border border-[#E4E6EB] rounded-full text-sm font-medium text-[#050505] hover:bg-gray-100 active:bg-gray-200 transition-colors"
              >
                {distanceUnit === 'miles' ? 'mi' : 'km'}
              </button>
            </div>
            {/* Mock Charging toggle — test user only */}
            {isTestUser && (
              <div className="flex items-center justify-between p-4 border-t border-[#E4E6EB]">
                <div className="flex items-center gap-3">
                  <Zap className="w-5 h-5 text-orange-500" />
                  <div>
                    <p className="font-medium text-[#050505]">Mock Charging</p>
                    <p className="text-sm text-[#65676B]">Simulate active session for testing</p>
                  </div>
                </div>
                <button
                  onClick={() => {
                    const next = !mockCharging
                    setMockCharging(next)
                    localStorage.setItem('debug_mock_charging', String(next))
                  }}
                  className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
                    mockCharging ? 'bg-orange-500' : 'bg-gray-300'
                  }`}
                  role="switch"
                  aria-checked={mockCharging}
                  aria-label="Toggle mock charging"
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform ${
                      mockCharging ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            )}
          </div>

          {/* Test Push button removed for production */}

          {/* Support */}
          <a
            href="https://nerava.network/support"
            target="_blank"
            rel="noopener noreferrer"
            className="w-full p-4 bg-gray-50 rounded-2xl flex items-center gap-3 hover:bg-gray-100 active:bg-gray-200 transition-colors border border-[#E4E6EB]"
          >
            <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center">
              <HelpCircle className="w-5 h-5 text-[#65676B]" />
            </div>
            <div className="flex-1 text-left">
              <p className="font-medium">Help & Support</p>
              <p className="text-sm text-[#65676B]">FAQ, contact, and feedback</p>
            </div>
            <ExternalLink className="w-4 h-4 text-gray-400" />
          </a>

          {/* Send Feedback */}
          <button
            onClick={() => setShowFeedback(true)}
            className="w-full p-4 bg-gray-50 rounded-2xl flex items-center gap-3 hover:bg-gray-100 active:bg-gray-200 transition-colors border border-[#E4E6EB]"
          >
            <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center">
              <MessageSquare className="w-5 h-5 text-[#65676B]" />
            </div>
            <div className="flex-1 text-left">
              <p className="font-medium">Send Feedback</p>
              <p className="text-sm text-[#65676B]">Help us improve Nerava</p>
            </div>
            <ChevronRight className="w-5 h-5 text-gray-400" />
          </button>

          {/* Logout - only show when authenticated */}
          {isAuthenticated && (
            <button
              onClick={handleLogout}
              className="w-full p-4 bg-red-50 rounded-2xl flex items-center gap-3 hover:bg-red-100 active:bg-red-200 transition-colors border border-red-100"
            >
              <div className="w-10 h-10 bg-red-100 rounded-full flex items-center justify-center">
                <LogOut className="w-5 h-5 text-red-600" />
              </div>
              <span className="text-red-600 font-medium">Log out</span>
            </button>
          )}

          {/* Delete Account - danger zone */}
          {isAuthenticated && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="w-full p-4 bg-white rounded-2xl flex items-center gap-3 hover:bg-red-50 transition-colors border border-gray-200"
            >
              <div className="w-10 h-10 bg-red-50 rounded-full flex items-center justify-center">
                <Trash2 className="w-5 h-5 text-red-500" />
              </div>
              <span className="text-red-500 font-medium text-sm">Delete Account</span>
            </button>
          )}
        </div>
      )}
    </div>

    {/* Remove Vehicle Confirmation Modal */}
    {showRemoveVehicle && (
      <div className="fixed inset-0 z-[4000] bg-black/50 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl max-w-sm w-full p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 bg-red-100 rounded-full flex items-center justify-center">
              <Trash2 className="w-5 h-5 text-red-600" />
            </div>
            <h3 className="text-lg font-bold text-gray-900">Remove Vehicle</h3>
          </div>
          <p className="text-sm text-gray-600 mb-1 font-medium">
            Remove {teslaStatus?.vehicle_name || teslaStatus?.vehicle_model || 'this vehicle'}?
          </p>
          <p className="text-sm text-gray-500 mb-4">
            You will stop earning rewards from this vehicle's charging sessions. You can reconnect later.
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => setShowRemoveVehicle(false)}
              className="flex-1 py-3 bg-gray-100 text-gray-700 font-medium rounded-xl hover:bg-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleRemoveVehicle}
              disabled={removeVehicleLoading}
              className="flex-1 py-3 bg-red-600 text-white font-medium rounded-xl hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              {removeVehicleLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Remove Vehicle
            </button>
          </div>
        </div>
      </div>
    )}

    {/* Delete Account Confirmation Modal */}
    {showDeleteConfirm && (
      <div className="fixed inset-0 z-[4000] bg-black/50 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl max-w-sm w-full p-6">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 bg-red-100 rounded-full flex items-center justify-center">
              <AlertTriangle className="w-5 h-5 text-red-600" />
            </div>
            <h3 className="text-lg font-bold text-gray-900">Delete Account</h3>
          </div>
          <p className="text-sm text-gray-600 mb-4">
            This action is permanent. All your data, rewards, and history will be deleted. Type <strong>DELETE</strong> to confirm.
          </p>
          <input
            type="text"
            value={deleteConfirmText}
            onChange={(e) => setDeleteConfirmText(e.target.value)}
            placeholder="Type DELETE"
            className="w-full px-4 py-3 border border-gray-300 rounded-xl mb-4 text-sm focus:outline-none focus:ring-2 focus:ring-red-300"
          />
          <div className="flex gap-3">
            <button
              onClick={() => { setShowDeleteConfirm(false); setDeleteConfirmText('') }}
              className="flex-1 py-3 bg-gray-100 text-gray-700 font-medium rounded-xl hover:bg-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDeleteAccount}
              disabled={deleteConfirmText !== 'DELETE' || deleteLoading}
              className="flex-1 py-3 bg-red-600 text-white font-medium rounded-xl hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              {deleteLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Delete
            </button>
          </div>
        </div>
      </div>
    )}

    {/* Feedback Modal */}
    {showFeedback && (
      <div className="fixed inset-0 z-[4000] bg-black/50 flex items-center justify-center p-4">
        <div className="bg-white rounded-2xl max-w-sm w-full p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-bold text-gray-900">Send Feedback</h3>
            <button onClick={() => { setShowFeedback(false); setFeedbackText('') }} className="p-1 hover:bg-gray-100 rounded-full">
              <X className="w-5 h-5" />
            </button>
          </div>
          {feedbackSent ? (
            <div className="text-center py-6">
              <p className="text-green-600 font-medium">Thank you for your feedback!</p>
            </div>
          ) : (
            <>
              <textarea
                value={feedbackText}
                onChange={(e) => setFeedbackText(e.target.value)}
                placeholder="Tell us what you think..."
                rows={4}
                className="w-full px-4 py-3 border border-gray-300 rounded-xl mb-4 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
              />
              <button
                onClick={handleSubmitFeedback}
                disabled={!feedbackText.trim() || feedbackSubmitting}
                className="w-full py-3 bg-[#1877F2] text-white font-semibold rounded-xl hover:bg-[#166FE5] disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
              >
                {feedbackSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                Submit
              </button>
            </>
          )}
        </div>
      </div>
    )}

    {/* Vehicle Connect Options Screen */}
    {showConnectOptions && (
      <VehicleConnectOptions
        onClose={() => setShowConnectOptions(false)}
        onConnectTesla={handleConnectTesla}
        onConnectSmartcar={handleConnectSmartcar}
      />
    )}

    {/* Smartcar toast */}
    {smartcarToast && (
      <SmartcarToast message={smartcarToast} onDismiss={() => setSmartcarToast(null)} />
    )}
    </>
  )
}

function SmartcarToast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 3000)
    return () => clearTimeout(timer)
  }, [onDismiss])

  return (
    <div className="fixed bottom-6 left-4 right-4 z-[4100] flex justify-center pointer-events-none">
      <div className="bg-gray-900 text-white text-sm px-4 py-3 rounded-xl shadow-lg max-w-sm text-center">
        {message}
      </div>
    </div>
  )
}
