import { keepPreviousData, useQuery } from '@tanstack/react-query'
import type L from 'leaflet'
import { ArrowUpDown, ChevronDown, ChevronUp, Crosshair, LocateFixed, MapPin, Minus, Pause, Play, Plus, RotateCcw, TrafficCone, X } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ExplainCard } from '../components/user/ExplainCard'
import { PlaceCard } from '../components/user/PlaceCard'
import { PlaceSearch } from '../components/user/PlaceSearch'
import { AlertList, DiversionCard, RouteList } from '../components/user/RoutePanel'
import { UserMap, type MapFit } from '../components/user/UserMap'
import { useAuth } from '../hooks/useAuth'
import { useGeolocation } from '../hooks/useGeolocation'
import { errorText, userApi } from '../services/userApi'
import type { ExplainTarget, Level, Place, Pt, UserAlert } from '../types/user'
import { LEVEL_COLOR, LEVEL_LABEL, inArea, simClock } from '../utils/userTraffic'

const HYDERABAD = { lat: 17.4, lon: 78.45 }
const REPLAY_MS = 6000                 // one replay step (5 simulated minutes) every 6 s
const TRAFFIC_POLL_MS = 30_000
const CATS_FALLBACK = [{ id: 'hospital', label: 'Hospitals' }, { id: 'fuel', label: 'Fuel stations' }, { id: 'food', label: 'Restaurants' },
  { id: 'bus', label: 'Bus stops' }, { id: 'metro', label: 'Metro stations' }, { id: 'parking', label: 'Parking' }]
const CAT_SHORT: Record<string, string> = { hospital: 'Hospitals', fuel: 'Fuel', food: 'Food', bus: 'Bus', metro: 'Metro', parking: 'Parking' }
const r3 = (n: number) => Math.round(n * 1000) / 1000

/**
 * Rider portal: map first, one clear question ("Where are you going?"), and the answer: route, traffic, what is expected next,
 * and a better alternative when congestion develops. Public (no operator login); traffic is the FlowSense dataset REPLAY, labelled as such.
 */
export default function UserPortal() {
  const cfgQ = useQuery({ queryKey: ['u-config'], queryFn: userApi.config, staleTime: Infinity, retry: 1 })
  const cfg = cfgQ.data
  const [stepState, setStep] = useState<number | null>(null)
  const step = stepState ?? cfg?.simulation.default_step ?? 6
  const steps = cfg?.simulation.steps ?? 24
  const [playing, setPlaying] = useState(false)

  const [origin, setOrigin] = useState<Pt | null>(null)
  const [dest, setDest] = useState<Pt | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dismissedDiv, setDismissedDiv] = useState(false)
  const [picking, setPicking] = useState<'origin' | 'destination' | null>(null)
  const [cats, setCats] = useState<string[]>([])
  const [anchor, setAnchor] = useState<{ lat: number; lon: number } | null>(null)
  const [selectedPlace, setSelectedPlace] = useState<Place | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [sheetOpen, setSheetOpen] = useState(true)
  const [showTraffic, setShowTraffic] = useState(true)
  const [fit, setFit] = useState<MapFit | null>(null)
  const [why, setWhy] = useState<ExplainTarget | null>(null)     // what the rider asked FlowSense to explain
  const { logout } = useAuth()
  const navigate = useNavigate()
  const mapRef = useRef<L.Map | null>(null)
  const rootRef = useRef<HTMLElement>(null)
  const topRef = useRef<HTMLElement>(null)
  const centerRef = useRef(HYDERABAD)
  const nonce = useRef(0)
  const geo = useGeolocation()

  // mobile: the floating clock and map buttons sit just below the (variable-height) search panel
  useLayoutEffect(() => {
    const set = () => rootRef.current?.style.setProperty('--u-top', `${(topRef.current?.offsetHeight ?? 150) + 24}px`)
    set()
    if (typeof ResizeObserver === 'undefined' || !topRef.current) return
    const ro = new ResizeObserver(set); ro.observe(topRef.current)
    return () => ro.disconnect()
  }, [])

  const fly = useCallback((points: [number, number][], key: string) => setFit({ key: `${key}-${nonce.current++}`, points }), [])

  // ---- simulated traffic (dataset replay), polled at a sensible interval
  const traffic = useQuery({ queryKey: ['u-traffic', step], queryFn: () => userApi.traffic(step), enabled: Boolean(cfg),
    refetchInterval: TRAFFIC_POLL_MS, placeholderData: keepPreviousData, retry: 1 })
  const areaAlerts = useQuery({ queryKey: ['u-alerts', step], queryFn: () => userApi.alerts(step), enabled: Boolean(cfg),
    refetchInterval: TRAFFIC_POLL_MS, placeholderData: keepPreviousData, retry: 1 })

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setStep(s => Math.min((s ?? cfg?.simulation.default_step ?? 6) + 1, steps - 1)), REPLAY_MS)
    return () => clearInterval(id)
  }, [playing, steps, cfg])
  useEffect(() => { if (playing && step >= steps - 1) setPlaying(false) }, [playing, step, steps])

  // ---- routes
  const od = origin && dest ? `${r3(origin.lat)},${r3(origin.lon)}>${r3(dest.lat)},${r3(dest.lon)}` : ''
  const routeQ = useQuery({
    queryKey: ['u-route', od, step],
    queryFn: ({ signal }) => userApi.routes(origin!, dest!, step, signal),
    enabled: Boolean(origin && dest), retry: false, staleTime: 60_000,
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === od ? prev : undefined),   // replay steps keep the list; a new trip starts clean
  })
  const routes = useMemo(() => routeQ.data?.routes ?? [], [routeQ.data])
  const routeStatus = !origin || !dest ? 'idle' : routeQ.isError ? 'error' : routeQ.data ? 'ok' : 'loading'

  useEffect(() => {                                       // a new trip selects the recommended route and frames it, once
    if (!routes.length) return
    if (!routes.some(r => r.id === selectedId)) setSelectedId(routes[0].id)
  }, [routes, selectedId])
  const framed = useRef('')
  useEffect(() => {
    if (!od || !routes.length || framed.current === od) return
    framed.current = od
    fly((routes.find(r => r.id === (selectedId ?? routes[0].id)) ?? routes[0]).coords, 'route')
  }, [od, routes, selectedId, fly])

  // ---- nearby places
  const nearbyQ = useQuery({
    queryKey: ['u-nearby', anchor && r3(anchor.lat), anchor && r3(anchor.lon), [...cats].sort().join(',')],
    queryFn: ({ signal }) => userApi.nearby(anchor!.lat, anchor!.lon, cats, 1200, signal),
    enabled: Boolean(anchor && cats.length), retry: false, staleTime: 5 * 60_000,
  })
  const places = cats.length ? nearbyQ.data?.places ?? [] : []

  // ---- actions
  const clearTrip = () => { setDest(null); setOrigin(null); setSelectedId(null); setDismissedDiv(false); setPicking(null); framed.current = '' }
  const chooseDest = (p: Pt) => {
    setDest(p); setSelectedId(null); setDismissedDiv(false); setSelectedPlace(null); framed.current = ''
    if (!origin && geo.here && cfg && inArea(geo.here, cfg.service_area)) setOrigin({ label: 'Your location', lat: geo.here.lat, lon: geo.here.lon, source: 'gps' })
    if (!origin) fly([[p.lat, p.lon]], 'dest')
  }
  const chooseOrigin = (p: Pt) => { setOrigin(p); setSelectedId(null); setDismissedDiv(false); framed.current = '' }
  const swap = () => { const o = origin; setOrigin(dest); setDest(o); setSelectedId(null); framed.current = '' }
  const selectRoute = (id: string) => {
    setSelectedId(id)
    const r = routes.find(x => x.id === id)
    if (r) fly(r.coords, 'sel')
  }

  const locateMe = useCallback(async (asOrigin: boolean) => {
    setNotice(null)
    const h = await geo.locate()
    if (!h) return null
    if (cfg && !inArea(h, cfg.service_area)) { setNotice('You appear to be outside the Hyderabad area FlowSense covers. Search for a starting point instead.'); return null }
    if (asOrigin) chooseOrigin({ label: 'Your location', lat: h.lat, lon: h.lon, source: 'gps' })
    else mapRef.current?.flyTo([h.lat, h.lon], 15, { duration: 0.9 })
    return h
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo.locate, cfg])

  const toggleCat = (id: string) => {
    setSelectedPlace(null)
    setCats(c => {
      const next = c.includes(id) ? c.filter(x => x !== id) : [...c, id]
      if (!c.length && next.length) setAnchor(geo.here && cfg && inArea(geo.here, cfg.service_area) ? { lat: geo.here.lat, lon: geo.here.lon } : centerRef.current)
      return next
    })
  }
  const directionsTo = (pl: Place) => {
    chooseDest({ label: pl.name ?? pl.category_label, lat: pl.lat, lon: pl.lon, source: 'place' })
    setSelectedPlace(null)
  }
  const pick = (lat: number, lon: number) => {
    const p: Pt = { label: 'Pinned location', lat, lon, source: 'pin' }
    if (picking === 'origin') chooseOrigin(p); else chooseDest(p)
    setPicking(null)
  }

  const alerts = useMemo<UserAlert[]>(() => {
    const list = [...(areaAlerts.data?.alerts ?? []), ...(routeQ.data?.alerts ?? [])]
    return list
  }, [areaAlerts.data, routeQ.data])
  const mapAlerts = useMemo(() => alerts.filter(a => a.lat != null && a.lon != null), [alerts])
  const routeAlerts = routeQ.data?.alerts ?? []
  const categories = cfg?.categories ?? CATS_FALLBACK
  const zones = traffic.data?.zones ?? []
  const inTrip = Boolean(dest)
  // explain what the rider tapped; otherwise, in a trip, the worst congestion on the selected route (shown compact)
  const selRoute = routes.find(r => r.id === selectedId)
  const routeWorst = inTrip && selRoute?.worst && (selRoute.traffic_level === 'heavy' || selRoute.traffic_level === 'severe') ? selRoute.worst : null
  const explainTarget: ExplainTarget | null = why ?? (routeWorst ? { lat: routeWorst.lat, lon: routeWorst.lon } : null)
  const nearbyMessage = cats.length && anchor
    ? nearbyQ.isFetching ? 'Finding places nearby…'
      : nearbyQ.isError ? errorText(nearbyQ.error)
        : nearbyQ.data && places.length === 0 ? 'No places found within 1.2 km. Move the map and use “Search this area”.' : null
    : null

  const originQuick = [{
    key: 'gps', label: 'Your location',
    hint: geo.status === 'locating' ? 'Locating…' : geo.status === 'denied' ? 'Location access is unavailable' : geo.status === 'unavailable' ? 'Location is unavailable on this device' : 'Use your current position',
    onPick: () => { void locateMe(true) },
  }]

  return (
    <main ref={rootRef} className="fixed inset-0 bg-[#e9edf1] overflow-hidden" aria-label="FlowSense map">
      <div className="absolute inset-0">
        <UserMap center={cfg?.center ?? HYDERABAD} zones={zones} showTraffic={showTraffic} routes={routes} selectedRouteId={selectedId} onSelectRoute={selectRoute}
          origin={origin} dest={dest} here={geo.here} places={places} selectedPlaceId={selectedPlace?.id ?? null} onSelectPlace={setSelectedPlace}
          alerts={mapAlerts} picking={Boolean(picking)} onPick={pick} fit={fit} onReady={m => { mapRef.current = m }}
          onMove={c => { centerRef.current = c }} onExplain={setWhy} />
      </div>

      {/* ---------------- left panel (desktop) / top bar + bottom sheet (mobile) ---------------- */}
      <div className="absolute z-[1000] inset-x-3 top-3 md:right-auto md:w-[400px] md:bottom-3 flex flex-col gap-2.5 pointer-events-none">
        <section ref={topRef} aria-label={inTrip ? 'Directions' : 'Search'} className="pointer-events-auto rounded-2xl border border-line bg-white/97 p-3 shadow-[0_10px_30px_-14px_rgba(22,24,29,.35)]">
          <div className="flex items-center justify-between mb-2.5">
            <img src="/flowsense-logo.png" alt="FlowSense AI" width={548} height={120} className="h-6 w-auto" />
            {inTrip
              ? <button type="button" onClick={clearTrip} aria-label="Close directions" className="text-ink-3 hover:text-ink"><X className="size-5" aria-hidden /></button>
              : <button type="button" onClick={() => { void logout().finally(() => navigate('/login', { replace: true })) }} className="text-[11.5px] text-ink-3 hover:text-ink">Log out</button>}
          </div>

          {!inTrip ? (
            <PlaceSearch label="Search destination" placeholder="Where are you going?" value="" onSelect={chooseDest} large />
          ) : (
            <div className="space-y-2">
              <PlaceSearch label="Starting point" placeholder="Choose a starting point" value={origin?.label ?? ''} onSelect={chooseOrigin} quick={originQuick}
                onClear={() => { setOrigin(null); setSelectedId(null); framed.current = '' }} autoFocus={!origin}
                leading={<span className="size-3.5 rounded-full border-[3px] border-accent bg-white shrink-0" aria-hidden />}
                trailing={<button type="button" onClick={() => setPicking(p => (p === 'origin' ? null : 'origin'))} aria-label="Choose starting point on map" aria-pressed={picking === 'origin'}
                  className={`shrink-0 ${picking === 'origin' ? 'text-accent' : 'text-ink-3 hover:text-ink'}`}><Crosshair className="size-4" aria-hidden /></button>} />
              <div className="flex items-center gap-2 pl-1">
                <span className="h-px flex-1 bg-line" />
                <button type="button" onClick={swap} disabled={!origin || !dest} aria-label="Swap start and destination" className="text-ink-3 hover:text-ink disabled:opacity-40"><ArrowUpDown className="size-4" aria-hidden /></button>
                <span className="h-px flex-1 bg-line" />
              </div>
              <PlaceSearch label="Destination" placeholder="Search destination" value={dest?.label ?? ''} onSelect={chooseDest}
                leading={<span className="size-3.5 rounded-full bg-ink shrink-0" aria-hidden />}
                trailing={<button type="button" onClick={() => setPicking(p => (p === 'destination' ? null : 'destination'))} aria-label="Choose destination on map" aria-pressed={picking === 'destination'}
                  className={`shrink-0 ${picking === 'destination' ? 'text-accent' : 'text-ink-3 hover:text-ink'}`}><Crosshair className="size-4" aria-hidden /></button>} />
            </div>
          )}

          {!inTrip && (
            <div className="mt-2.5" role="group" aria-label="Nearby places">
              <div className="flex gap-1.5 overflow-x-auto pb-0.5 [scrollbar-width:none]">
                {categories.map(c => {
                  const on = cats.includes(c.id)
                  return (
                    <button key={c.id} type="button" onClick={() => toggleCat(c.id)} aria-pressed={on}
                      className={`shrink-0 h-8 px-3 rounded-full border text-[12.5px] ${on ? 'bg-ink text-paper border-ink' : 'bg-white text-ink-2 border-line-strong hover:border-ink-3'}`}>{CAT_SHORT[c.id] ?? c.label}</button>
                  )
                })}
              </div>
              {nearbyMessage && (
                <p role={nearbyQ.isError ? 'alert' : 'status'} className={`mt-2 text-[12.5px] ${nearbyQ.isError ? 'text-[#9f2a1c]' : 'text-ink-3'}`}>
                  {nearbyMessage} {nearbyQ.isError && <button type="button" onClick={() => nearbyQ.refetch()} className="underline font-semibold">Try again</button>}</p>
              )}
              {cats.length > 0 && !nearbyQ.isFetching && (
                <button type="button" onClick={() => setAnchor(centerRef.current)} className="mt-1.5 text-[12px] text-accent hover:underline">Search this area</button>
              )}
            </div>
          )}
          {geo.status === 'denied' && !notice && <p role="status" className="mt-2 text-[12px] text-ink-3">Location access is unavailable. You can search for a starting point instead.</p>}
          {notice && <p role="status" className="mt-2 text-[12px] text-ink-2">{notice} <button type="button" onClick={() => setNotice(null)} className="underline">Dismiss</button></p>}
        </section>

        {/* results: side panel on desktop, bottom sheet on mobile */}
        {(inTrip || selectedPlace || alerts.length > 0 || why) && (
          <div className="pointer-events-auto fixed inset-x-0 bottom-0 max-h-[52dvh] overflow-y-auto rounded-t-2xl border border-line bg-paper/98 px-3 pb-3 pt-2 shadow-[0_-10px_30px_-14px_rgba(22,24,29,.35)]
            md:static md:max-h-[calc(100dvh-210px)] md:min-h-0 md:rounded-2xl md:border md:pt-3 md:shadow-[0_10px_30px_-14px_rgba(22,24,29,.35)]">
            <button type="button" onClick={() => setSheetOpen(o => !o)} aria-expanded={sheetOpen} aria-label={sheetOpen ? 'Collapse panel' : 'Expand panel'}
              className="md:hidden w-full pb-2 flex flex-col items-center gap-1"><span className="u-sheet-handle" />{sheetOpen ? <ChevronDown className="size-4 text-ink-3" aria-hidden /> : <ChevronUp className="size-4 text-ink-3" aria-hidden />}</button>
            <div className={`space-y-2.5 ${sheetOpen ? '' : 'max-md:hidden'}`}>
              {selectedPlace && <PlaceCard place={selectedPlace} onClose={() => setSelectedPlace(null)} onDirections={() => directionsTo(selectedPlace)} />}

              {inTrip && !origin && (
                <p className="rounded-xl border border-line bg-white px-3.5 py-3 text-[13px] text-ink-2 flex gap-2"><MapPin className="size-4 shrink-0 mt-0.5 text-ink-3" aria-hidden />
                  Choose a starting point to see routes. Use your location or search for a place.</p>
              )}
              {routeQ.data?.diversion && (
                <DiversionCard diversion={routeQ.data.diversion} routes={routes} selectedId={selectedId} dismissed={dismissedDiv}
                  onView={() => selectRoute(routeQ.data!.diversion!.alternative_route_id)} onKeep={() => { setDismissedDiv(true); selectRoute(routeQ.data!.diversion!.current_route_id) }} />
              )}
              {explainTarget && (
                <ExplainCard key={`${explainTarget.segment_id ?? ''}${explainTarget.lat.toFixed(3)}${explainTarget.lon.toFixed(3)}${explainTarget.open ? 'o' : ''}`}
                  target={explainTarget} step={step} auto={!why} onClose={why ? () => setWhy(null) : undefined}
                  saving={routeQ.data?.diversion?.estimated_saving_min} onViewAlternative={routeQ.data?.diversion && selectedId !== routeQ.data.diversion.alternative_route_id
                    ? () => selectRoute(routeQ.data!.diversion!.alternative_route_id) : undefined} />
              )}
              {inTrip && <AlertList alerts={routeAlerts} onWhy={a => setWhy({ lat: a.lat!, lon: a.lon!, open: true })} />}
              {inTrip && origin && <RouteList status={routeStatus} routes={routes} selectedId={selectedId} onSelect={selectRoute} onRetry={() => routeQ.refetch()}
                error={routeQ.isError ? errorText(routeQ.error) : undefined} note={routeQ.data?.traffic_note} />}

              {!inTrip && !selectedPlace && alerts.length > 0 && (
                <div>
                  <h2 className="px-1 mb-1.5 text-[11px] font-semibold tracking-wider uppercase text-ink-3">Traffic alerts nearby</h2>
                  <AlertList alerts={alerts} onWhy={a => setWhy({ lat: a.lat!, lon: a.lon!, segment_id: a.segment_id, open: true })} />
                  {alerts[0].lat != null && <button type="button" onClick={() => fly([[alerts[0].lat!, alerts[0].lon!]], 'alert')} className="mt-1.5 px-1 text-[12.5px] text-accent hover:underline">Show on map</button>}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {picking && (
        <div role="status" className="absolute z-[1000] left-1/2 -translate-x-1/2 top-[calc(var(--u-top,150px)+48px)] md:top-4 pointer-events-auto rounded-full bg-ink text-paper text-[13px] px-4 py-2 shadow-lg flex items-center gap-3">
          Tap the map to choose the {picking === 'origin' ? 'starting point' : 'destination'}
          <button type="button" onClick={() => setPicking(null)} className="underline">Cancel</button></div>
      )}

      {/* ---------------- simulation clock + map controls ---------------- */}
      <div className="absolute z-[900] right-3 top-[var(--u-top,76px)] md:top-3 flex items-center gap-1.5 rounded-full border border-line bg-white/95 pl-3 pr-1.5 h-9 text-[12px] shadow-sm"
        title={cfg?.traffic_source ?? 'FlowSense traffic simulation'}>
        <span className="text-ink-3">Simulated traffic</span>
        <span className="num text-ink">{traffic.isError ? 'unavailable' : simClock(traffic.data?.time)}</span>
        <button type="button" onClick={() => setPlaying(p => !p)} aria-label={playing ? 'Pause replay' : 'Replay simulated traffic'} aria-pressed={playing}
          className="size-7 grid place-items-center rounded-full hover:bg-paper text-ink-2">{playing ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}</button>
        {stepState !== null && stepState !== cfg?.simulation.default_step && (
          <button type="button" onClick={() => { setStep(null); setPlaying(false) }} aria-label="Reset simulated time" className="size-7 grid place-items-center rounded-full hover:bg-paper text-ink-2"><RotateCcw className="size-3.5" aria-hidden /></button>
        )}
      </div>

      <div className="absolute z-[900] right-3 top-[calc(var(--u-top,76px)+48px)] md:top-auto md:bottom-9 flex flex-col gap-2" role="group" aria-label="Map controls">
        <span className="max-md:hidden contents"><MapBtn label="Zoom in" onClick={() => mapRef.current?.zoomIn()}><Plus className="size-4" aria-hidden /></MapBtn>
        <MapBtn label="Zoom out" onClick={() => mapRef.current?.zoomOut()}><Minus className="size-4" aria-hidden /></MapBtn></span>
        <MapBtn label="Show my location" onClick={() => { void locateMe(false) }} busy={geo.status === 'locating'}><LocateFixed className="size-4" aria-hidden /></MapBtn>
        <MapBtn label={showTraffic ? 'Hide simulated traffic' : 'Show simulated traffic'} onClick={() => setShowTraffic(s => !s)} on={showTraffic}><TrafficCone className="size-4" aria-hidden /></MapBtn>
      </div>

      <TrafficLegend counts={traffic.data?.counts} />
      {cfgQ.isError && <div role="alert" className="absolute z-[1100] left-1/2 -translate-x-1/2 bottom-24 md:bottom-3 rounded-xl border border-[#f1c9c4] bg-[#fdf3f2] px-4 py-2.5 text-[13px] text-[#9f2a1c]">
        FlowSense couldn’t load its settings. <button type="button" onClick={() => cfgQ.refetch()} className="underline font-semibold">Retry</button></div>}
    </main>
  )
}

function MapBtn({ label, onClick, children, on, busy }: { label: string; onClick: () => void; children: React.ReactNode; on?: boolean; busy?: boolean }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} aria-pressed={on} disabled={busy}
      className={`size-10 rounded-full border bg-white shadow-sm grid place-items-center hover:bg-paper ${on ? 'border-ink text-ink' : 'border-line-strong text-ink-2'} ${busy ? 'animate-pulse' : ''}`}>{children}</button>
  )
}

/** Small legend. Green only appears along a route: the simulation is not real road geometry, so free-flow areas are not painted on the map. */
function TrafficLegend({ counts }: { counts?: Record<Level, number> }) {
  const shown: Level[] = ['free_flow', 'moderate', 'heavy', 'severe', 'no_data']
  return (
    <div className="hidden md:flex absolute z-[900] left-[424px] bottom-3 items-center gap-3 rounded-full border border-line bg-white/95 px-3.5 h-8 text-[11.5px] text-ink-2 shadow-sm" aria-label="Traffic legend">
      {shown.map(l => (
        <span key={l} className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: LEVEL_COLOR[l] }} aria-hidden />{l === 'no_data' ? 'No data' : LEVEL_LABEL[l]}</span>
      ))}
      {counts && <span className="text-ink-3 border-l border-line pl-3">{counts.heavy + counts.severe + counts.moderate} congested areas (simulated)</span>}
    </div>
  )
}
