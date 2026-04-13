import { useEffect, useRef, useMemo } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { DiscoveryItem } from './discovery-types'
import { getItemId, getItemLat, getItemLng } from './discovery-types'
import type { ChargerSummary } from '../../types'

function getChargerColor(item: DiscoveryItem): string {
  if (item.type !== 'charger') return '#656A6B'
  const c = item.data as ChargerSummary
  if (c.network_name?.toLowerCase().includes('tesla')) return '#E31937'
  if (c.power_kw && c.power_kw > 50) return '#1877F2'
  return '#10B981'
}

/** Build HTML for badges on charger pin. Always show price. Stack star + $ reward when present.
 *  When collapseLabel is true, omit the fallback network name label to reduce visual clutter. */
function getChargerBadgeHtml(item: DiscoveryItem, collapseLabel = false): string {
  if (item.type !== 'charger') return ''
  const c = item.data as ChargerSummary
  let badges = ''

  // Gold star badge for merchant amenity/perk — top-left
  if (c.has_merchant_perk) {
    badges += `<div style="position:absolute;top:-12px;left:-8px;background:linear-gradient(135deg,#F59E0B,#D97706);color:white;font-size:9px;font-weight:700;padding:1px 5px;border-radius:8px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.3);display:flex;align-items:center;gap:1px;z-index:10;">★</div>`
  }

  // Nerava blue $ badge for campaign rewards — top-right
  const rewardCents = c.campaign_reward_cents
  if (rewardCents && rewardCents > 0) {
    const amount = `$${(rewardCents / 100).toFixed(0)}`
    badges += `<div style="position:absolute;top:-12px;right:-10px;background:#1877F2;color:white;font-size:9px;font-weight:700;padding:1px 5px;border-radius:8px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.3);display:flex;align-items:center;gap:1px;z-index:10;">${amount}</div>`
  }

  // Price badge — always shown below the pin
  if (c.pricing_per_kwh != null && c.pricing_per_kwh > 0) {
    const priceLabel = `$${c.pricing_per_kwh.toFixed(2)}/kWh`
    badges += `<div style="position:absolute;bottom:-14px;left:50%;transform:translateX(-50%);background:#1a1a2e;color:white;font-size:8px;font-weight:600;padding:1px 4px;border-radius:6px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.3);z-index:9;">${priceLabel}</div>`
  } else if (!collapseLabel) {
    // Fallback: show network name so pin is never bare (hidden when 3+ pins share same network)
    const label = c.network_name || 'EV'
    badges += `<div style="position:absolute;bottom:-14px;left:50%;transform:translateX(-50%);background:#1a1a2e;color:white;font-size:8px;font-weight:600;padding:1px 4px;border-radius:6px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.3);z-index:9;">${label}</div>`
  }

  return badges
}

interface DiscoveryMapProps {
  items: DiscoveryItem[]
  selectedId: string | null
  onPinTap: (id: string) => void
  userLat?: number
  userLng?: number
  onRecenter?: () => void
  sheetPosition?: 'peek' | 'half' | 'full'
  activeChargerId?: string | null
  mapCenter?: { lat: number; lng: number } | null
  onMapMoved?: (center: { lat: number; lng: number }) => void
}

export function DiscoveryMap({
  items,
  selectedId,
  onPinTap,
  userLat,
  userLng,
  sheetPosition = 'half',
  activeChargerId,
  mapCenter,
  onMapMoved,
}: DiscoveryMapProps) {
  const mapRef = useRef<HTMLDivElement>(null)
  const mapInstanceRef = useRef<L.Map | null>(null)
  const markersRef = useRef<L.Layer[]>([])
  const fittedRef = useRef(false)

  // Default center
  const center = useMemo((): [number, number] => {
    if (userLat && userLng) return [userLat, userLng]
    const first = items[0]
    if (first) return [getItemLat(first), getItemLng(first)]
    return [30.9876, -97.6492] // Harker Heights TX fallback
  }, [userLat, userLng, items])

  // Initialize map once
  useEffect(() => {
    if (!mapRef.current || mapInstanceRef.current) return

    const map = L.map(mapRef.current, {
      zoomControl: false,
    }).setView(center, 14)

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map)

    mapInstanceRef.current = map

    // Fire onMapMoved only when user drags (not programmatic pan/zoom)
    map.on('dragend', () => {
      const c = map.getCenter()
      onMapMovedRef.current?.({ lat: c.lat, lng: c.lng })
    })

    return () => {
      map.remove()
      mapInstanceRef.current = null
    }
  }, []) // Only run once on mount

  // Stable ref for onMapMoved to avoid re-registering listener
  const onMapMovedRef = useRef(onMapMoved)
  useEffect(() => { onMapMovedRef.current = onMapMoved }, [onMapMoved])

  // Fit bounds once when data first arrives
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || fittedRef.current || items.length === 0) return

    const points: L.LatLngExpression[] = []
    for (const item of items) {
      const lat = getItemLat(item)
      const lng = getItemLng(item)
      if (lat && lng) points.push([lat, lng])
    }
    if (userLat && userLng) points.push([userLat, userLng])

    if (points.length > 0) {
      const bounds = L.latLngBounds(points)
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 })
      fittedRef.current = true
    }
  }, [items, userLat, userLng])

  // Update markers when items or selection changes
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map) return

    // Clear existing markers
    markersRef.current.forEach((marker) => marker.remove())
    markersRef.current = []

    // Count how many pins share each network name — collapse labels when 3+ are identical
    const networkCounts = new Map<string, number>()
    for (const item of items) {
      if (item.type === 'charger') {
        const normalizedName = ((item.data as ChargerSummary).network_name || 'EV').trim().toLowerCase()
        networkCounts.set(normalizedName, (networkCounts.get(normalizedName) || 0) + 1)
      }
    }

    // Add user location with pulsing ring
    if (userLat && userLng) {
      const pulsingRing = L.circle([userLat, userLng], {
        radius: 30,
        fillColor: '#1877F2',
        color: '#1877F2',
        weight: 0,
        opacity: 0.3,
        fillOpacity: 0.3,
        className: 'pulsing-ring',
      }).addTo(map)
      markersRef.current.push(pulsingRing)

      const userMarker = L.circleMarker([userLat, userLng], {
        radius: 8,
        fillColor: '#1877F2',
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 1,
      }).addTo(map)
      markersRef.current.push(userMarker)
    }

    // Add markers for each item
    items.forEach((item) => {
      const lat = getItemLat(item)
      const lng = getItemLng(item)
      const id = getItemId(item)
      const name = item.data.name
      const isSelected = selectedId === id

      if (!lat || !lng) return

      if (item.type === 'charger') {
        const pinColor = getChargerColor(item)
        const isActiveCharger = activeChargerId === id
        const chargerNetwork = (item.data as ChargerSummary).network_name || 'EV'
        const shouldCollapseLabel = (networkCounts.get(chargerNetwork.trim().toLowerCase()) || 0) >= 3
        const badgeHtml = getChargerBadgeHtml(item, shouldCollapseLabel)
        const activeRing = isActiveCharger
          ? `<div style="position:absolute;top:-4px;left:-4px;width:48px;height:48px;border:3px solid #10B981;border-radius:50%;animation:discoveryPulse 2s infinite;"></div>`
          : ''
        const activeLabel = isActiveCharger
          ? `<div style="position:absolute;top:-20px;left:50%;transform:translateX(-50%);background:#10B981;color:white;font-size:10px;font-weight:600;padding:1px 6px;border-radius:8px;white-space:nowrap;">Charging</div>`
          : ''
        const iconHtml = isSelected || isActiveCharger
          ? `<div style="position:relative;width:40px;height:40px;">
               ${activeRing}
               <div style="position:absolute;top:0;left:0;width:40px;height:40px;background:${pinColor}40;border-radius:50%;${isActiveCharger ? '' : 'animation:discoveryPulse 2s infinite;'}"></div>
               <div style="position:absolute;top:4px;left:4px;width:32px;height:32px;background:${pinColor};border:2px solid ${isActiveCharger ? '#10B981' : 'white'};border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;color:white;font-size:16px;">⚡</div>
               ${activeLabel}
               ${badgeHtml}
             </div>`
          : `<div style="position:relative;width:32px;height:32px;"><div style="width:32px;height:32px;background:${pinColor};border:2px solid white;border-radius:50%;box-shadow:0 2px 8px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;color:white;font-size:14px;">⚡</div>${badgeHtml}</div>`

        const icon = L.divIcon({
          html: iconHtml,
          className: '',
          iconSize: isSelected ? [40, 54] : [32, 48],
          iconAnchor: isSelected ? [20, 22] : [16, 18],
        })

        const marker = L.marker([lat, lng], { icon })
        marker.bindTooltip(name, { permanent: false, direction: 'top' })
        marker.on('click', () => onPinTap(id))
        marker.addTo(map)
        markersRef.current.push(marker)
      }
    })
  }, [items, selectedId, onPinTap, userLat, userLng, activeChargerId])

  // Recenter map when mapCenter changes (geocoded search)
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !mapCenter) return
    map.setView([mapCenter.lat, mapCenter.lng], 13, { animate: true, duration: 0.5 })
  }, [mapCenter])

  // Pan to selected marker, offset so pin sits in visible area between filters and sheet
  useEffect(() => {
    const map = mapInstanceRef.current
    if (!map || !selectedId) return

    const selectedItem = items.find((item) => getItemId(item) === selectedId)
    if (!selectedItem) return

    const targetLatLng = L.latLng(getItemLat(selectedItem), getItemLng(selectedItem))
    const mapSize = map.getSize()

    // Calculate the visible vertical band:
    // Top is obscured by search bar + filters (~120px)
    // Bottom is obscured by the sheet
    const topOffset = 120
    const sheetHeightPx =
      sheetPosition === 'peek'
        ? 160
        : sheetPosition === 'half'
          ? mapSize.y * 0.45
          : mapSize.y * 0.92

    // Target the vertical center of the visible area
    const visibleTop = topOffset
    const visibleBottom = mapSize.y - sheetHeightPx
    const visibleCenterY = (visibleTop + visibleBottom) / 2

    // Offset from the true center of the map container
    const mapCenterY = mapSize.y / 2
    const pixelOffsetY = visibleCenterY - mapCenterY

    // Convert target latlng to pixel, apply offset, convert back
    const targetPoint = map.latLngToContainerPoint(targetLatLng)
    const offsetPoint = L.point(targetPoint.x, targetPoint.y - pixelOffsetY)
    const offsetLatLng = map.containerPointToLatLng(offsetPoint)

    map.panTo(offsetLatLng, {
      animate: true,
      duration: 0.5,
    })
  }, [selectedId, items, sheetPosition])

  return (
    <div className="relative w-full h-full">
      <div ref={mapRef} className="w-full h-full" />

      {/* Pulse animation CSS */}
      <style>{`
        @keyframes discoveryPulse {
          0% { transform: scale(1); opacity: 0.6; }
          50% { transform: scale(1.3); opacity: 0.2; }
          100% { transform: scale(1); opacity: 0.6; }
        }
        .pulsing-ring {
          animation: discoveryPulse 2s infinite;
        }
      `}</style>
    </div>
  )
}
