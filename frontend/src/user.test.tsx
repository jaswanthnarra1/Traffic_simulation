import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './hooks/useAuth'
import Login from './pages/Login'
import UserPortal from './pages/UserPortal'
import { UserApiError, userApi } from './services/userApi'
import { LEVEL_COLOR, delayText, inArea, meters, minutes, safeUrl, simClock } from './utils/userTraffic'

// Leaflet needs real layout; the map is stubbed so these tests exercise the portal's logic and states.
vi.mock('./components/user/UserMap', () => ({
  UserMap: (p: { routes: { id: string; duration_min: number }[]; selectedRouteId: string | null; places: { id: string; name: string | null }[]; zones: unknown[]; onSelectPlace: (x: unknown) => void; onSelectRoute: (id: string) => void; onExplain?: (t: object) => void }) => (
    <div data-testid="map" data-selected={p.selectedRouteId ?? ''} data-zones={p.zones.length}>
      {p.routes.map(r => <button key={r.id} onClick={() => p.onSelectRoute(r.id)}>map-route-{r.id}</button>)}
      <button onClick={() => p.onExplain?.({ lat: 17.426, lon: 78.521, segment_id: 'R1', open: true })}>map-incident</button>
      {p.places.map(pl => <button key={pl.id} onClick={() => p.onSelectPlace(pl)}>marker-{pl.name ?? pl.id}</button>)}
    </div>
  ),
}))

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
const CONFIG = { mode: 'simulation', traffic_source: 'FlowSense traffic simulation', simulation: { start: '2026-01-16 13:00:00', step_minutes: 5, steps: 24, default_step: 6 },
  service_area: { south: 17.1, west: 78.1, north: 17.7, east: 78.8 }, center: { lat: 17.4, lon: 78.45 }, providers: { geocoding: 'nominatim', routing: 'osrm', places: 'overpass' },
  categories: [{ id: 'hospital', label: 'Hospitals' }, { id: 'fuel', label: 'Fuel stations' }] }
const TRAFFIC = { mode: 'simulation', step: 6, time: '2026-01-16 13:30:00', updated_at: '2026-01-16 13:30:00', counts: { free_flow: 420, moderate: 2, heavy: 13, severe: 1, no_data: 0 },
  zones: [{ segment_id: 'R1', traffic_level: 'severe', status: 'Severe congestion', speed_kmh: 12, congestion: 0.5, updated_at: 't', path: [[17.4, 78.5], [17.4, 78.52]] }] }
const ALERTS = { time: 't', alerts: [{ type: 'disruption', severity: 'severe', title: 'Traffic disruption detected', message: 'Possible incident. Severe congestion is likely nearby; expect delays.',
  segment_id: 'R1', lat: 17.426, lon: 78.521, confidence: 'high' }] }
const route = (id: string, o: object) => ({ id, default: id === 'r1', distance_km: 12, duration_min: 30, free_flow_min: 25, delay_min: 5, traffic_level: 'severe', traffic_label: 'Severe congestion',
  expected_worsening_min: 0, forecast: { '30': 30 }, simulation_coverage_pct: 90, stretches: [], worst: null, coords: [[17.4, 78.4], [17.41, 78.5]], ranking: 1, recommended: false, label: 'Alternative', ...o })
const ROUTES = { mode: 'simulation', step: 6, time: 't', routing_provider: 'osrm', traffic_note: 'Traffic is simulated by FlowSense and only approximately matched to these roads.',
  routes: [route('r2', { ranking: 1, recommended: true, label: 'Recommended route', duration_min: 24, traffic_level: 'free_flow', traffic_label: 'Free flow', delay_min: 0, distance_km: 13.2 }),
    route('r1', { ranking: 2, duration_min: 30 })],
  diversion: { recommended: true, current_route_id: 'r1', alternative_route_id: 'r2', current_eta_min: 30, alternative_eta_min: 24, estimated_saving_min: 6, alternative_traffic_level: 'free_flow', confidence: 'Medium', reason: 'severe' },
  alerts: [{ type: 'congestion', severity: 'severe', title: 'Severe congestion ahead', message: 'Traffic ahead on the current route is severe.', lat: 17.42, lon: 78.51, segment_id: null, confidence: 'medium', route_id: 'r1' }] }
const GEO = { results: [{ id: 'n1', label: 'Uppal', sublabel: 'Hyderabad, Telangana', lat: 17.4, lon: 78.56, kind: 'suburb' }] }
const GEO2 = { results: [{ id: 'n2', label: 'Ameerpet', sublabel: 'Hyderabad', lat: 17.43, lon: 78.44, kind: 'suburb' }] }
const PLACES = { radius_m: 1200, places: [{ id: 'n9', category: 'hospital', category_label: 'Hospital', name: 'City Hospital', lat: 17.43, lon: 78.5, address: '5 MG Road', distance_m: 340,
  opening_hours: '24/7', phone: '+91 40 1234', website: 'javascript:alert(1)' }] }

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>
function backend(over: Record<string, Handler> = {}) {
  const calls: { url: string; body?: unknown }[] = []
  const table: Record<string, Handler> = {
    '/api/user/config': () => json(CONFIG), '/api/user/traffic': () => json(TRAFFIC), '/api/user/alerts': () => json(ALERTS),
    '/api/user/geocode': url => json(url.includes('Ameerpet') ? GEO2 : GEO), '/api/user/route-options': () => json(ROUTES), '/api/user/nearby': () => json(PLACES), ...over,
  }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(table).find(k => url.startsWith(k))
    return key ? table[key](url, init) : json({}, 404)
  }))
  return calls
}

function mount(path = '/user') {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><Routes>
        <Route path="/login" element={<Login />} /><Route path="/user/*" element={<UserPortal />} /><Route path="/dashboard" element={<div>OPERATOR DASHBOARD</div>} />
      </Routes></AuthProvider></MemoryRouter>
    </QueryClientProvider>)
}
const type = (label: RegExp, text: string) => fireEvent.change(screen.getByRole('combobox', { name: label }), { target: { value: text } })
async function pickSuggestion(label: RegExp, text: string, result: string) {
  type(label, text)
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(result) }, { timeout: 3000 }))
}

beforeEach(() => { Object.defineProperty(navigator, 'geolocation', { value: undefined, configurable: true }) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('entry point', () => {
  it('the operator login has a smaller USER LOGIN button below Sign In that opens the rider portal', async () => {
    backend({ '/api/auth/me': () => json({ detail: 'x' }, 401) })
    mount('/login')
    const signIn = await screen.findByRole('button', { name: 'Sign In' })
    const link = screen.getByRole('link', { name: /user login/i })
    expect(signIn.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()      // below Sign In
    expect(link).toHaveAttribute('href', '/user')
    fireEvent.click(link)
    expect(await screen.findByRole('combobox', { name: /search destination/i })).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/sign up|register|google|facebook/i)
  })

  it('the rider portal is public and shows no operator navigation or controls', async () => {
    backend()
    mount()
    await screen.findByRole('combobox', { name: /search destination/i })
    for (const operatorOnly of ['Incidents', 'Forecast', 'Propagation', 'Interventions', 'Simulation', 'Evaluation', 'Anomaly', 'Simulate']) {
      expect(screen.queryByRole('link', { name: operatorOnly })).toBeNull()
    }
    expect(document.body.textContent).not.toMatch(/anomaly score|model|P50|residual/i)
  })
})

describe('default state', () => {
  it('asks "Where are you going?", labels traffic as simulated, and lists area alerts in rider language', async () => {
    backend()
    mount()
    expect(await screen.findByPlaceholderText('Where are you going?')).toBeInTheDocument()
    expect(await screen.findByText('Traffic disruption detected')).toBeInTheDocument()
    expect(screen.getByText(/Possible incident/)).toBeInTheDocument()
    expect(screen.getByText('Simulated traffic')).toBeInTheDocument()
    expect(await screen.findByText('Fri 16 Jan · 13:30')).toBeInTheDocument()
    expect(screen.getByTestId('map').getAttribute('data-zones')).toBe('1')
    expect(document.body.textContent).not.toMatch(/undefined|NaN|null/)
  })

  it('still works when the traffic service fails: search stays available and the clock says unavailable', async () => {
    backend({ '/api/user/traffic': () => json({ detail: 'boom' }, 500), '/api/user/alerts': () => json({ detail: 'boom' }, 500) })
    mount()
    expect(await screen.findByPlaceholderText('Where are you going?')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('unavailable')).toBeInTheDocument(), { timeout: 5000 })
  })
})

describe('search', () => {
  it('debounces: no request per keystroke, one request for the settled query, with suggestions', async () => {
    const calls = backend()
    mount()
    const box = await screen.findByRole('combobox', { name: /search destination/i })
    fireEvent.focus(box)
    for (const t of ['Up', 'Upp', 'Uppal']) fireEvent.change(box, { target: { value: t } })
    expect(calls.filter(c => c.url.startsWith('/api/user/geocode')).length).toBe(0)
    expect(await screen.findByRole('option', { name: /Uppal/ }, { timeout: 3000 })).toBeInTheDocument()
    expect(calls.filter(c => c.url.startsWith('/api/user/geocode')).length).toBe(1)
    expect(calls.find(c => c.url.startsWith('/api/user/geocode'))!.url).toContain('q=Uppal')
  })

  it('shows Searching…, an empty-result message and a readable error', async () => {
    backend({ '/api/user/geocode': () => json({ results: [] }) })
    mount()
    type(/search destination/i, 'zzzzq')
    expect(await screen.findByText(/No places found for “zzzzq”/, {}, { timeout: 3000 })).toBeInTheDocument()
    vi.unstubAllGlobals()
    backend({ '/api/user/geocode': () => json({ detail: 'Search is busy right now. Please try again in a moment.' }, 503) })
    type(/search destination/i, 'uppal x')
    expect(await screen.findByText('Search is busy right now. Please try again in a moment.', {}, { timeout: 3000 })).toBeInTheDocument()
  })

  it('supports keyboard selection (arrow down + Enter)', async () => {
    backend()
    mount()
    const box = await screen.findByRole('combobox', { name: /search destination/i })
    type(/search destination/i, 'Uppal')
    await screen.findByRole('option', { name: /Uppal/ }, { timeout: 3000 })
    fireEvent.keyDown(box, { key: 'ArrowDown' })
    fireEvent.keyDown(box, { key: 'Enter' })
    expect(await screen.findByRole('combobox', { name: /destination/i })).toHaveValue('Uppal')
  })
})

describe('routes, ranking and diversion', () => {
  async function planTrip() {
    await pickSuggestion(/search destination/i, 'Uppal', 'Uppal')
    await pickSuggestion(/starting point/i, 'Ameerpet', 'Ameerpet')
  }

  it('requests routes for the chosen places, shows ranked cards with distance, ETA, traffic level and delay', async () => {
    const calls = backend()
    mount()
    await planTrip()
    const list = await screen.findByRole('list', { name: 'Route options' }, { timeout: 3000 })
    const cards = within(list).getAllByRole('button')
    expect(cards).toHaveLength(2)
    expect(cards[0]).toHaveTextContent('Recommended route'); expect(cards[0]).toHaveTextContent('24 min'); expect(cards[0]).toHaveTextContent('13.2 km')
    expect(cards[0]).toHaveTextContent('Free flow'); expect(cards[0]).toHaveTextContent('No delay'); expect(cards[0]).toHaveTextContent('1 of 2')
    expect(cards[1]).toHaveTextContent('30 min'); expect(cards[1]).toHaveTextContent('Severe congestion'); expect(cards[1]).toHaveTextContent('+5 min delay')
    const req = calls.find(c => c.url.startsWith('/api/user/route-options'))!.body as { origin: object; destination: object; step: number }
    expect(req.step).toBe(6); expect(req.origin).toEqual({ lat: 17.43, lon: 78.44 }); expect(req.destination).toEqual({ lat: 17.4, lon: 78.56 })
    expect(screen.getByTestId('map').getAttribute('data-selected')).toBe('r2')                    // recommended route preselected
    expect(screen.getByText(/only approximately matched/)).toBeInTheDocument()
  })

  it('recommends a diversion with an ESTIMATED saving, never a guaranteed one, and switches routes on request', async () => {
    backend()
    mount()
    await planTrip()
    const div = await screen.findByRole('region', { name: 'Diversion recommended' }, { timeout: 3000 })
    expect(div).toHaveTextContent('Traffic ahead is severe.')
    expect(div).toHaveTextContent('Current route30 min'); expect(div).toHaveTextContent('24 min'); expect(div).toHaveTextContent('Estimated saving'); expect(div).toHaveTextContent('about 6 min')
    expect(div).toHaveTextContent('Confidence'); expect(div).toHaveTextContent(/not guaranteed/i)
    expect(screen.getByRole('list', { name: 'Traffic alerts' })).toHaveTextContent('Severe congestion ahead')
    fireEvent.click(screen.getByRole('button', { name: 'Keep current route' }))
    expect(screen.queryByRole('region', { name: 'Diversion recommended' })).toBeNull()             // dismissed
    expect(screen.getByTestId('map').getAttribute('data-selected')).toBe('r1')                      // and the current route is now selected
    fireEvent.click(screen.getByRole('button', { name: 'map-route-r2' }))                          // clicking the alternative on the map selects it
    expect(screen.getByTestId('map').getAttribute('data-selected')).toBe('r2')
  })

  it('shows a skeleton while routing, then a readable error with a retry when routing fails', async () => {
    let fail = true
    backend({ '/api/user/route-options': async () => { await new Promise(r => setTimeout(r, 30)); return fail ? json({ detail: 'Routing is temporarily unavailable. Please try again shortly.' }, 503) : json(ROUTES) } })
    mount()
    await planTrip()
    expect(await screen.findByRole('status', { name: 'Calculating routes' }, { timeout: 3000 })).toBeInTheDocument()
    expect(await screen.findByText('Routing is temporarily unavailable. Please try again shortly.')).toBeInTheDocument()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('list', { name: 'Route options' })).toBeInTheDocument()
  })

  it('reports an invalid destination in plain language and never leaks raw errors', async () => {
    backend({ '/api/user/route-options': () => json({ detail: [{ type: 'missing', loc: ['body', 'origin'], msg: 'Field required' }] }, 422) })
    mount()
    await planTrip()
    expect(await screen.findByText('Please check the places you entered and try again.', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/Field required|undefined|Traceback/)
  })

  it('says so when there is only one route instead of inventing alternatives', async () => {
    backend({ '/api/user/route-options': () => json({ ...ROUTES, routes: [ROUTES.routes[1]], diversion: null, alerts: [] }) })
    mount()
    await planTrip()
    expect(await screen.findByText(/No alternative route was available/, {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Diversion recommended' })).toBeNull()
  })
})

describe('nearby places', () => {
  it('loads a category, shows markers, place details without invented fields, and directions set the destination', async () => {
    const calls = backend()
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Hospitals' }))
    fireEvent.click(await screen.findByRole('button', { name: 'marker-City Hospital' }, { timeout: 3000 }))
    const card = await screen.findByRole('region', { name: 'Place details' })
    expect(card).toHaveTextContent('City Hospital'); expect(card).toHaveTextContent('340 m away'); expect(card).toHaveTextContent('5 MG Road'); expect(card).toHaveTextContent('24/7')
    expect(within(card).queryByRole('link', { name: 'Website' })).toBeNull()                       // a javascript: URL is never rendered as a link
    expect(within(card).getByRole('link', { name: '+91 40 1234' })).toHaveAttribute('href', 'tel:+91401234')
    expect(calls.find(c => c.url.startsWith('/api/user/nearby'))!.body).toMatchObject({ categories: ['hospital'], radius_m: 1200 })
    fireEvent.click(within(card).getByRole('button', { name: /get directions/i }))
    expect(await screen.findByRole('combobox', { name: /destination/i })).toHaveValue('City Hospital')
  })

  it('shows an empty message and a friendly error with retry', async () => {
    backend({ '/api/user/nearby': () => json({ places: [], radius_m: 1200 }) })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Hospitals' }))
    expect(await screen.findByText(/No places found within 1.2 km/, {}, { timeout: 3000 })).toBeInTheDocument()
    vi.unstubAllGlobals()
    backend({ '/api/user/nearby': () => json({ detail: 'Nearby places are unavailable right now.' }, 503) })
    fireEvent.click(screen.getByRole('button', { name: 'Fuel' }))
    expect(await screen.findByText('Nearby places are unavailable right now.', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})

describe('location', () => {
  it('handles a denied permission gracefully: explains it and offers manual search, without asking again on load', async () => {
    const getCurrentPosition = vi.fn((_ok: unknown, err: (e: { code: number }) => void) => err({ code: 1 }))
    Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition }, configurable: true })
    backend()
    mount()
    await screen.findByPlaceholderText('Where are you going?')
    expect(getCurrentPosition).not.toHaveBeenCalled()                                              // never prompts on load
    fireEvent.click(screen.getByRole('button', { name: 'Show my location' }))
    expect(await screen.findByText(/Location access is unavailable/)).toBeInTheDocument()
    expect(getCurrentPosition).toHaveBeenCalledTimes(1)
  })

  it('uses the device location as the starting point only after the user asks for it', async () => {
    const getCurrentPosition = vi.fn((ok: (p: unknown) => void) => ok({ coords: { latitude: 17.43, longitude: 78.44, accuracy: 30 } }))
    Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition }, configurable: true })
    const calls = backend()
    mount()
    await pickSuggestion(/search destination/i, 'Uppal', 'Uppal')
    type(/starting point/i, '')
    fireEvent.focus(screen.getByRole('combobox', { name: /starting point/i }))
    fireEvent.click(await screen.findByRole('option', { name: /Your location/ }))
    await screen.findByRole('list', { name: 'Route options' }, { timeout: 3000 })
    expect((calls.find(c => c.url.startsWith('/api/user/route-options'))!.body as { origin: object }).origin).toEqual({ lat: 17.43, lon: 78.44 })
  })

  it('does not treat a position outside the service area as a starting point', async () => {
    Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition: (ok: (p: unknown) => void) => ok({ coords: { latitude: 51.5, longitude: -0.12, accuracy: 20 } }) }, configurable: true })
    backend()
    mount()
    await screen.findByText('Fri 16 Jan · 13:30')                                                    // settings (service area) are loaded
    fireEvent.click(screen.getByRole('button', { name: 'Show my location' }))
    expect(await screen.findByText(/outside the Hyderabad area/)).toBeInTheDocument()
  })
})

describe('simulated replay', () => {
  it('advances the simulation clock on a timer and reloads traffic for the new step (no invented movement)', async () => {
    const calls = backend()
    mount()
    await screen.findByText('Fri 16 Jan · 13:30')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fireEvent.click(screen.getByRole('button', { name: 'Replay simulated traffic' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(6100) })
    await waitFor(() => expect(calls.some(c => c.url === '/api/user/traffic?step=7')).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: 'Pause replay' }))
  })
})

const EXPLAIN = { status: 'disruption_detected', headline: 'Traffic disruption ahead', summary: 'Traffic conditions changed sharply here. This is an inference, not a confirmed report.',
  traffic_level: 'severe', updated_at: '2026-01-16 13:30:00', segment_id: 'R1',
  cause: { label: 'Traffic disruption', certainty: 'likely', confidence: 0.87, confidence_label: 'High', type: 'Undetermined', type_note: null },
  evidence: [{ signal: 'speed_drop', message: 'Speed is 58% below normal for this time of day' }, { signal: 'sudden_onset', message: 'Traffic slowed sharply within one 5-minute interval' },
    { signal: 'congestion_change', message: 'Congestion is well above what is usual here' }, { signal: 'roadwork', message: 'No active roadwork on record' }],
  forecast: { now: 'severe', now_label: 'Severe congestion', trend: 'worsening', why: 'Congestion is building and nearby roads are also slowing.', spread: 'Congestion may spread to nearby roads.',
    horizons: { '15m': { level: 'heavy', label: 'Heavy traffic', confidence: 'Medium' }, '30m': { level: 'severe', label: 'Severe congestion', confidence: 'Medium' }, '60m': { level: 'severe', label: 'Severe congestion', confidence: 'Low' } } } }
const withWorst = { ...ROUTES, routes: ROUTES.routes.map(r => (r.id === 'r1' ? { ...r, worst: { lat: 17.42, lon: 78.51, level: 'severe' } } : r)) }

describe('traffic explanation ("Why is traffic heavy?")', () => {
  async function tripOnHeavyRoute(over: Record<string, Handler> = {}) {
    const calls = backend({ '/api/user/route-options': () => json(withWorst), '/api/user/explain': () => json(EXPLAIN), ...over })
    mount()
    await pickSuggestion(/search destination/i, 'Uppal', 'Uppal')
    await pickSuggestion(/starting point/i, 'Ameerpet', 'Ameerpet')
    await screen.findByRole('list', { name: 'Route options' }, { timeout: 3000 })
    fireEvent.click(screen.getByRole('button', { name: 'map-route-r1' }))            // the congested route
    return calls
  }

  it('shows a compact cause card with certainty and confidence, and opens details with evidence and forecast', async () => {
    await tripOnHeavyRoute()
    const card = await screen.findByRole('region', { name: 'Traffic explanation' })
    expect(card).toHaveTextContent('Traffic disruption ahead'); expect(card).toHaveTextContent('Possible cause'); expect(card).toHaveTextContent('Traffic disruption')
    expect(card).toHaveTextContent('Likely'); expect(card).toHaveTextContent('High (87%)'); expect(card).toHaveTextContent('Speed is 58% below normal')
    expect(card).not.toHaveTextContent('Traffic forecast')
    fireEvent.click(within(card).getByRole('button', { name: 'Why is traffic heavy?' }))
    expect(card).toHaveTextContent('Traffic forecast (estimated)'); expect(card).toHaveTextContent('+30 min'); expect(card).toHaveTextContent('nearby roads are also slowing')
    expect(card).toHaveTextContent('Forecast confidence: Low'); expect(card).toHaveTextContent('No active roadwork on record'); expect(card).toHaveTextContent('not live city traffic')
    expect(card.textContent).not.toMatch(/confirmed accident|anomaly|undefined|NaN|null/i)
  })

  it('offers the alternative route, labelled as an estimate', async () => {
    await tripOnHeavyRoute()
    const card = await screen.findByRole('region', { name: 'Traffic explanation' })
    fireEvent.click(within(card).getByRole('button', { name: /View alternative route \(estimated 6 min faster\)/ }))
    expect(screen.getByTestId('map').getAttribute('data-selected')).toBe('r2')
  })

  it('opens from a tapped map incident and asks about that exact segment', async () => {
    const calls = backend({ '/api/user/explain': () => json(EXPLAIN) })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'map-incident' }))
    const card = await screen.findByRole('region', { name: 'Traffic explanation' })
    expect(card).toHaveTextContent('Traffic forecast (estimated)')                     // opened straight to the details
    expect((calls.find(c => c.url === '/api/user/explain')!.body as { segment_id: string }).segment_id).toBe('R1')
  })

  it('says the cause could not be determined when the explanation service fails, and the rest keeps working', async () => {
    await tripOnHeavyRoute({ '/api/user/explain': () => json({ detail: 'x' }, 500) })
    expect(await screen.findByText('Traffic disruption detected, but the cause could not be determined.')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Route options' })).toBeInTheDocument()
  })

  it('does not invent a reason: an unclear cause is shown as unclear with no confidence figure', async () => {
    await tripOnHeavyRoute({ '/api/user/explain': () => json({ ...EXPLAIN, status: 'congestion', headline: 'Heavy traffic ahead', summary: 'Traffic is abnormal, but FlowSense cannot confidently identify the cause.',
      cause: { label: 'Cause unclear', certainty: 'unknown', confidence: null, confidence_label: null, type: null, type_note: null }, evidence: [], forecast: null }) })
    const card = await screen.findByRole('region', { name: 'Traffic explanation' })
    expect(card).toHaveTextContent('Cause unclear'); expect(card).toHaveTextContent('Unknown'); expect(card).not.toHaveTextContent('Confidence')
  })
})

describe('logout', () => {
  it('ends the session on the server and returns to the main login, which still offers Sign In and User login', async () => {
    const calls = backend({ '/api/auth/logout': () => json({ authenticated: false }), '/api/auth/me': () => json({ detail: 'x' }, 401) })
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Log out' }))
    expect(await screen.findByRole('button', { name: 'Sign In' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /user login/i })).toBeInTheDocument()
    expect(calls.some(c => c.url === '/api/auth/logout')).toBe(true)
  })
})

describe('helpers', () => {
  it('format values for people and never print NaN / undefined', () => {
    expect(minutes(24.4)).toBe('24 min'); expect(minutes(75)).toBe('1 h 15 min'); expect(minutes(NaN)).toBe('—'); expect(minutes(undefined)).toBe('—')
    expect(meters(340)).toBe('340 m'); expect(meters(1250)).toBe('1.3 km')
    expect(simClock('2026-01-16 13:30:00')).toBe('Fri 16 Jan · 13:30'); expect(simClock('nonsense')).toBe('—'); expect(simClock(undefined)).toBe('—')
    expect(delayText({ delay_min: 0, traffic_level: 'free_flow' } as never)).toBe('No delay'); expect(delayText({ delay_min: 7, traffic_level: 'severe' } as never)).toBe('+7 min delay')
    expect(delayText({ delay_min: 0, traffic_level: 'heavy' } as never)).toBe('Less than 1 min delay')            // never "Heavy traffic · No delay"
  })
  it('traffic colours follow the required scheme', () => {
    expect(LEVEL_COLOR).toMatchObject({ free_flow: '#2f8f5b', moderate: '#d4a21a', heavy: '#e0661b', severe: '#c62828', no_data: '#9ca3af' })
  })
  it('only http(s) links and in-area points are accepted', () => {
    expect(safeUrl('https://example.org')).toBe('https://example.org'); expect(safeUrl('javascript:alert(1)')).toBeNull(); expect(safeUrl(null)).toBeNull()
    expect(inArea({ lat: 17.4, lon: 78.5 }, CONFIG.service_area)).toBe(true); expect(inArea({ lat: 51, lon: 0 }, CONFIG.service_area)).toBe(false)
  })
  it('API failures become human messages', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    await expect(userApi.config()).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Can't reach FlowSense") })
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: [{ msg: 'x' }] }, 422)))
    const e = await userApi.traffic(1).catch(x => x)
    expect(e).toBeInstanceOf(UserApiError); expect(e.message).toBe('Please check the places you entered and try again.')
  })
})
