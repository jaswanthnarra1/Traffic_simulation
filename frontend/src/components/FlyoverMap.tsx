import L from 'leaflet'
import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Marker, Pane, Polyline, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import type { Bn, Candidate, Corridor } from '../types/corridor'

const TILES = (import.meta.env.VITE_TILE_URL as string | undefined) || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const BN_COLOR: Record<Bn, string> = { LOW: '#2f8f5b', MODERATE: '#d4a21a', HIGH: '#e0661b', CRITICAL: '#c62828', NO_DATA: '#9ca3af' }
const CAND = '#6d28d9'
const pin = (html: string, size: number) => L.divIcon({ className: '', html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] })
const ICON = {
  o: pin('<span style="display:block;width:16px;height:16px;border-radius:9999px;background:#16a34a;border:3px solid #fff;box-shadow:0 1px 5px rgba(22,24,29,.4)"></span>', 22),
  d: pin('<span style="display:block;width:16px;height:16px;border-radius:9999px;background:#16a34a;border:3px solid #fff;box-shadow:0 0 0 2px #16a34a,0 1px 5px rgba(22,24,29,.4)"></span>', 22),
  s: pin(`<span style="display:grid;place-items:center;width:22px;height:22px;border-radius:6px;background:${CAND};color:#fff;font:700 11px sans-serif;border:2px solid #fff">S</span>`, 22),
  e: pin(`<span style="display:grid;place-items:center;width:22px;height:22px;border-radius:6px;background:${CAND};color:#fff;font:700 11px sans-serif;border:2px solid #fff">E</span>`, 22),
}
const hot = pin('<span class="u-alert">!</span>', 20)

interface Props {
  result: Corridor | null; selectedSeg: string | null; onSelectSeg: (id: string) => void; candidate: Candidate | null
  origin: { lat: number; lon: number } | null; dest: { lat: number; lon: number } | null
  picking: boolean; onPick: (lat: number, lon: number) => void; onCandidateClick?: () => void
}

export function FlyoverMap({ result, selectedSeg, onSelectSeg, candidate, origin, dest, picking, onPick, onCandidateClick }: Props) {
  const hotspots = useMemo(() => {
    const seen = new Set<string>()
    return (result?.segments ?? []).filter(s => (s.accident_count ?? 0) > 0 && s.grid_segment_id && !seen.has(s.grid_segment_id) && seen.add(s.grid_segment_id))
  }, [result])
  return (
    <MapContainer center={[17.4, 78.45]} zoom={12} minZoom={9} maxZoom={18} className={`size-full ${picking ? 'u-picking' : ''}`} zoomSnap={0.25}>
      <TileLayer url={TILES} attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' maxNativeZoom={19} />
      <Bridge picking={picking} onPick={onPick} />
      <Camera points={result?.corridor.coords ?? null} key={result?.id ?? 'none'} />
      <Pane name="fo-cand" style={{ zIndex: 404 }}>
        {candidate && <span key={candidate.name}>
          <Polyline positions={candidate.coords} interactive={false} pathOptions={{ color: CAND, weight: 20, opacity: 0.28, lineCap: 'round', className: 'u-route-in' }} />
          <Polyline positions={candidate.coords} eventHandlers={{ click: () => onCandidateClick?.() }} pathOptions={{ color: '#fff', weight: 7, opacity: 0.95, lineCap: 'round', className: 'u-route-in' }} />
          <Polyline positions={candidate.coords} interactive={false} pathOptions={{ color: CAND, weight: 3, opacity: 1, dashArray: '10 6', lineCap: 'butt', className: 'u-route-in' }} />
        </span>}
      </Pane>
      <Pane name="fo-base" style={{ zIndex: 403 }}>
        {result && <Polyline positions={result.corridor.coords} interactive={false} pathOptions={{ color: '#2563eb', weight: 14, opacity: 0.4, dashArray: '8 8', lineCap: 'butt' }} />}
        {result?.segments.filter(s => s.bottleneck_level === 'HIGH' || s.bottleneck_level === 'CRITICAL').map(s => (
          <Polyline key={`r${s.segment_id}`} positions={s.coords} interactive={false} pathOptions={{ color: '#c62828', weight: 16, opacity: 0.22, lineCap: 'butt', className: 'u-route-in' }} />))}
      </Pane>
      <Pane name="fo-route" style={{ zIndex: 406 }}>
        {result?.segments.map(s => {
          const on = s.segment_id === selectedSeg, sev = s.bottleneck_level === 'HIGH' || s.bottleneck_level === 'CRITICAL'
          return (
            <span key={s.segment_id}>
              <Polyline positions={s.coords} interactive={false} pathOptions={{ color: '#fff', weight: on ? 12 : sev ? 11 : 9, opacity: 0.95, lineCap: 'butt' }} />
              <Polyline positions={s.coords} eventHandlers={{ click: () => onSelectSeg(s.segment_id) }}
                pathOptions={{ color: BN_COLOR[s.bottleneck_level], weight: sev ? 7 : 5, opacity: 1, lineCap: 'butt', dashArray: s.data_status === 'NO_DATA' ? '6 6' : undefined }} />
            </span>
          )
        })}
      </Pane>
      {result?.corridor.junctions.map((j, i) => <CircleMarker key={i} center={j} radius={2.5} interactive={false} pathOptions={{ color: '#475569', weight: 1, fillColor: '#fff', fillOpacity: 1 }} />)}
      {hotspots.map(s => <Marker key={`h${s.segment_id}`} icon={hot} title={`${s.accident_count} historical incident record(s) on the matched segment`}
        position={s.coords[Math.floor(s.coords.length / 2)]} eventHandlers={{ click: () => onSelectSeg(s.segment_id) }} keyboard={false} />)}
      {candidate && <Marker position={candidate.start} icon={ICON.s} title={`Candidate start (${candidate.name})`} keyboard={false} zIndexOffset={500} />}
      {candidate && <Marker position={candidate.end} icon={ICON.e} title={`Candidate end (${candidate.name})`} keyboard={false} zIndexOffset={500} />}
      {origin && <Marker position={[origin.lat, origin.lon]} icon={ICON.o} interactive={false} keyboard={false} zIndexOffset={300} />}
      {dest && <Marker position={[dest.lat, dest.lon]} icon={ICON.d} interactive={false} keyboard={false} zIndexOffset={300} />}
    </MapContainer>
  )
}

function Bridge({ picking, onPick }: { picking: boolean; onPick: (a: number, b: number) => void }) {
  useMapEvents({ click: e => { if (picking) onPick(e.latlng.lat, e.latlng.lng) } })
  return null
}

function Camera({ points }: { points: [number, number][] | null }) {
  const map = useMap()
  useEffect(() => {
    const pts = points?.length ? points : null
    if (!pts) return
    const mobile = window.innerWidth < 768
    map.flyToBounds(L.latLngBounds(pts), { paddingTopLeft: mobile ? [24, 24] : [420, 40], paddingBottomRight: mobile ? [24, Math.round(window.innerHeight * 0.5)] : [40, 40], maxZoom: 16, duration: 0.8 })
  }, [points, map])
  return null
}
