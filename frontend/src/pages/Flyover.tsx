import { useMutation, useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowDown, Crosshair, Loader2, Play } from 'lucide-react'
import { Fragment, useState } from 'react'
import { BN_COLOR, FlyoverMap } from '../components/FlyoverMap'
import { InfrastructurePanel, type InfraStatus } from '../components/InfrastructurePanel'
import { PlaceSearch } from '../components/user/PlaceSearch'
import { ErrorState, Panel } from '../components/ui'
import { api } from '../services/api'
import type { Bn, Candidate, Corridor, CorridorSegment } from '../types/corridor'
import type { Infrastructure } from '../types/infrastructure'
import type { Pt } from '../types/user'

const LABEL: Record<string, string> = {
  traffic_pressure: 'Traffic pressure', junction_bottleneck: 'Junction bottleneck', delay: 'Delay', capacity_utilization: 'Capacity utilisation',
  historical_growth: 'Historical growth', accident_risk: 'Accident risk (records)',
}
const COMP: Record<string, string> = { coverage: 'Geographic coverage', history: 'Historical data volume', freshness: 'Freshness', model_validation: 'Model validation', field_completeness: 'Field completeness' }
const na = (v: unknown, unit = '') => (v === null || v === undefined ? 'Data unavailable' : `${typeof v === 'number' ? v.toLocaleString() : v}${unit}`)
const stat = (s: CorridorSegment, k: keyof CorridorSegment, unit = '') => na(s[k], unit)

export default function Flyover() {
  const [origin, setOrigin] = useState<Pt | null>(null)
  const [dest, setDest] = useState<Pt | null>(null)
  const [picking, setPicking] = useState<'origin' | 'destination' | null>(null)
  const [segId, setSegId] = useState<string | null>(null)
  const [candIdx, setCandIdx] = useState(0)
  const run = useMutation({ mutationFn: () => api.analyzeCorridor({ lat: origin!.lat, lng: origin!.lon }, { lat: dest!.lat, lng: dest!.lon }), onSuccess: () => { setCandIdx(0); setSegId(null) } })
  const [showFly, setShowFly] = useState(false)
  const infra = useMutation({ mutationFn: () => api.analyzeInfrastructure({ lat: origin!.lat, lng: origin!.lon }, { lat: dest!.lat, lng: dest!.lon }) })
  const res = run.data
  const infraStatus: InfraStatus = run.isPending ? 'corridor' : infra.isPending ? 'infrastructure' : infra.isError ? 'error' : infra.data ? 'done' : res ? 'infrastructure' : 'idle'
  const analyze = () => { setShowFly(false); infra.reset(); run.mutate(undefined, { onSuccess: () => infra.mutate() }) }
  const cand = res?.candidates[candIdx] ?? null
  const seg = res?.segments.find(s => s.segment_id === segId) ?? null
  const canRun = Boolean(origin && dest) && !run.isPending

  const pick = (lat: number, lon: number) => {
    const p: Pt = { label: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, lat, lon, source: 'pin' }
    if (picking === 'origin') setOrigin(p); else setDest(p)
    setPicking(null)
  }

  return (
    <div className="h-full relative overflow-hidden bg-[#e9edf1]" data-testid="flyover-page">
      <div className="absolute inset-0">
        <FlyoverMap result={res ?? null} selectedSeg={segId} onSelectSeg={setSegId} candidate={cand} origin={origin} dest={dest} picking={Boolean(picking)} onPick={pick} onCandidateClick={() => setShowFly(true)} />
      </div>

      <aside className="absolute z-[1000] left-3 top-3 bottom-3 w-[400px] max-w-[calc(100%-1.5rem)] max-md:top-auto max-md:bottom-2 max-md:right-3 max-md:w-auto max-md:max-w-none max-md:max-h-[58%] flex flex-col gap-2.5 pointer-events-none" aria-label="Flyover candidate analysis">
        <Panel title="Infrastructure & flyover analysis" right={<span className="text-[10px] font-semibold tracking-wider uppercase text-[#8a4a06] bg-[#fff1dc] border border-[#ecc99a] rounded-sm px-1.5 h-5 inline-flex items-center">Simulation data</span>} className="pointer-events-auto" bodyClass="p-3 space-y-2">
          <PlaceSearch label="Origin" placeholder="Origin (search or pick on map)" value={origin?.label ?? ''} onSelect={setOrigin} onClear={() => setOrigin(null)}
            leading={<span className="size-3.5 rounded-full border-[3px] border-accent bg-white shrink-0" aria-hidden />}
            trailing={<button type="button" onClick={() => setPicking(p => (p === 'origin' ? null : 'origin'))} aria-label="Pick origin on map" aria-pressed={picking === 'origin'} className={picking === 'origin' ? 'text-accent' : 'text-ink-3'}><Crosshair className="size-4" aria-hidden /></button>} />
          <PlaceSearch label="Destination" placeholder="Destination (search or pick on map)" value={dest?.label ?? ''} onSelect={setDest} onClear={() => setDest(null)}
            leading={<span className="size-3.5 rounded-full bg-ink shrink-0" aria-hidden />}
            trailing={<button type="button" onClick={() => setPicking(p => (p === 'destination' ? null : 'destination'))} aria-label="Pick destination on map" aria-pressed={picking === 'destination'} className={picking === 'destination' ? 'text-accent' : 'text-ink-3'}><Crosshair className="size-4" aria-hidden /></button>} />
          {picking && <p role="status" className="text-[12px] text-ink-2">Click the map to set the {picking}. <button type="button" className="underline" onClick={() => setPicking(null)}>Cancel</button></p>}
          <button type="button" disabled={!canRun} onClick={analyze}
            className="w-full h-9 rounded-md bg-ink text-paper text-[13px] font-semibold disabled:opacity-40 inline-flex items-center justify-center gap-2">
            {run.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
            {run.isPending ? 'Analysing corridor…' : 'Run Corridor Analysis'}
          </button>
          {!origin || !dest ? <p className="text-[11.5px] text-ink-3">Choose an origin and a destination anywhere in Hyderabad. The road route is generated from a routing provider, then segmented and analysed.</p> : null}
        </Panel>

        <div className="pointer-events-auto min-h-0 overflow-y-auto space-y-2.5 pr-0.5">
          {!res && <InfrastructurePanel status={run.isPending ? 'corridor' : 'idle'} hasCorridor={Boolean(origin && dest)} onAnalyze={analyze} />}
          {run.isError && <ErrorState error={run.error} what="corridor analysis" />}
          {res && <Results res={res} origin={origin?.label} dest={dest?.label} candIdx={candIdx} setCandIdx={setCandIdx} onSeg={setSegId}
            infra={<InfrastructurePanel status={infraStatus} infra={infra.data} error={infra.isError ? (infra.error as Error).message.replace(/^"|"$/g, '') : undefined} hasCorridor onAnalyze={() => infra.mutate()} />} />}
        </div>
      </aside>

      {showFly && infra.data && <FlyoverDetail infra={infra.data} onClose={() => setShowFly(false)} />}
      {seg && !showFly && <SegmentCard seg={seg} candidate={cand} onClose={() => setSegId(null)} />}
      <Legend />
    </div>
  )
}

function Bar({ v }: { v: number | null }) {
  return <div className="h-1.5 bg-line rounded-full overflow-hidden"><div className="h-full bg-ink" style={{ width: `${v ?? 0}%` }} /></div>
}

function Results({ res, origin, dest, candIdx, setCandIdx, onSeg, infra }: { res: Corridor; origin?: string; dest?: string; candIdx: number; setCandIdx: (i: number) => void; onSeg: (id: string) => void; infra: React.ReactNode }) {
  const c = res.corridor, cand = res.candidates[candIdx]
  const info = useQuery({ queryKey: ['flyover-info'], queryFn: api.flyoverModelInfo, enabled: false })
  const [more, setMore] = useState(false)
  const counts = res.bottlenecks.counts
  return (
    <>
      <Panel title="Corridor analysis" bodyClass="p-3 space-y-2">
        <div className="text-[13px] font-semibold">{origin ?? 'Origin'} <ArrowDown className="inline size-3 -rotate-90" aria-hidden /> {dest ?? 'Destination'}</div>
        <dl className="grid grid-cols-3 gap-2 text-[12px]">
          <div><dt className="eyebrow">Distance</dt><dd className="font-semibold">{c.distance_km} km</dd></div>
          <div><dt className="eyebrow">Est. time</dt><dd className="font-semibold">{c.estimated_travel_time_min} min</dd></div>
          <div><dt className="eyebrow">Traffic status</dt><dd className="font-semibold" style={{ color: BN_COLOR[c.traffic_status] }}>{c.traffic_status === 'NO_DATA' ? 'No data' : c.traffic_status}</dd></div>
        </dl>
        <p className="text-[11.5px] text-ink-3">{res.data_notice}</p>
        <p className="text-[11.5px] text-ink-3">Simulation coverage of this route: <b>{c.coverage_pct}%</b>. Stretches beyond the simulated area have no data and are excluded from every score.</p>
      </Panel>

      <Panel title="Bottlenecks" bodyClass="p-3 space-y-2">
        <div className="flex gap-1 h-2 rounded overflow-hidden" role="img" aria-label="Bottleneck level mix">
          {(['CRITICAL', 'HIGH', 'MODERATE', 'LOW', 'NO_DATA'] as Bn[]).map(k => counts[k] > 0 && <div key={k} style={{ flex: counts[k], background: BN_COLOR[k] }} title={`${k}: ${counts[k]}`} />)}
        </div>
        <p className="text-[12.5px]">{res.bottlenecks.segments.length ? <><b>{res.bottlenecks.segments.length}</b> of {c.segments} segments are HIGH or CRITICAL ({counts.CRITICAL} critical). </> : 'No HIGH or CRITICAL bottleneck segment on this corridor. '}
          {counts.NO_DATA > 0 && <span className="text-ink-3">{counts.NO_DATA} segments have no data.</span>}</p>
        {res.bottlenecks.segments.length > 0 && <div className="flex flex-wrap gap-1">{res.bottlenecks.segments.map(id => <button key={id} type="button" onClick={() => onSeg(id)} className="h-6 px-2 rounded border border-line text-[11px] hover:border-ink-3">{id}</button>)}</div>}
      </Panel>

      {infra}

      <Panel title="Flyover analysis" bodyClass="p-3 space-y-3">
        {!res.candidates.length && <p className="text-[12.5px] text-ink-2">No candidate zone: no group of HIGH/CRITICAL bottleneck segments was found on this corridor, so a flyover study is not indicated by this data.</p>}
        {res.candidates.length > 1 && <div className="flex gap-1.5" role="tablist" aria-label="Candidate zones">
          {res.candidates.map((k, i) => <button key={k.name} role="tab" aria-selected={i === candIdx} onClick={() => setCandIdx(i)}
            className={`h-7 px-2.5 rounded border text-[12px] ${i === candIdx ? 'bg-ink text-paper border-ink' : 'border-line'}`}>{k.name} · {k.suitability.score ?? '–'}</button>)}
        </div>}
        {cand && <Candidate c={cand} />}
      </Panel>

      <Panel title="Data confidence" bodyClass="p-3 space-y-2">
        <div className="text-[20px] font-semibold num">{res.confidence.score}%</div>
        <p className="text-[11.5px] text-ink-3">{res.confidence.meaning}</p>
        {Object.entries(res.confidence.components).map(([k, v]) => <div key={k}><div className="flex justify-between text-[12px]"><span>{COMP[k] ?? k}</span><span className="num">{v}%</span></div><Bar v={v} /></div>)}
        {res.confidence.missing_fields.length > 0 && <p className="text-[11.5px] text-ink-3">Fields with no data on this corridor: {res.confidence.missing_fields.join(', ')}.</p>}
      </Panel>

      <div className="rounded-md border border-[#ecc99a] bg-[#fff8ee] px-3 py-2 text-[12px] text-[#6b3a05]" role="note">
        <div className="font-semibold mb-1 flex items-center gap-1.5"><AlertTriangle className="size-3.5" aria-hidden />{res.disclaimer.text}</div>
        <ul className="list-disc pl-4">{res.disclaimer.layers.map(l => <li key={l}>{l}</li>)}</ul>
      </div>

      <Panel title="Pipeline and scoring" bodyClass="p-3 space-y-1.5">
        <ol className="text-[12px] space-y-0.5" aria-label="Analysis stages">{res.stages.map(s => <li key={s.stage} className="flex justify-between"><span>✓ {s.stage}</span><span className="num text-ink-3">{s.ms} ms</span></li>)}</ol>
        <button type="button" className="text-[12px] text-accent underline" onClick={() => { setMore(m => !m); if (!info.data) void info.refetch() }}>{more ? 'Hide' : 'How is this scored?'}</button>
        {more && (info.isFetching ? <p className="text-[12px] text-ink-3">Loading…</p> : info.data ? (
          <div className="text-[12px] space-y-1.5">
            <p>Scores are transparent weighted indicators (weights in <code>corridor_config.json</code>); there is no labelled flyover dataset, so they cannot be reported as accuracy.</p>
            {info.data.evaluation && <p>Benchmark against the organizer bottleneck flag: ROC-AUC <b>{info.data.evaluation.roc_auc}</b> ({info.data.evaluation.roc_auc_without_throughput_ceiling} without the throughput-ceiling component), precision@{info.data.evaluation.k} <b>{info.data.evaluation.precision_at_k}</b>: weak agreement, so treat rankings as screening only.</p>}
            <p>Traffic forecast: {info.data.forecaster?.model} (v{info.data.forecaster?.version}). {info.data.forecaster?.why_not_deep_learning}</p>
          </div>) : null)}
      </Panel>
    </>
  )
}

function Candidate({ c }: { c: Candidate }) {
  const s = c.suitability
  return (
    <div className="space-y-3">
      <div className="rounded border border-[#d9ccf5] bg-[#f6f2ff] px-3 py-2">
        <div className="eyebrow text-[#5b21b6]">Candidate detected · {c.name}</div>
        <dl className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
          <dt className="text-ink-3">Start</dt><dd className="num">{c.start[0].toFixed(5)}, {c.start[1].toFixed(5)}</dd>
          <dt className="text-ink-3">End</dt><dd className="num">{c.end[0].toFixed(5)}, {c.end[1].toFixed(5)}</dd>
          <dt className="text-ink-3">Length</dt><dd>~{(c.estimated_length_m / 1000).toFixed(2)} km</dd>
          <dt className="text-ink-3">Affected segments</dt><dd>{c.affected_segments[0]}–{c.affected_segments[c.affected_segments.length - 1]} ({c.affected_segments.length})</dd>
          <dt className="text-ink-3">Bottleneck segments</dt><dd>{c.bottleneck_segments.length}</dd>
        </dl>
        {c.existing_grade_separation.length > 0 && <p className="mt-1.5 text-[12px] text-[#8a4a06]">The route already passes an existing grade separation here: {c.existing_grade_separation.join(', ')}.</p>}
      </div>

      <div>
        <div className="flex items-baseline justify-between"><span className="eyebrow">Flyover suitability (analytical indicator)</span><span className="text-[22px] font-semibold num">{s.score ?? '–'}<span className="text-[12px] text-ink-3"> / 100</span></span></div>
        {s.screening_level && <p className="text-[11.5px] text-ink-3">Screening level: {s.screening_level}. Not an engineering approval.</p>}
        <div className="mt-1.5 space-y-1.5">{Object.entries(s.breakdown).map(([k, v]) => <div key={k}><div className="flex justify-between text-[12px]"><span>{LABEL[k] ?? k}</span><span className="num">{v ?? 'Data unavailable'}</span></div><Bar v={v} /></div>)}</div>
        <ul className="mt-1.5 text-[11.5px] text-ink-3 list-disc pl-4">{Object.entries(s.excluded).filter(([k]) => ['feasibility', 'pedestrian', 'public_transport'].includes(k)).map(([k, v]) => <li key={k}><b>{k.replace('_', ' ')}</b>: {v}</li>)}</ul>
      </div>

      <div>
        <div className="eyebrow mb-1">Why this zone was identified</div>
        <ol className="list-decimal pl-4 text-[12.5px] space-y-1">{c.explanation.map(e => <li key={e.factor}>{e.text}</li>)}</ol>
        <p className="mt-1.5 text-[11.5px] text-ink-3">{c.reason_for_selection}</p>
      </div>

      <div>
        <div className="eyebrow mb-1">Why this needs further analysis, and alternatives to weigh first</div>
        <ul className="text-[12.5px] space-y-1">{c.alternatives.options.map(o => (
          <li key={o.name} className="flex gap-2"><span className={`mt-0.5 text-[10px] font-semibold rounded-sm px-1 h-4 shrink-0 ${o.supported_by_data ? 'bg-[#e3f3ea] text-[#1f6b43]' : 'bg-line text-ink-3'}`}>{o.supported_by_data === null ? 'NEEDS DATA' : o.supported_by_data ? 'SUPPORTED' : 'NOT INDICATED'}</span><span><b>{o.name}</b> <span className="text-ink-3">{o.basis}</span></span></li>))}</ul>
        {c.alternatives.dataset_planning_candidates.length > 0 && <p className="mt-1.5 text-[11.5px] text-ink-3">Dataset planning candidates on these segments: {c.alternatives.dataset_planning_candidates.map(p => `${p.type.replace('_', ' ')} on ${p.segment} (+${p.capacity_delta_vph} veh/h, cost ${p.cost_index}, ${p.feasibility_band})`).join('; ')}.</p>}
      </div>
    </div>
  )
}

function SegmentCard({ seg, candidate, onClose }: { seg: CorridorSegment; candidate: Candidate | null; onClose: () => void }) {
  const inCand = candidate?.affected_segments.includes(seg.segment_id)
  const rows: [string, string][] = seg.data_status === 'NO_DATA' ? [] : [
    ['Segment length', `${Math.round(seg.length_m)} m`], ['Traffic volume (peak)', stat(seg, 'traffic_volume', ' veh/h')], ['Average speed (peak)', stat(seg, 'average_speed', ' km/h')],
    ['Congestion index', na(seg.congestion_index, ' / 100')], ['Delay', stat(seg, 'delay_min', ' min')], ['Historical incident records', stat(seg, 'accident_count')],
    ['Bottleneck score', na(seg.bottleneck_score, ' / 100')], ['Junctions (route)', na(seg.junction_count)], ['Lanes', stat(seg, 'lane_count')],
    ['Pedestrian activity', 'Data unavailable'], ['Public transport', 'Data unavailable'],
  ]
  return (
    <section aria-label="Segment details" className="absolute z-[1000] right-3 top-3 w-[300px] max-w-[calc(100%-1.5rem)] rounded-md border border-line bg-panel shadow-lg p-3 text-[12.5px]">
      <div className="flex justify-between gap-2"><div><div className="text-[14px] font-semibold">{seg.road_name ?? 'Road name unavailable'}</div><div className="text-ink-3">{seg.segment_id} · <span style={{ color: BN_COLOR[seg.bottleneck_level] }}>{seg.bottleneck_level === 'NO_DATA' ? 'No data' : seg.bottleneck_level}</span></div></div>
        <button type="button" onClick={onClose} aria-label="Close segment details" className="text-ink-3 self-start">✕</button></div>
      {seg.data_status === 'NO_DATA'
        ? <p className="mt-2 text-ink-2">No simulated traffic data within reach of this stretch, so nothing is scored for it.</p>
        : <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5">{rows.map(([k, v]) => <Fragment key={k}><dt className="text-ink-3">{k}</dt><dd className="num text-right">{v}</dd></Fragment>)}</dl>}
      {inCand && <p className="mt-2 text-[#5b21b6]">Inside {candidate!.name} (suitability {candidate!.suitability.score ?? '–'}).</p>}
      <p className="mt-2 text-[11px] text-ink-3">{seg.data_status === 'MATCHED' ? `Matched to simulation segment ${seg.grid_segment_id} (${seg.match_distance_m} m away): data confidence is limited by this approximate match. Simulated data.` : 'Simulated data.'}</p>
    </section>
  )
}

function FlyoverDetail({ infra, onClose }: { infra: Infrastructure; onClose: () => void }) {
  const fly = infra.flyover, c = infra.candidate, t = infra.traffic, land = infra.land_evaluation
  const cell = (v: unknown, unit = '') => (v === null || v === undefined ? 'Data unavailable' : `${typeof v === 'number' ? v.toLocaleString() : v}${unit}`)
  if (!fly || !c) return null
  const rows: [string, string][] = [
    ['Start point', `${c.start[0].toFixed(5)}, ${c.start[1].toFixed(5)}`], ['End point', `${c.end[0].toFixed(5)}, ${c.end[1].toFixed(5)}`],
    ['Length', `~${(c.estimated_length_m / 1000).toFixed(2)} km`], ['Affected segments', `${c.affected_segments.length} (${c.affected_segments[0]}–${c.affected_segments[c.affected_segments.length - 1]})`],
    ['Traffic volume', cell(t.zone_volume_vph, ' veh/h')], ['Current congestion', cell(t.zone_congestion_pct, '%')], ['Peak delay', cell(t.zone_delay_min, ' min')],
    ['Land availability', land ? `${land.land_availability_pct}% (simulated)` : 'Data unavailable'], ['Structures affected', land ? `${land.structures_affected} (simulated)` : 'Data unavailable'],
    ['Estimated cost', fly.estimated_cost_cr == null ? 'Data unavailable' : `₹${Math.round(fly.estimated_cost_cr)} Cr (demo estimate)`],
    ['Expected impact', fly.simulated_impact ? `${fly.simulated_impact.congestion_change_pts} pts congestion (simulated)` : 'Data unavailable'],
    ['Suitability score', `${cell(c.suitability.score)} data-driven · ${cell(fly.scores?.overall)} intervention`], ['Data confidence', `${infra.confidence.overall}% (land and infrastructure simulated)`],
  ]
  return (
    <section aria-label="Flyover candidate details" className="absolute z-[1000] right-3 top-3 w-[320px] max-w-[calc(100%-1.5rem)] rounded-md border border-[#d9ccf5] bg-panel shadow-lg p-3 text-[12.5px]">
      <div className="flex justify-between"><div className="eyebrow text-[#5b21b6]">Flyover candidate · simulation data</div><button type="button" onClick={onClose} aria-label="Close flyover details" className="text-ink-3">✕</button></div>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">{rows.map(([k, v]) => <Fragment key={k}><dt className="text-ink-3">{k}</dt><dd className="num text-right">{v}</dd></Fragment>)}</dl>
      <p className="mt-2 text-[11px] text-ink-3">Planning aid, not engineering approval. Land, cost and impact values are simulated.</p>
    </section>
  )
}

function Legend() {
  return (
    <div className="absolute z-[900] right-3 bottom-3 rounded-md border border-line bg-panel/95 px-3 py-2 text-[11.5px] flex flex-wrap gap-x-3 gap-y-1 max-w-[520px]" role="group" aria-label="Legend">
      {(['LOW', 'MODERATE', 'HIGH', 'CRITICAL', 'NO_DATA'] as Bn[]).map(k => <span key={k} className="inline-flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: BN_COLOR[k] }} />{k === 'NO_DATA' ? 'No data' : k[0] + k.slice(1).toLowerCase()}</span>)}
      <span className="inline-flex items-center gap-1.5"><span className="h-2 w-4 rounded-sm bg-[#6d28d9]/40 border border-[#6d28d9]" />Flyover candidate</span>
      <span className="text-ink-3">Simulated traffic</span>
    </div>
  )
}
