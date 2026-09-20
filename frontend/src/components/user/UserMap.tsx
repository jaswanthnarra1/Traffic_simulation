import L from 'leaflet'
import { useEffect, useRef } from 'react'
import { Circle, MapContainer, Marker, Pane, Polyline, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import type { Here } from '../../hooks/useGeolocation'
import type { ExplainTarget, Place, Pt, RouteOption, UserAlert, Zone } from '../../types/user'
import { LEVEL_COLOR, minutes } from '../../utils/userTraffic'

// Standard OpenStreetMap tiles (same pale treatment as the operator map via CSS). No API key.
const TILES = (import.meta.env.VITE_TILE_URL as string | undefined) || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
const GLYPH: Record<string, string> = { hospital: 'H', fuel: 'F', food: 'R', bus: 'B', metro: 'M', parking: 'P' }

export interface MapFit { key: string; points: [number, number][] }

interface Props {
  center: { lat: number; lon: number }
  zones: Zone[]
  showTraffic: boolean
  routes: RouteOption[]
  selectedRouteId: string | null
  onSelectRoute: (id: string) => void
  origin: Pt | null
  dest: Pt | null
  here: Here | null
  places: Place[]
  selectedPlaceId: string | null
  onSelectPlace: (p: Place) => void
  alerts: UserAlert[]
  picking: boolean
  onPick: (lat: number, lon: number) => void
  fit: MapFit | null
  onReady: (m: L.Map) => void
  onMove?: (c: { lat: number; lon: number }) => void
  onExplain?: (t: ExplainTarget) => void
}

const icon = (html: string, size = 24) => L.divIcon({ className: '', html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] })
const ICONS = {
  origin: icon('<span class="u-origin"></span>', 20), dest: icon('<span class="u-dest"></span>', 26), me: icon('<span class="u-me"></span>', 22),
  alert: icon('<span class="u-alert">!</span>', 26),
}
const placeIcon = (cat: string, on: boolean) => icon(`<span class="u-place${on ? ' on' : ''}">${GLYPH[cat] ?? '•'}</span>`, 26)
const etaIcon = (text: string) => L.divIcon({ className: '', html: `<span class="u-eta">${text}</span>`, iconSize: [64, 24], iconAnchor: [32, 12] })

const ZONE_OPACITY = { moderate: 0.16, heavy: 0.26, severe: 0.34, free_flow: 0, no_data: 0 }

export function UserMap(p: Props) {
  const selected = p.routes.find(r => r.id === p.selectedRouteId) ?? null
  const others = p.routes.filter(r => r !== selected)
  return (
    <MapContainer center={[p.center.lat, p.center.lon]} zoom={12} minZoom={9} maxZoom={18} zoomControl={false} zoomSnap={0.25} zoomDelta={0.5}
      wheelPxPerZoomLevel={110} className={`size-full ${p.picking ? 'u-picking' : ''}`} attributionControl>
      <TileLayer url={TILES} attribution={ATTRIBUTION} maxNativeZoom={19} />
      <Bridge onReady={p.onReady} onMove={p.onMove} picking={p.picking} onPick={p.onPick} />
      <Camera fit={p.fit} />

      {/* simulated congestion: soft bands, deliberately not crisp road lines — the simulation grid is not real road geometry */}
      <Pane name="u-zones" style={{ zIndex: 402 }}>
        {p.showTraffic && p.zones.map(z => (
          <Polyline key={z.segment_id} positions={z.path} eventHandlers={{ click: () => p.onExplain?.({ lat: (z.path[0][0] + z.path[1][0]) / 2, lon: (z.path[0][1] + z.path[1][1]) / 2, segment_id: z.segment_id, open: true }) }}
            pathOptions={{ color: LEVEL_COLOR[z.traffic_level], weight: 24, opacity: ZONE_OPACITY[z.traffic_level], lineCap: 'round', className: 'fs-seg' }} />
        ))}
      </Pane>

      <Pane name="u-alt" style={{ zIndex: 404 }}>
        {others.map(r => (
          <Polyline key={`c-${r.id}`} positions={r.coords} interactive={false}
            pathOptions={{ color: '#fff', weight: 10, opacity: 0.9, lineCap: 'round', lineJoin: 'round', className: 'fs-seg' }} />
        ))}
        {others.map(r => (
          <Polyline key={`a-${r.id}`} positions={r.coords} eventHandlers={{ click: () => p.onSelectRoute(r.id) }}
            pathOptions={{ color: '#7d8ea3', weight: 6, opacity: 0.95, lineCap: 'round', lineJoin: 'round', className: 'u-route-in' }} />
        ))}
      </Pane>
      <Pane name="u-route" style={{ zIndex: 406 }}>
        {selected && <Polyline key={`sc-${selected.id}`} positions={selected.coords} interactive={false}
          pathOptions={{ color: '#fff', weight: 12, opacity: 0.95, lineCap: 'round', lineJoin: 'round', className: 'u-route-in' }} />}
        {selected?.stretches.map((s, i) => (
          <Polyline key={`s-${selected.id}-${i}`} positions={s.coords} interactive={false}
            pathOptions={{ color: LEVEL_COLOR[s.level], weight: 7, opacity: 1, lineCap: 'round', lineJoin: 'round', className: 'u-route-in' }} />
        ))}
      </Pane>

      {others.map(r => <Marker key={`e-${r.id}`} position={r.coords[Math.floor(r.coords.length / 2)]} icon={etaIcon(minutes(r.duration_min))}
        eventHandlers={{ click: () => p.onSelectRoute(r.id) }} keyboard={false} />)}
      {p.here && p.here.accuracy < 2000 && <Circle center={[p.here.lat, p.here.lon]} radius={p.here.accuracy} interactive={false}
        pathOptions={{ color: '#1d4ed8', weight: 1, opacity: 0.3, fillColor: '#1d4ed8', fillOpacity: 0.08 }} />}
      {p.here && <Marker position={[p.here.lat, p.here.lon]} icon={ICONS.me} interactive={false} keyboard={false} zIndexOffset={200} />}
      {p.origin && p.origin.source !== 'gps' && <Marker position={[p.origin.lat, p.origin.lon]} icon={ICONS.origin} interactive={false} keyboard={false} zIndexOffset={300} />}
      {p.dest && <Marker position={[p.dest.lat, p.dest.lon]} icon={ICONS.dest} interactive={false} keyboard={false} zIndexOffset={400} />}
      {p.alerts.filter(a => a.lat != null && a.lon != null).map(a => (
        <Marker key={`al-${a.type}-${a.segment_id ?? a.route_id ?? ''}`} position={[a.lat!, a.lon!]} icon={ICONS.alert} keyboard={false} title="Traffic disruption: tap for details" zIndexOffset={350}
          eventHandlers={{ click: () => p.onExplain?.({ lat: a.lat!, lon: a.lon!, segment_id: a.segment_id, open: true }) }} />
      ))}
      {p.places.map(pl => <Marker key={pl.id} position={[pl.lat, pl.lon]} icon={placeIcon(pl.category, pl.id === p.selectedPlaceId)} keyboard={false}
        eventHandlers={{ click: () => p.onSelectPlace(pl) }} zIndexOffset={pl.id === p.selectedPlaceId ? 500 : 100} />)}
    </MapContainer>
  )
}

function Bridge({ onReady, onMove, picking, onPick }: Pick<Props, 'onReady' | 'onMove' | 'picking' | 'onPick'>) {
  const map = useMap()
  useEffect(() => { onReady(map) }, [map, onReady])
  useMapEvents({
    moveend: () => { const c = map.getCenter(); onMove?.({ lat: c.lat, lon: c.lng }) },
    click: e => { if (picking) onPick(e.latlng.lat, e.latlng.lng) },
  })
  return null
}

/** Smooth fly-to whenever fit.key changes; padding keeps the route clear of the floating panel (desktop) or bottom sheet (mobile). */
function Camera({ fit }: { fit: MapFit | null }) {
  const map = useMap()
  const done = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!fit || !fit.points.length || done.current === fit.key) return
    done.current = fit.key
    const b = L.latLngBounds(fit.points)
    const mobile = window.innerWidth < 768
    if (b.getNorthEast().equals(b.getSouthWest())) { map.flyTo(b.getCenter(), 15, { duration: 0.9 }); return }
    map.flyToBounds(b, {
      paddingTopLeft: mobile ? [24, 150] : [440, 90], paddingBottomRight: mobile ? [24, Math.round(window.innerHeight * 0.48)] : [90, 90],
      maxZoom: 16, duration: 0.9, easeLinearity: 0.3,
    })
  }, [fit?.key]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}
