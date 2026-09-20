import { useQuery } from '@tanstack/react-query'
import L, { type LatLngTuple } from 'leaflet'
import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Layers as LayersIcon } from 'lucide-react'
import { MapContainer, Marker, Pane, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import { api } from '../services/api'
import type { TrafficState } from '../types'
import { ErrorState, Loading } from './ui'

/**
 * Map hierarchy (bottom → top):
 *   basemap (OSM, dominant) → faint simulation network (one canvas layer, drawn once)
 *   → white casings → problem / corridor segments → selected halo + segment → incident markers.
 * Only segments the caller has something to say about are drawn above the faint network.
 */
export interface SegmentStyle {
  color: string; weight?: number; opacity?: number; dashed?: boolean
  animate?: boolean          // marching dashes — propagation paths only
  label?: string             // small permanent label (e.g. time to impact)
}
export interface TipData { title: string; badge?: string; rows: [string, string][] }
export interface IncidentMark { id: string; severity: TrafficState }
export interface Fit { key: string; ids: string[]; maxZoom?: number }

interface Props {
  styleFor: (segmentId: string) => SegmentStyle | null   // null = leave to the faint base network
  tooltipFor?: (segmentId: string) => TipData | null
  onSelect?: (segmentId: string) => void
  selected?: string | null
  incidents?: IncidentMark[]
  showNetwork?: boolean
  /** Frame these segments whenever `key` changes (smooth fly-to). Never moves on other re-renders. */
  fit?: Fit | null
  /** 'always': wheel zooms immediately. 'on-click': embedded maps zoom only after a click, so the page can still scroll. */
  wheel?: 'always' | 'on-click'
  /** Maps sharing a syncKey move together. Only the first (master) should get `fit`-driven camera moves. */
  syncKey?: string
  follower?: boolean
  badge?: ReactNode
  overlay?: ReactNode        // bottom-left card
  legend?: ReactNode
  controls?: ReactNode
  className?: string
  title?: string
}

// Standard OpenStreetMap tiles — no API key. Recoloured in CSS to a pale, road-first look.
// ponytail: the public OSM tile server is fine for demos, not for production traffic (tile usage policy);
// swap VITE_TILE_URL for a hosted tile provider when deploying at scale.
const TILES = (import.meta.env.VITE_TILE_URL as string | undefined) || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
export const NETWORK_GREY = '#5f6b7a'

export function useNetwork() {
  return useQuery({ queryKey: ['network'], queryFn: api.network, staleTime: Infinity })
}

type Feature = { properties: { segment_id: string }; geometry: { coordinates: number[][] } }

/** faint base network: barely noticeable, quieter still when zoomed in on real streets */
const baseStyle = (z: number) => ({
  color: NETWORK_GREY, lineCap: 'round' as const,
  weight: z >= 14 ? 1.4 : z >= 12.5 ? 1 : 0.8,
  opacity: z >= 14 ? 0.18 : z >= 12.5 ? 0.2 : 0.15,
})
const zoomScale = (z: number) => (z <= 11.5 ? 0.8 : z >= 14 ? 1.45 : z >= 13 ? 1.2 : 1)

/* ------------------------------------------------------------------ tooltips */
const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
/** HTML twin of <TipView/> — the faint canvas layer can only bind string tooltips. */
export const tipHtml = (d: TipData) =>
  `<div class="fs-tipbox"><div class="fs-tiphead"><b>${esc(d.title)}</b>${d.badge ? `<span>${esc(d.badge)}</span>` : ''}</div>` +
  d.rows.map(([k, v]) => `<div class="fs-tiprow"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('') + '</div>'

function TipView({ data }: { data: TipData }) {
  return (
    <div className="fs-tipbox">
      <div className="fs-tiphead"><b>{data.title}</b>{data.badge && <span>{data.badge}</span>}</div>
      {data.rows.map(([k, v]) => <div key={k} className="fs-tiprow"><span>{k}</span><span>{v}</span></div>)}
    </div>
  )
}

/* ------------------------------------------------------------------ the map */
export function TrafficMap({ styleFor, tooltipFor, onSelect, selected, incidents, showNetwork = true, fit, wheel = 'always', syncKey,
  follower, badge, overlay, legend, controls, className = '', title = 'Traffic Simulation Network' }: Props) {
  const net = useNetwork()
  const [tileError, setTileError] = useState(false)
  const [zoom, setZoom] = useState(12)
  const [wheelHint, setWheelHint] = useState(false)
  // the legend starts collapsed on small maps so it never covers the corridor being looked at
  const [wide, setWide] = useState<boolean | null>(null)
  const measure = (el: HTMLDivElement | null) => { if (el && wide === null) setWide(el.offsetWidth >= 720 && el.offsetHeight >= 480) }
  const onSelectRef = useRef(onSelect); onSelectRef.current = onSelect
  const tipRef = useRef(tooltipFor); tipRef.current = tooltipFor

  const features = useMemo(() => (net.data?.geojson.features ?? []) as Feature[], [net.data])
  // stable geometry cache: [lat, lon] pairs per segment, computed once
  const geo = useMemo(() => new Map(features.map(f => [f.properties.segment_id, f.geometry.coordinates.map(([lon, lat]) => [lat, lon] as LatLngTuple)])), [features])
  const mid = useMemo(() => new Map([...geo].map(([id, [a, b]]) => [id, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] as LatLngTuple])), [geo])

  if (net.isLoading) return <div className={className}><Loading label="Loading network" /></div>
  if (net.error || !net.data) return <div className={className}><ErrorState error={net.error} what="network" /></div>
  const b = net.data.bounds
  const pad = 0.012
  const cityBounds = L.latLngBounds([[b[0][0] - pad, b[0][1] - pad], [b[1][0] + pad, b[1][1] + pad]])
  return (
    <div ref={measure} className={`relative ${className}`}>
      <MapContainer bounds={cityBounds} boundsOptions={{ padding: [8, 8] }}
        className="size-full rounded-md" zoomSnap={0.25} zoomDelta={0.5} wheelPxPerZoomLevel={130} wheelDebounceTime={40}
        minZoom={10} maxZoom={17} scrollWheelZoom={wheel === 'always'} attributionControl>
        <TileLayer url={TILES} attribution={ATTRIBUTION} maxNativeZoom={19} eventHandlers={{ tileerror: () => setTileError(true) }} />
        <MapWatch onZoom={setZoom} wheel={wheel} onHint={setWheelHint} />
        {!follower && <Camera fit={fit} geo={geo} reserveBottom={overlay ? 170 : 0} />}
        {syncKey && <SyncMaps syncKey={syncKey} adopt={Boolean(follower)} />}
        <Pane name="fs-base" style={{ zIndex: 401 }}>
          <BaseNetwork features={features} visible={showNetwork} zoom={zoom} onSelectRef={onSelectRef} tipRef={tipRef} />
        </Pane>
        <Overlay geo={geo} styleFor={styleFor} tooltipFor={tooltipFor} onSelect={onSelect} selected={selected} zoom={zoom} />
        <Pane name="fs-marker" style={{ zIndex: 406 }}>
          {incidents?.map(m => mid.get(m.id) && (
            <Marker key={m.id} position={mid.get(m.id)!} icon={iconFor(m.severity)} keyboard={false} interactive={Boolean(onSelect)}
              eventHandlers={onSelect ? { click: () => onSelect(m.id) } : undefined} />
          ))}
        </Pane>
      </MapContainer>

      <div className="absolute top-2 left-12 right-2 z-[500] flex flex-wrap gap-1.5 items-start pointer-events-none">
        <div className="fs-chip !flex-col !items-start !gap-0 pointer-events-auto" title={net.data.disclaimer}>
          <span className="eyebrow text-ink whitespace-nowrap">{title}</span>
          <span className="text-ink-3">Dataset topology on an OSM basemap · not actual road geometry</span>
        </div>
        {badge && <div className="pointer-events-auto">{badge}</div>}
        {controls && <div className="pointer-events-auto">{controls}</div>}
      </div>
      {overlay && <div className="absolute bottom-6 left-2 z-[500] max-w-[min(360px,calc(100%-16px))]">{overlay}</div>}
      {wheelHint && <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-[500] fs-chip pointer-events-none">Click the map, then scroll to zoom</div>}
      {tileError && (
        <div className="absolute bottom-7 left-2 z-[500] bg-panel border border-line rounded-sm px-2 py-1 text-[11px] text-ink-2">
          Basemap tiles unavailable — network overlay still shown
        </div>
      )}
      {legend && wide !== null && <LegendOpen.Provider value={wide}><div className="absolute bottom-6 right-2 z-[500]">{legend}</div></LegendOpen.Provider>}
    </div>
  )
}

/* ------------------------------------------------------------------ base network (canvas, drawn once) */
function BaseNetwork({ features, visible, zoom, onSelectRef, tipRef }: {
  features: Feature[]; visible: boolean; zoom: number
  onSelectRef: { current?: (id: string) => void }; tipRef: { current?: (id: string) => TipData | null }
}) {
  const map = useMap()
  const layer = useRef<L.GeoJSON | null>(null)
  useEffect(() => {
    const g = L.geoJSON({ type: 'FeatureCollection', features } as unknown as GeoJSON.FeatureCollection, {
      pane: 'fs-base',
      renderer: L.canvas({ pane: 'fs-base', tolerance: 8, padding: 0.4 }),
      style: () => baseStyle(map.getZoom()),
      onEachFeature: (f: GeoJSON.Feature, l: L.Layer) => {
        const id = (f.properties as { segment_id: string }).segment_id
        l.on('click', () => onSelectRef.current?.(id))
        l.bindTooltip(() => { const d = tipRef.current?.(id); return d ? tipHtml(d) : esc(id) }, { sticky: true, className: 'fs-tip', direction: 'top', opacity: 1 })
      },
    } as unknown as L.GeoJSONOptions)
    layer.current = g
    return () => { g.remove(); layer.current = null }
  }, [map, features, onSelectRef, tipRef])
  useEffect(() => {
    const g = layer.current
    if (!g) return
    if (visible) { if (!map.hasLayer(g)) g.addTo(map); g.setStyle(baseStyle(zoom)) } else g.remove()
  }, [map, visible, zoom, features])
  return null
}

/* ------------------------------------------------------------------ overlay: only what matters */
function Overlay({ geo, styleFor, tooltipFor, onSelect, selected, zoom }: {
  geo: Map<string, LatLngTuple[]>; zoom: number
} & Pick<Props, 'styleFor' | 'tooltipFor' | 'onSelect' | 'selected'>) {
  const k = zoomScale(zoom)
  const { mains, selectedItem } = useMemo(() => {
    const mains: { id: string; st: SegmentStyle }[] = []
    let selectedItem: { id: string; st: SegmentStyle } | null = null
    for (const id of geo.keys()) {
      const st = styleFor(id)
      if (id === selected) { selectedItem = { id, st: st ?? { color: '#16181d', weight: 4 } }; continue }
      if (st) mains.push({ id, st })
    }
    mains.sort((a, b) => (a.st.weight ?? 3) - (b.st.weight ?? 3))   // heavier lines end up on top
    return { mains, selectedItem }
  }, [geo, styleFor, selected])
  const click = (id: string) => (onSelect ? { click: () => onSelect(id) } : undefined)
  const selW = Math.max(selectedItem?.st.weight ?? 4, 4.5) * k + 1.5
  return (
    <>
      <Pane name="fs-casing" style={{ zIndex: 402 }}>
        {mains.filter(m => (m.st.weight ?? 3) >= 3.5).map(m => (
          <Polyline key={m.id} positions={geo.get(m.id)!} interactive={false}
            pathOptions={{ color: '#ffffff', weight: (m.st.weight ?? 3) * k + 3.5, opacity: 0.9, lineCap: 'round', lineJoin: 'round', className: 'fs-seg' }} />
        ))}
      </Pane>
      <Pane name="fs-overlay" style={{ zIndex: 403 }}>
        {mains.map(({ id, st }) => (
          // path class is fixed at creation, so the animation flag is part of the key
          <Polyline key={`${id}-${st.animate ? 'a' : 's'}`} positions={geo.get(id)!} eventHandlers={click(id)}
            pathOptions={{ color: st.color, weight: (st.weight ?? 3) * k, opacity: st.opacity ?? 0.95, lineCap: 'round', lineJoin: 'round',
              dashArray: st.dashed || st.animate ? '9 7' : undefined, className: st.animate ? 'fs-seg fs-march' : 'fs-seg' }}>
            {st.label ? <Tooltip permanent direction="center" className="fs-eta">{st.label}</Tooltip>
              : tooltipFor && <MaybeTip id={id} tooltipFor={tooltipFor} />}
          </Polyline>
        ))}
      </Pane>
      <Pane name="fs-halo" style={{ zIndex: 404 }}>
        {selectedItem && <Polyline key={`h-${selectedItem.id}`} positions={geo.get(selectedItem.id)!} interactive={false}
          pathOptions={{ color: '#ffffff', weight: selW + 7, opacity: 0.95, lineCap: 'round', lineJoin: 'round' }} />}
      </Pane>
      <Pane name="fs-top" style={{ zIndex: 405 }}>
        {selectedItem && (
          <Polyline key={`s-${selectedItem.id}`} positions={geo.get(selectedItem.id)!} eventHandlers={click(selectedItem.id)}
            pathOptions={{ color: selectedItem.st.color, weight: selW, opacity: 1, lineCap: 'round', lineJoin: 'round', className: 'fs-seg' }}>
            <Tooltip permanent direction="top" offset={[0, -6]} className="fs-label">{selectedItem.id}</Tooltip>
          </Polyline>
        )}
      </Pane>
    </>
  )
}

function MaybeTip({ id, tooltipFor }: { id: string; tooltipFor: (id: string) => TipData | null }) {
  const d = tooltipFor(id)
  return d ? <Tooltip sticky className="fs-tip" opacity={1}><TipView data={d} /></Tooltip> : null
}

/* ------------------------------------------------------------------ incident markers (small, severity-scaled) */
const ICONS = new Map<string, L.DivIcon>()
function iconFor(sev: TrafficState): L.DivIcon {
  const key = sev.toLowerCase()
  if (!ICONS.has(key)) ICONS.set(key, L.divIcon({ className: '', html: `<span class="fs-inc fs-inc-${key}"></span>`, iconSize: [28, 28], iconAnchor: [14, 14] }))
  return ICONS.get(key)!
}

/* ------------------------------------------------------------------ camera, zoom and wheel behaviour */
function MapWatch({ onZoom, wheel, onHint }: { onZoom: (z: number) => void; wheel: 'always' | 'on-click'; onHint: (v: boolean) => void }) {
  const map = useMap()
  useEffect(() => { onZoom(map.getZoom()) }, [map, onZoom])
  useMapEvents({ zoomend: () => onZoom(map.getZoom()) })
  useEffect(() => {
    if (wheel === 'always') { map.scrollWheelZoom.enable(); return }
    // embedded maps: the page keeps scrolling until the user clicks into the map; leaving turns it off again
    map.scrollWheelZoom.disable()
    const on = () => { map.scrollWheelZoom.enable(); onHint(false) }
    const enter = () => { if (!map.scrollWheelZoom.enabled()) onHint(true) }
    const leave = () => { map.scrollWheelZoom.disable(); onHint(false) }
    map.on('click', on); map.on('mouseover', enter); map.on('mouseout', leave)
    return () => { map.off('click', on); map.off('mouseover', enter); map.off('mouseout', leave) }
  }, [map, wheel, onHint])
  return null
}

/** Frames `fit` before the first paint, then flies smoothly whenever fit.key changes — never on ordinary re-renders. */
function Camera({ fit, geo, reserveBottom }: { fit?: Fit | null; geo: Map<string, LatLngTuple[]>; reserveBottom: number }) {
  const map = useMap()
  const done = useRef<string | undefined>(undefined)
  const first = useRef(true)
  useLayoutEffect(() => {
    if (!fit || done.current === fit.key) return
    const pts = fit.ids.flatMap(id => geo.get(id) ?? [])
    if (!pts.length) return
    done.current = fit.key
    const h = map.getSize().y                             // margins scale with the map, so small embedded maps still zoom in
    const opts = {
      paddingTopLeft: [40, Math.min(96, h * 0.16)] as [number, number],
      paddingBottomRight: [40, Math.min(reserveBottom || 80, h * (reserveBottom ? 0.34 : 0.2))] as [number, number],
      maxZoom: fit.maxZoom ?? 14.5,
    }
    const bounds = L.latLngBounds(pts)
    if (first.current) { first.current = false; map.fitBounds(bounds, { ...opts, animate: false }) }
    else map.flyToBounds(bounds, { ...opts, duration: 0.9, easeLinearity: 0.3 })
  }, [fit?.key]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

const syncGroups = new Map<string, Set<L.Map>>()
let syncLock = false
/** Keep every map sharing `syncKey` on the same view. The follower adopts the master's view on join;
 *  a master pushes its view to followers that joined earlier — so mount order does not matter. */
function SyncMaps({ syncKey, adopt }: { syncKey: string; adopt: boolean }) {
  const map = useMap()
  useEffect(() => {
    const group = syncGroups.get(syncKey) ?? new Set<L.Map>()
    syncGroups.set(syncKey, group)
    const push = () => {
      if (syncLock) return
      syncLock = true
      for (const m of group) if (m !== map) m.setView(map.getCenter(), map.getZoom(), { animate: false })
      syncLock = false
    }
    const peer = [...group][0]
    group.add(map)
    if (adopt && peer) map.setView(peer.getCenter(), peer.getZoom(), { animate: false })
    else if (!adopt) push()
    map.on('move', push)
    return () => { map.off('move', push); group.delete(map); if (!group.size) syncGroups.delete(syncKey) }
  }, [map, syncKey, adopt])
  return null
}

/* ------------------------------------------------------------------ legend + layer toggles */

export function LegendRow({ color, label, dashed, weight = 3, dot }: { color: string; label: string; dashed?: boolean; weight?: number; dot?: TrafficState }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-ink-2 leading-[18px]">
      {dot ? <span className={`fs-inc fs-inc-${dot.toLowerCase()} fs-inc-static`} /> : (
        <span className="inline-block w-5" style={{ height: weight, borderRadius: 2,
          background: dashed ? `repeating-linear-gradient(90deg, ${color} 0 4px, transparent 4px 7px)` : color }} />
      )}
      {label}
    </div>
  )
}

const LegendOpen = createContext(true)

export function Legend({ sections }: { sections: { title: string; rows: ReactNode }[] }) {
  return (
    <details open={useContext(LegendOpen)} className="bg-panel/95 border border-line rounded-sm px-2.5 py-1.5 min-w-[140px] group">
      <summary className="eyebrow cursor-pointer select-none list-none flex justify-between gap-3">
        Legend <span className="text-ink-3 group-open:rotate-180 transition-transform">▾</span></summary>
      <div className="space-y-1.5 mt-1">
        {sections.map(s => (
          <div key={s.title}><div className="eyebrow text-[9.5px] mb-0.5">{s.title}</div>{s.rows}</div>
        ))}
      </div>
    </details>
  )
}

export function LayerToggles({ layers }: { layers: { label: string; on: boolean; set: (v: boolean) => void; disabled?: boolean }[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open} aria-haspopup="true"
        className="fs-card !rounded-sm h-7 px-2.5 inline-flex items-center gap-1.5 text-[11.5px] text-ink hover:border-ink-3">
        <LayersIcon className="size-3.5" aria-hidden /> Layers <span className="text-ink-3 num">{layers.filter(l => l.on).length}/{layers.length}</span>
      </button>
      {open && (
        <div role="group" aria-label="Map layers" className="fs-card !rounded-sm absolute left-0 top-full mt-1 p-2 space-y-1 min-w-[190px] z-10">
          {layers.map(l => (
            <label key={l.label} className={`flex items-center gap-2 text-[11.5px] ${l.disabled ? 'text-ink-3' : 'text-ink cursor-pointer'}`}>
              <input type="checkbox" checked={l.on} disabled={l.disabled} onChange={e => l.set(e.target.checked)} className="accent-[#16181d]" />
              {l.label}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
