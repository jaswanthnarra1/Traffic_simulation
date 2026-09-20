import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import Flyover from './pages/Flyover'

vi.mock('./components/FlyoverMap', () => ({
  BN_COLOR: { LOW: '#2f8f5b', MODERATE: '#d4a21a', HIGH: '#e0661b', CRITICAL: '#c62828', NO_DATA: '#9ca3af' },
  FlyoverMap: (p: { result: { segments: { segment_id: string }[] } | null; candidate: { name: string } | null; onSelectSeg: (id: string) => void; onCandidateClick?: () => void }) => (
    <div data-testid="map" data-candidate={p.candidate?.name ?? ''}><button onClick={() => p.onCandidateClick?.()}>map-flyover</button>{p.result?.segments.map(s => <button key={s.segment_id} onClick={() => p.onSelectSeg(s.segment_id)}>seg-{s.segment_id}</button>)}</div>
  ),
}))

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
const seg = (i: number, over: object = {}) => ({ index: i, segment_id: `S0${i + 1}`, length_m: 300, start_m: i * 300, end_m: i * 300 + 300, coords: [[17.4, 78.4], [17.41, 78.41]], road_name: 'SP Road',
  junction_count: 3, grid_segment_id: 'R1', match_distance_m: 120, data_status: 'MATCHED', unavailable: { pedestrian_activity: 'data_required' }, traffic_volume: 2500, average_speed: 21.4,
  congestion_index: 61, delay_min: 1.2, accident_count: 2, bottleneck_score: 72, bottleneck_level: 'HIGH', lane_count: 3, ...over })
const CAND = { name: 'Zone A', start: [17.401, 78.401], end: [17.409, 78.409], start_m: 250, end_m: 1100, estimated_length_m: 850, affected_segments: ['S01', 'S02'], bottleneck_segments: ['S01'],
  reason_for_selection: 'Segments S01-S02 hold 1 HIGH bottleneck.', coords: [[17.4, 78.4]], existing_grade_separation: ['Begumpet Railway Flyover'],
  suitability: { score: 84, breakdown: { traffic_pressure: 91, junction_bottleneck: 88, delay: 86, capacity_utilization: 89, historical_growth: null, accident_risk: 61 },
    excluded: { feasibility: 'data_required (no land-use, right-of-way, utilities or geotechnical data)', pedestrian: 'data_required', public_transport: 'data_required' }, screening_level: 'strong' },
  explanation: [{ factor: 'capacity', text: 'Peak-hour volume averages about 2,550 veh/h against roughly 3,105 veh/h of capacity (82% utilisation).' }],
  alternatives: { options: [{ name: 'Signal optimization', supported_by_data: true, basis: '1 signalised segment.', note: null }, { name: 'Bus priority', supported_by_data: null, basis: 'Cannot be assessed: no public transport data.', note: 'data_required' }],
    dataset_planning_candidates: [] } }
const RESULT = (over: object = {}) => ({ id: 'abc', mode: 'simulation', data_notice: 'SIMULATION DATA: traffic is the organizer dataset.', provider: { traffic: 'dataset', live: false, as_of: 't' },
  corridor: { origin: { lat: 17.4, lng: 78.4 }, destination: { lat: 17.41, lng: 78.41 }, distance_km: 2.1, free_flow_time_min: 6, estimated_travel_time_min: 8, segments: 2, junctions: [], coords: [[17.4, 78.4]],
    congestion_index: 55, traffic_status: 'HIGH', coverage_pct: 100, alternative_routes: 0 },
  segments: [seg(0), seg(1, { data_status: 'NO_DATA', bottleneck_level: 'NO_DATA', bottleneck_score: null, congestion_index: null, road_name: null })],
  bottlenecks: { counts: { LOW: 0, MODERATE: 0, HIGH: 1, CRITICAL: 0, NO_DATA: 1 }, segments: ['S01'] }, candidates: [CAND], recommended_analysis: CAND,
  confidence: { score: 72, components: { coverage: 100, history: 86, freshness: 30, model_validation: 100, field_completeness: 62 }, meaning: 'Data-quality indicator. It is not a probability and not engineering certainty.', missing_fields: ['pedestrian_activity'] },
  stages: [{ stage: 'Generating route', ms: 120 }, { stage: 'Segmenting corridor', ms: 3 }],
  disclaimer: { layers: ['AI/Data analysis (this screen)', 'Engineering feasibility (not assessed)', 'Official infrastructure decision (not made)'], text: 'This is an AI/data-driven planning aid. It does not constitute engineering feasibility or approval.' }, ...over })

const IV = (id: string, label: string, overall: number, over: object = {}) => ({ id, label, applicable: true, reason_not_applicable: null, length_km: 2.1, estimated_cost_cr: 320,
  scores: { traffic_impact: 90, land_feasibility: 78, construction_feasibility: 74, cost_efficiency: 20, long_term_impact: 88, overall },
  simulated_impact: { before: { speed_kmh: 8, congestion_pct: 92, delay_min: 18 }, after: { speed_kmh: 10.6, congestion_pct: 32, delay_min: 6 }, capacity_change_pct: 35, speed_change_pct: 32,
    congestion_change_pct: -65, congestion_change_pts: -60, delay_reduction_min: 12, emissions_change_pct: -28, data_status: 'SIMULATED' }, ...over })
const INFRA = (over: object = {}) => ({ mode: 'SIMULATION', corridor_id: 'abc', corridor: RESULT(), disclaimer: 'This AI-generated infrastructure analysis is intended for planning and simulation purposes only. Actual construction requires detailed studies.',
  labels: { traffic: 'SIMULATION (organizer dataset replay)', land: 'SIMULATED', infrastructure: 'SIMULATED', banner: 'Simulation / Demo Data' },
  traffic: { zone_congestion_pct: 92, zone_delay_min: 18, zone_avg_speed_kmh: 8, zone_volume_vph: 4850, zone_capacity_utilization_pct: 95, zone_incidents: 3, zone_length_km: 2.1, congestion_index: 55, traffic_status: 'HIGH' },
  bottlenecks: { zones: [{ name: 'Zone A', start_segment: 'S01', end_segment: 'S02', bottleneck_segments: ['S01'], length_km: 2.1 }] },
  land_evaluation: { data_status: 'SIMULATED', label: 'SIMULATED LAND EVALUATION (demo estimate, not government land records)', land_availability_pct: 78, government_land_pct: 61, private_property_impact_pct: 22,
    structures_affected: 12, row_availability_pct: 82, utility_conflict: 'Low', environmental_constraint: 'Requires assessment', construction_difficulty: 0.3 },
  interventions: [IV('flyover', 'Flyover / grade separator', 84), IV('signal_optimization', 'Signal optimization', 66, { estimated_cost_cr: 1 }),
    { id: 'traffic_diversion', label: 'Traffic diversion', applicable: false, reason_not_applicable: 'The routing provider found no alternative route.', scores: null, simulated_impact: null, length_km: null, estimated_cost_cr: null }],
  recommendation: { action: 'flyover', headline: 'Construct a candidate flyover', summary: 'Among the evaluated interventions the flyover gives the highest overall suitability.', overall_score: 84,
    why: ['Volume is 95% of capacity.', 'Sufficient simulated land availability (78%).'], data_status: 'SIMULATED', disclaimer: 'x', caution: null,
    simulated_impact: { congestion_change_pct: -65, congestion_change_pts: -60, peak_travel_time_change_min: -12, speed_change_pct: 32, emissions_change_pct: -28 },
    candidate: { start: [17.4442, 78.4908], end: [17.4599, 78.5004], start_label: 'near General Choudhuri Road', end_label: 'near Hyderabad - Mancherial Highway', length_km: 2.1, estimated_cost_cr: 320 } },
  flyover: IV('flyover', 'Flyover / grade separator', 84), candidate: { name: 'Zone A', start: [17.4442, 78.4908], end: [17.4599, 78.5004], estimated_length_m: 2100, affected_segments: ['S01', 'S02'], suitability: { score: 66 } },
  confidence: { overall: 78, components: { traffic: 92, road_network: 96, land: 65, infrastructure: 60 }, simulated: ['land', 'infrastructure'], meaning: 'Data-quality indicator. It is not the probability that a project would succeed or be built.' }, ...over })
const SIMRES = { mode: 'SIMULATION', label: 'SIMULATED IMPACT', intervention: { id: 'flyover', label: 'Flyover / grade separator' }, current_vs_after: IV('flyover', 'x', 1).simulated_impact,
  planning_horizon: { ...IV('flyover', 'x', 1).simulated_impact, demand_growth_pct: 20 }, method: 'BPR', disclaimer: 'Simulated results are not predictions of actual future outcomes.' }

function setup(analyze: () => Response, infra: () => Response = () => json(INFRA()), simulate: () => Response = () => json(SIMRES)) {
  const calls: { url: string; body?: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.startsWith('/api/user/geocode')) return json({ results: [{ id: 'n', label: url.includes('Ame') ? 'Ameerpet' : 'SR Nagar', sublabel: 'Hyderabad', lat: url.includes('Ame') ? 17.4375 : 17.4416, lon: url.includes('Ame') ? 78.4483 : 78.4445, kind: 's' }] })
    if (url.startsWith('/api/corridor/analyze')) return analyze()
    if (url.startsWith('/api/infrastructure/analyze')) return infra()
    if (url.startsWith('/api/infrastructure/simulate')) return simulate()
    return json({}, 404)
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><Flyover /></MemoryRouter></QueryClientProvider>)
  return calls
}
async function choose(label: RegExp, text: string, result: string) {
  fireEvent.change(screen.getByRole('combobox', { name: label }), { target: { value: text } })
  fireEvent.click(await screen.findByRole('option', { name: new RegExp(result) }, { timeout: 3000 }))
}
async function runAnalysis() {
  await choose(/^origin/i, 'Ameerpet', 'Ameerpet')
  await choose(/^destination/i, 'SR Nagar', 'SR Nagar')
  fireEvent.click(screen.getByRole('button', { name: 'Run Corridor Analysis' }))
}
afterEach(() => vi.unstubAllGlobals())

describe('flyover candidate analysis', () => {
  it('needs both points, labels the data as simulation, and sends lat/lng to the analysis API', async () => {
    const calls = setup(() => json(RESULT()))
    expect(screen.getByRole('button', { name: 'Run Corridor Analysis' })).toBeDisabled()
    expect(screen.getAllByText(/simulation data/i)[0]).toBeInTheDocument()
    await runAnalysis()
    await screen.findByText(/Candidate detected/)
    expect(calls.find(c => c.url === '/api/corridor/analyze')!.body).toEqual({ origin: { lat: 17.4375, lng: 78.4483 }, destination: { lat: 17.4416, lng: 78.4445 } })
  })

  it('shows the candidate, suitability breakdown, reasons, alternatives, confidence and the three-layer disclaimer', async () => {
    setup(() => json(RESULT()))
    await runAnalysis()
    await screen.findByText(/Candidate detected/)
    expect(screen.getByText('2.1 km')).toBeInTheDocument()
    expect(screen.getAllByText('84').length).toBeGreaterThan(0)
    expect(screen.getByText(/17\.40100, 78\.40100/)).toBeInTheDocument()
    expect(screen.getByText('Traffic pressure').closest('div')).toHaveTextContent('91')
    expect(screen.getByText('Historical growth').closest('div')).toHaveTextContent('Data unavailable')            // null stays "unavailable", never 0
    expect(screen.getByText(/feasibility/i, { selector: 'b' })).toBeInTheDocument()
    expect(screen.getByText(/Peak-hour volume averages/)).toBeInTheDocument()
    expect(screen.getByText('NEEDS DATA')).toBeInTheDocument()
    expect(screen.getByText(/already passes an existing grade separation.*Begumpet Railway Flyover/)).toBeInTheDocument()
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText(/not a probability and not engineering certainty/)).toBeInTheDocument()
    expect(screen.getByText(/does not constitute engineering feasibility/)).toBeInTheDocument()
    expect(screen.getByText('Engineering feasibility (not assessed)')).toBeInTheDocument()
    expect(screen.getByTestId('map').getAttribute('data-candidate')).toBe('Zone A')
    expect(document.body.textContent).not.toMatch(/undefined|NaN|null/)
  })

  it('opens segment details, marking missing fields as unavailable and no-data stretches as unscored', async () => {
    setup(() => json(RESULT()))
    await runAnalysis()
    fireEvent.click(await screen.findByRole('button', { name: 'seg-S01' }))
    const card = screen.getByRole('region', { name: 'Segment details' })
    expect(card).toHaveTextContent('SP Road'); expect(card).toHaveTextContent('2,500 veh/h'); expect(card).toHaveTextContent('72 / 100')
    expect(card).toHaveTextContent('Pedestrian activity'); expect(card).toHaveTextContent('Data unavailable'); expect(card).toHaveTextContent('Inside Zone A')
    fireEvent.click(screen.getByRole('button', { name: 'seg-S02' }))
    const nd = screen.getByRole('region', { name: 'Segment details' })
    expect(nd).toHaveTextContent('Road name unavailable'); expect(nd).toHaveTextContent('nothing is scored')
  })

  it('says no flyover study is indicated when there is no candidate', async () => {
    setup(() => json(RESULT({ candidates: [], recommended_analysis: null, bottlenecks: { counts: { LOW: 2, MODERATE: 0, HIGH: 0, CRITICAL: 0, NO_DATA: 0 }, segments: [] } })))
    await runAnalysis()
    expect(await screen.findByText(/No candidate zone/)).toBeInTheDocument()
    expect(screen.getByText(/No HIGH or CRITICAL bottleneck/)).toBeInTheDocument()
    expect(screen.queryByText(/Candidate detected/)).toBeNull()
  })

  it('shows a readable error and keeps the form usable when the analysis fails', async () => {
    setup(() => json({ detail: 'No route could be found between these points.' }, 404))
    await runAnalysis()
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(within(screen.getByRole('alert')).queryByText(/stack|Traceback/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Run Corridor Analysis' })).toBeEnabled()
  })
})

describe('AI infrastructure recommendation', () => {
  it('starts with the select-a-corridor state, then Ready to analyze once both points are set', async () => {
    setup(() => json(RESULT()))
    expect(screen.getByText('Select a road corridor to begin infrastructure analysis.')).toBeInTheDocument()
    await choose(/^origin/i, 'Ameerpet', 'Ameerpet')
    await choose(/^destination/i, 'SR Nagar', 'SR Nagar')
    expect(screen.getByText('Ready to analyze.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyze Corridor' })).toBeInTheDocument()
  })

  it('shows the analysing phases, then the recommendation rendered from the API response (nothing hard-coded)', async () => {
    let release!: () => void
    const gate = new Promise<void>(r => { release = r })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.startsWith('/api/user/geocode')) return json({ results: [{ id: 'n', label: url.includes('Ame') ? 'Ameerpet' : 'SR Nagar', sublabel: 'H', lat: url.includes('Ame') ? 17.4375 : 17.4416, lon: 78.44, kind: 's' }] })
      if (url.startsWith('/api/corridor/analyze')) return json(RESULT())
      if (url.startsWith('/api/infrastructure/analyze')) { await gate; return json(INFRA({ recommendation: { ...INFRA().recommendation, headline: 'Headline from the API' } })) }
      return json({}, 404)
    }))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><Flyover /></MemoryRouter></QueryClientProvider>)
    await runAnalysis()
    const progress = await screen.findByRole('status', { name: 'Analysis progress' })
    expect(progress).toHaveTextContent('Evaluating land…'); expect(progress).toHaveTextContent('Comparing interventions…')
    release()
    expect(await screen.findByText('Headline from the API')).toBeInTheDocument()
    expect(screen.getByText('Simulation / Demo Data', { selector: 'span' })).toBeInTheDocument()
  })

  it('compares interventions, labels land/infrastructure as simulated and explains inapplicable options', async () => {
    setup(() => json(RESULT()))
    await runAnalysis()
    const table = await screen.findByRole('table', { name: 'Intervention comparison' })
    expect(within(table).getByText('Flyover / grade separator').closest('tr')).toHaveTextContent('84')
    expect(within(table).getByText('RECOMMENDED')).toBeInTheDocument()
    expect(within(table).getByText(/Not applicable: The routing provider found no alternative route/)).toBeInTheDocument()
    expect(screen.getByText('Construct a candidate flyover')).toBeInTheDocument()
    expect(screen.getByText(/SIMULATED LAND EVALUATION \(demo estimate/)).toBeInTheDocument()
    expect(screen.getByText('78%', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText(/Sufficient simulated land availability/)).toBeInTheDocument()
    expect(screen.getByText('65% — SIMULATED')).toBeInTheDocument()
    expect(screen.getByText(/not the probability that a project would succeed/)).toBeInTheDocument()
    expect(screen.getByText(/planning and simulation purposes only/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/undefined|NaN|null/)
  })

  it('simulates an intervention: running state, then current vs simulated after', async () => {
    setup(() => json(RESULT()))
    await runAnalysis()
    fireEvent.click(await screen.findByRole('button', { name: 'Simulate Intervention' }))
    const cmp = await screen.findByLabelText('Current versus simulated')
    expect(cmp).toHaveTextContent('8 km/h'); expect(cmp).toHaveTextContent('10.6 km/h'); expect(cmp).toHaveTextContent('92%'); expect(cmp).toHaveTextContent('32%')
    expect(cmp).toHaveTextContent('SIMULATED IMPACT'); expect(cmp).toHaveTextContent('not predictions of actual future outcomes'); expect(cmp).toHaveTextContent('demand grown by 20%')
  })

  it('reports a simulation failure in plain language', async () => {
    setup(() => json(RESULT()), undefined, () => json({ detail: 'x' }, 500))
    await runAnalysis()
    fireEvent.click(await screen.findByRole('button', { name: 'Simulate Intervention' }))
    expect(await screen.findByText(/simulation could not be completed/i)).toBeInTheDocument()
  })

  it('keeps the corridor results when the infrastructure service fails, with a retry', async () => {
    setup(() => json(RESULT()), () => json({ detail: 'Insufficient traffic observations are available for this corridor.' }, 422))
    await runAnalysis()
    expect(await screen.findByText(/Insufficient traffic observations/)).toBeInTheDocument()
    expect(screen.getByText(/Candidate detected/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows the flyover detail card when the proposed overlay is clicked, from the response only', async () => {
    setup(() => json(RESULT()))
    await runAnalysis()
    await screen.findByRole('table', { name: 'Intervention comparison' })
    fireEvent.click(screen.getByRole('button', { name: 'map-flyover' }))
    const card = screen.getByRole('region', { name: 'Flyover candidate details' })
    for (const t of ['17.44420, 78.49080', '~2.10 km', '4,850 veh/h', '92%', '18 min', '78% (simulated)', '12 (simulated)', '₹320 Cr (demo estimate)', '66 data-driven', '78% (land and infrastructure simulated)']) expect(card).toHaveTextContent(t)
  })

  it('says no intervention is indicated when nothing is recommended', async () => {
    setup(() => json(RESULT()), () => json(INFRA({ interventions: [], land_evaluation: null, flyover: null, recommendation: { action: 'none', headline: 'No bottleneck zone found', summary: 'No intervention is indicated by this data.', why: [], data_status: 'SIMULATED', disclaimer: 'x' } })))
    await runAnalysis()
    expect(await screen.findByText('No bottleneck zone found')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Simulate Intervention' })).toBeNull()
  })
})
