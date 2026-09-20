import { useMutation } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../services/api'
import type { Impact, Infrastructure, Intervention } from '../types/infrastructure'
import { Panel } from './ui'

export type InfraStatus = 'idle' | 'ready' | 'corridor' | 'infrastructure' | 'done' | 'error'

const SIM = 'text-[10px] font-semibold tracking-wider uppercase text-[#8a4a06] bg-[#fff1dc] border border-[#ecc99a] rounded-sm px-1.5 h-5 inline-flex items-center'
const COLS: [keyof NonNullable<Intervention['scores']>, string][] = [['traffic_impact', 'Traffic'], ['land_feasibility', 'Land'], ['construction_feasibility', 'Build'], ['cost_efficiency', 'Cost'], ['long_term_impact', 'Long-term']]
const signed = (n: number, unit = '') => `${n > 0 ? '+' : ''}${n}${unit}`
const cr = (n: number | null | undefined) => (n == null ? 'Data unavailable' : `₹${Math.round(n).toLocaleString()} Cr`)

/**
 * "Infrastructure Recommendation" card. Renders whatever /api/infrastructure/analyze returned: no number in this file is a result.
 * States: no corridor / ready / analysing (two real phases) / done / simulation running / simulation complete / error.
 */
export function InfrastructurePanel({ status, infra, error, hasCorridor, onAnalyze }: { status: InfraStatus; infra?: Infrastructure; error?: string; hasCorridor: boolean; onAnalyze: () => void }) {
  if (status === 'idle') {
    return <Panel title="Infrastructure recommendation" right={<span className={SIM}>Simulation data</span>} bodyClass="p-3">
      <p className="text-[12.5px] text-ink-2">{hasCorridor ? 'Ready to analyze.' : 'Select a road corridor to begin infrastructure analysis.'}</p>
      {hasCorridor && <button type="button" onClick={onAnalyze} className="mt-2 h-8 px-3 rounded-md bg-ink text-paper text-[12.5px] font-semibold">Analyze Corridor</button>}
    </Panel>
  }
  if (status === 'corridor' || status === 'infrastructure') {
    const steps = [['Analyzing corridor…', 'corridor'], ['Analyzing traffic…', 'corridor'], ['Detecting bottlenecks…', 'corridor'], ['Evaluating land…', 'infrastructure'], ['Comparing interventions…', 'infrastructure']] as const
    const at = status === 'corridor' ? 0 : 3
    return <Panel title="Infrastructure recommendation" bodyClass="p-3"><ol role="status" aria-label="Analysis progress" className="text-[12.5px] space-y-1">
      {steps.map(([t, ph], i) => <li key={t} className={ph === status || (status === 'infrastructure' && i < at) ? 'text-ink' : 'text-ink-3'}>
        {status === 'infrastructure' && i < at ? '✓' : ph === status ? '›' : '○'} {t}</li>)}
    </ol><Loader2 className="size-4 animate-spin text-ink-3 mt-2" aria-hidden /></Panel>
  }
  if (status === 'error' || !infra) {
    return <Panel title="Infrastructure recommendation" bodyClass="p-3"><p role="alert" className="text-[12.5px] text-[#9f2a1c]">{error ?? 'The infrastructure analysis is unavailable right now.'}</p>
      {hasCorridor && <button type="button" onClick={onAnalyze} className="mt-2 text-[12.5px] underline">Try again</button>}</Panel>
  }
  return <Done infra={infra} />
}

function Done({ infra }: { infra: Infrastructure }) {
  const rec = infra.recommendation
  const applicable = infra.interventions.filter(i => i.applicable)
  const [pick, setPick] = useState(rec.action !== 'none' ? rec.action : applicable[0]?.id ?? '')
  useEffect(() => { setPick(rec.action !== 'none' ? rec.action : applicable[0]?.id ?? '') }, [infra.corridor_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const sim = useMutation({ mutationFn: (id: string) => api.simulateInfrastructure(infra.corridor_id, id) })
  const land = infra.land_evaluation
  const conf = infra.confidence
  const imp = rec.simulated_impact
  return (
    <>
      <Panel title="AI infrastructure recommendation" right={<span className={SIM}>{infra.labels.banner}</span>} bodyClass="p-3 space-y-3">
        <div>
          <div className="text-[16px] font-semibold leading-tight">{rec.headline}</div>
          <p className="mt-1 text-[12.5px] text-ink-2">{rec.summary}</p>
          {rec.caution && <p className="mt-1 text-[12px] text-[#8a4a06]">{rec.caution}</p>}
        </div>
        {rec.why.length > 0 && <div><div className="eyebrow mb-1">Why</div><ul className="text-[12.5px] space-y-1 list-disc pl-4">{rec.why.map(w => <li key={w}>{w}</li>)}</ul></div>}
        {rec.candidate && (
          <div className="rounded border border-[#d9ccf5] bg-[#f6f2ff] px-3 py-2 text-[12px]">
            <div className="eyebrow text-[#5b21b6]">Candidate</div>
            <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              <dt className="text-ink-3">Start</dt><dd>{rec.candidate.start_label} <span className="num text-ink-3">({rec.candidate.start[0].toFixed(4)}, {rec.candidate.start[1].toFixed(4)})</span></dd>
              <dt className="text-ink-3">End</dt><dd>{rec.candidate.end_label} <span className="num text-ink-3">({rec.candidate.end[0].toFixed(4)}, {rec.candidate.end[1].toFixed(4)})</span></dd>
              <dt className="text-ink-3">Length</dt><dd>~{rec.candidate.length_km} km</dd>
              <dt className="text-ink-3">Est. cost</dt><dd>{cr(rec.candidate.estimated_cost_cr)} <span className="text-ink-3">(demo estimate, not official)</span></dd>
            </dl>
          </div>
        )}
        {imp && (
          <div>
            <div className="flex items-center gap-2 mb-1"><span className="eyebrow">Simulated impact</span><span className={SIM}>Simulated</span></div>
            <dl className="grid grid-cols-2 gap-2 text-[12px]">
              <div><dt className="text-ink-3">Congestion</dt><dd className="font-semibold num">{signed(imp.congestion_change_pts, ' pts')} <span className="font-normal text-ink-3">({signed(imp.congestion_change_pct, '%')})</span></dd></div>
              <div><dt className="text-ink-3">Peak travel time (zone)</dt><dd className="font-semibold num">{signed(imp.peak_travel_time_change_min, ' min')}</dd></div>
              <div><dt className="text-ink-3">Average speed</dt><dd className="font-semibold num">{signed(imp.speed_change_pct, '%')}</dd></div>
              <div><dt className="text-ink-3">Emissions</dt><dd className="font-semibold num">{signed(imp.emissions_change_pct, '%')}</dd></div>
            </dl>
          </div>
        )}
        <p className="text-[11px] text-ink-3">Data status — Traffic: {infra.labels.traffic}. Land: {infra.labels.land}. Infrastructure: {infra.labels.infrastructure}.</p>
      </Panel>

      {infra.interventions.length > 0 && (
        <Panel title="Intervention analysis" right={<span className={SIM}>Simulated</span>} bodyClass="p-3">
          <table className="w-full text-[11.5px]" aria-label="Intervention comparison">
            <thead><tr className="text-ink-3 text-left"><th className="font-medium pb-1">Intervention</th>{COLS.map(([, l]) => <th key={l} className="font-medium pb-1 text-right px-1">{l}</th>)}<th className="font-medium pb-1 text-right">Overall</th></tr></thead>
            <tbody>{infra.interventions.map(i => (
              <tr key={i.id} className={`border-t border-line ${i.id === rec.action ? 'bg-[#f6f2ff]' : ''} ${i.applicable ? '' : 'text-ink-3'}`}>
                <td className="py-1 pr-1">{i.label}{i.id === rec.action && <span className="ml-1 text-[10px] font-semibold text-[#5b21b6]">RECOMMENDED</span>}
                  {!i.applicable && <div className="text-[10.5px]">Not applicable: {i.reason_not_applicable}</div>}</td>
                {COLS.map(([k]) => <td key={k} className="text-right num px-1">{i.scores ? i.scores[k] : '–'}</td>)}
                <td className="text-right num font-semibold">{i.scores ? i.scores.overall : '–'}</td>
              </tr>))}</tbody>
          </table>
          <p className="mt-1.5 text-[11px] text-ink-3">Scores 0–100 from the corridor's measured features, the simulated land data and configurable weights. Estimated cost: {infra.interventions.filter(i => i.applicable).map(i => `${i.label} ${cr(i.estimated_cost_cr)}`).join(' · ')} (demo estimates).</p>
        </Panel>
      )}

      {land && (
        <Panel title="Land & construction feasibility" right={<span className={SIM}>Simulated land evaluation</span>} bodyClass="p-3 space-y-2">
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12px]">
            <div><dt className="text-ink-3">Land availability</dt><dd className="font-semibold num">{land.land_availability_pct}%</dd></div>
            <div><dt className="text-ink-3">ROW availability</dt><dd className="font-semibold num">{land.row_availability_pct}%</dd></div>
            <div><dt className="text-ink-3">Government / vacant land</dt><dd className="font-semibold num">{land.government_land_pct}%</dd></div>
            <div><dt className="text-ink-3">Private property impact</dt><dd className="font-semibold num">{land.private_property_impact_pct}%</dd></div>
            <div><dt className="text-ink-3">Structures affected</dt><dd className="font-semibold num">{land.structures_affected}</dd></div>
            <div><dt className="text-ink-3">Utility conflict</dt><dd className="font-semibold">{land.utility_conflict}</dd></div>
            <div><dt className="text-ink-3">Environmental constraint</dt><dd className="font-semibold">{land.environmental_constraint}</dd></div>
            <div><dt className="text-ink-3">Construction feasibility</dt><dd className="font-semibold num">{infra.flyover?.scores ? `${infra.flyover.scores.construction_feasibility}/100 (flyover)` : 'Data unavailable'}</dd></div>
          </dl>
          <p className="text-[11px] text-ink-3">{land.label}</p>
        </Panel>
      )}

      {applicable.length > 0 && (
        <Panel title="Simulate intervention" right={<span className={SIM}>Simulated impact</span>} bodyClass="p-3 space-y-2">
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="sim-pick">Intervention to simulate</label>
            <select id="sim-pick" value={pick} onChange={e => { setPick(e.target.value); sim.reset() }} className="flex-1 h-8 rounded border border-line-strong bg-white px-2 text-[12.5px]">
              {applicable.map(i => <option key={i.id} value={i.id}>{i.label}</option>)}
            </select>
            <button type="button" disabled={sim.isPending || !pick} onClick={() => sim.mutate(pick)} className="h-8 px-3 rounded-md bg-ink text-paper text-[12.5px] font-semibold disabled:opacity-40">Simulate Intervention</button>
          </div>
          {sim.isPending && <div role="status" className="text-[12.5px] text-ink-2"><div className="h-1 bg-line rounded overflow-hidden"><div className="h-full w-1/3 bg-ink animate-pulse" /></div><p className="mt-1">Simulation running…</p></div>}
          {sim.isError && <p role="alert" className="text-[12.5px] text-[#9f2a1c]">The simulation could not be completed. {(sim.error as Error)?.message ? '' : ''}Please try again.</p>}
          {sim.data && <BeforeAfter title={sim.data.intervention.label} impact={sim.data.current_vs_after} horizon={sim.data.planning_horizon} growth={sim.data.planning_horizon.demand_growth_pct} disclaimer={sim.data.disclaimer} />}
        </Panel>
      )}

      <Panel title="Data confidence" bodyClass="p-3 space-y-1.5">
        <div className="text-[20px] font-semibold num">{conf.overall}% <span className="text-[12px] font-normal text-ink-3">overall</span></div>
        {(Object.entries(conf.components) as [string, number][]).map(([k, v]) => (
          <div key={k} className="flex justify-between text-[12px]"><span className="capitalize">{k.replace('_', ' ')}</span><span className="num">{v}%{conf.simulated.includes(k) ? ' — SIMULATED' : ''}</span></div>))}
        <p className="text-[11px] text-ink-3">{conf.meaning}</p>
      </Panel>

      <div className="rounded-md border border-[#ecc99a] bg-[#fff8ee] px-3 py-2 text-[12px] text-[#6b3a05]" role="note">
        <div className="font-semibold mb-1 flex items-center gap-1.5"><AlertTriangle className="size-3.5" aria-hidden />Simulation / Demo Data</div>
        {infra.disclaimer}
      </div>
    </>
  )
}

function BeforeAfter({ title, impact, horizon, growth, disclaimer }: { title: string; impact: Impact; horizon: Impact; growth: number; disclaimer: string }) {
  const Row = ({ k, a, b, unit }: { k: string; a: number; b: number; unit: string }) => <tr className="border-t border-line"><td className="py-1 text-ink-3">{k}</td><td className="num text-right">{a}{unit}</td><td className="text-right text-ink-3"><ArrowRight className="size-3 inline" aria-hidden /></td><td className="num text-right font-semibold">{b}{unit}</td></tr>
  return (
    <div className="space-y-2" aria-label="Current versus simulated">
      <div className="eyebrow">Current vs simulated after — {title}</div>
      <table className="w-full text-[12px]"><thead><tr className="text-ink-3"><th className="text-left font-medium">Peak hour, zone</th><th className="text-right font-medium">Current</th><th /><th className="text-right font-medium">After (simulated)</th></tr></thead>
        <tbody>
          <Row k="Average speed" a={impact.before.speed_kmh} b={impact.after.speed_kmh} unit=" km/h" />
          <Row k="Congestion" a={impact.before.congestion_pct} b={impact.after.congestion_pct} unit="%" />
          <Row k="Delay" a={impact.before.delay_min} b={impact.after.delay_min} unit=" min" />
          <tr className="border-t border-line"><td className="py-1 text-ink-3">Capacity</td><td /><td /><td className="num text-right font-semibold">{signed(impact.capacity_change_pct, '%')}</td></tr>
        </tbody></table>
      <div className="grid grid-cols-2 gap-2" aria-hidden>
        {([['Current', impact.before], ['Simulated', impact.after]] as const).map(([l, s]) => (
          <div key={l}><div className="text-[11px] text-ink-3">{l} congestion</div><div className="h-2 bg-line rounded overflow-hidden"><div className="h-full" style={{ width: `${s.congestion_pct}%`, background: s.congestion_pct >= 50 ? '#c62828' : s.congestion_pct >= 30 ? '#e0661b' : s.congestion_pct >= 15 ? '#d4a21a' : '#2f8f5b' }} /></div></div>))}
      </div>
      <p className="text-[11.5px] text-ink-2">With demand grown by {growth}% (planning horizon): speed {horizon.before.speed_kmh} → {horizon.after.speed_kmh} km/h, congestion {horizon.before.congestion_pct}% → {horizon.after.congestion_pct}%.</p>
      <p className="text-[11px] text-ink-3">SIMULATED IMPACT. {disclaimer}</p>
    </div>
  )
}
