import { useState, useRef, useCallback, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useActiveChargingSession, pollChargingSession, type PollSessionResponse } from '../services/api'

interface SessionPollingState {
  isActive: boolean
  sessionId: string | null
  durationMinutes: number
  kwhDelivered: number | null
  minutesToFull: number | null
  batteryLevel: number | null
  chargerPowerKw: number | null
  lastIncentive: { amountCents: number } | null
  pollError: string | null
  telemetryMode: boolean
}

/**
 * Reads active charging session from the backend every 30s.
 * Also triggers POST /poll with smart intervals:
 *   - 60s when actively charging (need telemetry updates)
 *   - 5 min when not charging (just checking if session started)
 *   - Skips when moving (speed > 5 m/s)
 *   - Skips when vehicle is asleep
 */
export function useSessionPolling() {
  const queryClient = useQueryClient()
  const { data, error } = useActiveChargingSession()

  // Track auth state reactively so polling restarts after login
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('access_token'))
  useEffect(() => {
    const sync = () => setAuthToken(localStorage.getItem('access_token'))
    window.addEventListener('storage', sync)
    window.addEventListener('nerava:auth-changed', sync)
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener('nerava:auth-changed', sync)
    }
  }, [])

  const [lastIncentive, setLastIncentive] = useState<{ amountCents: number } | null>(null)
  const [serverMinutesToFull, setServerMinutesToFull] = useState<number | null>(null)
  const [localMinutesToFull, setLocalMinutesToFull] = useState<number | null>(null)
  const lastServerUpdateRef = useRef<number>(0) // timestamp of last server update
  const [batteryLevel, setBatteryLevel] = useState<number | null>(null)
  const [chargerPowerKw, setChargerPowerKw] = useState<number | null>(null)
  const prevSessionRef = useRef<string | null>(null)
  const incentiveShownRef = useRef<string | null>(null)
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollIntervalRef = useRef<number>(60000) // start at 60s
  const intervalIdRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Smart polling: adjusts interval based on charging state
  useEffect(() => {
    if (!authToken) return

    const doPoll = async () => {
      try {
        if (navigator.geolocation) {
          navigator.geolocation.getCurrentPosition(
            async (pos) => {
              const speed = pos.coords.speed
              if (speed !== null && speed > 5) {
                console.log(`[SessionPolling] Skipping poll — moving at ${speed.toFixed(1)} m/s`)
                // Moving = not charging, slow down polls
                updateInterval(300000)
                return
              }
              const result = await pollChargingSession(pos.coords.latitude, pos.coords.longitude)
              queryClient.invalidateQueries({ queryKey: ['charging-sessions', 'active'] })
              adjustInterval(result)
            },
            async () => {
              const result = await pollChargingSession()
              queryClient.invalidateQueries({ queryKey: ['charging-sessions', 'active'] })
              adjustInterval(result)
            },
            { timeout: 5000, maximumAge: 30000 }
          )
        } else {
          const result = await pollChargingSession()
          queryClient.invalidateQueries({ queryKey: ['charging-sessions', 'active'] })
          adjustInterval(result)
        }
      } catch {
        // On error, back off to 5 min
        updateInterval(300000)
      }
    }

    const adjustInterval = (result: PollSessionResponse | undefined) => {
      if (!result) return
      // Update telemetry from poll response
      if (result.minutes_to_full != null) {
        setServerMinutesToFull(result.minutes_to_full)
        setLocalMinutesToFull(result.minutes_to_full)
        lastServerUpdateRef.current = Date.now()
      }
      if (result.battery_level != null) setBatteryLevel(result.battery_level)
      if (result.charger_power_kw != null) setChargerPowerKw(result.charger_power_kw)
      if (!result.session_active) {
        setServerMinutesToFull(null)
        setLocalMinutesToFull(null)
        setChargerPowerKw(null)
      }
      if (result.session_active) {
        // Use server's smart interval (based on estimated charge completion time)
        // Falls back to 60s if server doesn't provide a recommendation
        const interval = result.recommended_interval_s ? result.recommended_interval_s * 1000 : 60000
        updateInterval(interval)
      } else if (result.vehicle_asleep) {
        // Car is asleep — no need to poll frequently, 5 min
        updateInterval(300000)
      } else {
        // Not charging — use server recommendation or default 5 min
        const recommended = (result.recommended_interval_s as number) || 300
        updateInterval(recommended * 1000)
      }
    }

    const updateInterval = (newMs: number) => {
      if (newMs === pollIntervalRef.current) return
      console.log(`[SessionPolling] Interval changed: ${pollIntervalRef.current / 1000}s → ${newMs / 1000}s`)
      pollIntervalRef.current = newMs
      // Restart the interval with the new timing
      if (intervalIdRef.current) clearInterval(intervalIdRef.current)
      intervalIdRef.current = setInterval(doPoll, newMs)
    }

    // Initial poll after 5s delay
    const initialTimeout = setTimeout(doPoll, 5000)
    // Start with 60s interval
    intervalIdRef.current = setInterval(doPoll, 60000)

    return () => {
      clearTimeout(initialTimeout)
      if (intervalIdRef.current) clearInterval(intervalIdRef.current)
    }
  }, [queryClient, authToken])

  // Local countdown: decrement minutesToFull every 60s between API polls
  useEffect(() => {
    if (serverMinutesToFull == null || serverMinutesToFull <= 0) {
      if (countdownRef.current) clearInterval(countdownRef.current)
      return
    }
    countdownRef.current = setInterval(() => {
      const elapsed = Math.floor((Date.now() - lastServerUpdateRef.current) / 60000)
      const remaining = Math.max(0, (serverMinutesToFull ?? 0) - elapsed)
      setLocalMinutesToFull(remaining)
    }, 60000)
    return () => {
      if (countdownRef.current) clearInterval(countdownRef.current)
    }
  }, [serverMinutesToFull])

  // Detect session end → check for incentive
  useEffect(() => {
    const currentSessionId = data?.active ? data.session?.id ?? null : null
    const prevId = prevSessionRef.current

    // Session just ended (was active, now not)
    if (prevId && !currentSessionId) {
      queryClient.invalidateQueries({ queryKey: ['charging-sessions'] })
      queryClient.invalidateQueries({ queryKey: ['wallet'] })
      queryClient.refetchQueries({ queryKey: ['wallet'] })

      // Check last_ended_session for incentive
      const ended = data?.last_ended_session
      if (ended?.incentive && ended.incentive.amount_cents > 0 && incentiveShownRef.current !== ended.id) {
        incentiveShownRef.current = ended.id
        setLastIncentive({ amountCents: ended.incentive.amount_cents })
      }
    }

    // Also check for incentive on recently ended sessions even if we didn't see the transition
    // (e.g. app was backgrounded during session end)
    if (!currentSessionId && !prevId && data?.last_ended_session) {
      const ended = data.last_ended_session
      if (ended.incentive && ended.incentive.amount_cents > 0 && incentiveShownRef.current !== ended.id) {
        incentiveShownRef.current = ended.id
        setLastIncentive({ amountCents: ended.incentive.amount_cents })
      }
    }

    prevSessionRef.current = currentSessionId
  }, [data, queryClient])

  const clearIncentive = useCallback(() => {
    setLastIncentive(null)
  }, [])

  // Compute live duration from session_start
  const session = data?.active ? data.session : null
  let durationMinutes = session?.duration_minutes ?? 0
  if (session?.session_start && !session.session_end) {
    const startMs = new Date(session.session_start).getTime()
    durationMinutes = Math.floor((Date.now() - startMs) / 60000)
  }

  // Debug mock charging: override isActive (restricted to admin account)
  const mockCharging = (() => {
    if (typeof window === 'undefined') return false
    if (localStorage.getItem('debug_mock_charging') !== 'true') return false
    try {
      const u = JSON.parse(localStorage.getItem('nerava_user') || '{}')
      return u.public_id === 'd537cd5a-f13b-4a12-a757-04b1b35d0749'
    } catch { return false }
  })()

  const state: SessionPollingState = {
    isActive: mockCharging || (data?.active ?? false),
    sessionId: session?.id ?? null,
    durationMinutes,
    kwhDelivered: session?.kwh_delivered ?? null,
    minutesToFull: localMinutesToFull,
    batteryLevel,
    chargerPowerKw,
    lastIncentive,
    pollError: error ? 'fetch_failed' : null,
    telemetryMode: true,
  }

  return { ...state, clearIncentive }
}

