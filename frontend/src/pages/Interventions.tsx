import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Play, XCircle } from 'lucide-react'
import { useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Legend, LegendRow, TrafficMap, type Fit, type IncidentMark, type SegmentStyle } from '../components/TrafficMap'
import { EvidenceTable, Empty, ErrorState, Loading, Panel, StateBadge, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { useFocusSegment } from '../hooks/useTraffic'
import { api } from '../services/api'
import type { Candidate } from '../types'
import { STATE_HEX, fmt, signed } from '../utils/format'
import { BETTER, SOURCE } from '../utils/mapStyles'
import { SegmentPicker } from './Forecast'

export default function Interventions() {
  const { t, setSeg } = useSim()
  const seg = useFocusSegment()
  const navigate = useNavigate()
  const rec = useQuery({ queryKey: ['rec', seg, t], queryFn: () => api.recommendations(seg!, t), enabled: Boolean(seg && t), staleTime: Infinity })
  const r = rec.data
  const detour = r?.operational_options.find(o => o.path)?.path ?? []
  const candTargets = new Set([...(r?.candidates_ranked ?? []), ...(r?.candidates_not_simulated ?? [])].map(c => c.target_segment))

  const simulate = (c?: Candidate) => {
    const p = new URLSearchParams({ t, seg: seg! })
    if (c) p.set('cand', c.candidate_id)
    if (r?.incident_capacity_reduction_assumed) p.set('red', String(r.incident_capacity_reduction_assumed))
    navigate({ pathname: '/simulation', search: p.toString() })
  }

  const styleFor = useCallback((id: string): SegmentStyle | null => {
    if (id === seg) return SOURCE
    if (detour.includes(id)) return { color: '#1d4ed8', weight: 5, opacity: 0.95 }
    if (candTargets.has(id)) return { color: BETTER, weight: 4, opacity: 0.95, dashed: true }
    return null
  }, [seg, detour, candTargets])
  // frame the affected segment, its legal diversion and the candidate targets once the advisory is ready
  const fit = useMemo<Fit | null>(() => (seg && r ? { key: `${seg}-${r.time}`, ids: [seg, ...detour, ...candTargets] } : null), [seg, r]) // eslint-disable-line react-hooks/exhaustive-deps
  const incidents = useMemo<IncidentMark[]>(() => (seg && r && r.state !== 'NORMAL' ? [{ id: seg, severity: r.state }] : []), [seg, r])

  return (
    <div className="p-3 flex flex-col gap-3">
      <div className="flex items-center gap-4">
        <SegmentPicker value={seg} onChange={setSeg} />
        {r && <StateBadge state={r.state} />}
        <span className="text-ink-3 text-[12px]">Advisory only — nothing is applied to any real system. "Simulate" runs our network model.</span>
      </div>
      {!seg ? <Panel><Empty>Select a segment.</Empty></Panel> : rec.isLoading ? <Panel><Loading label="Searching candidates and simulating each one (≈10–20 s)" /></Panel> :
        rec.error || !r ? <Panel><ErrorState error={rec.error} what="recommendation" /></Panel> : (
          <>
            <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] gap-3">
              <Panel title="Recommendation" right={<Tag kind="SIMULATED">advisory</Tag>}>
                <div className="text-[18px] font-semibold leading-6">{r.advisory.recommendation}</div>
                <div className="mt-1 text-ink-2">Confidence <b className="text-ink">{r.advisory.confidence}</b>
                  {r.incident_capacity_reduction_assumed > 0 && <> · modelled incident capacity loss {Math.round(r.incident_capacity_reduction_assumed * 100)}%</>}</div>
                <h3 className="eyebrow mt-3 mb-1">Why</h3>
                <ul className="list-disc pl-4 space-y-0.5">{r.advisory.reasons.map(x => <li key={x}>{x}</li>)}</ul>
                {r.advisory.simulated_impact && (
                  <>
                    <h3 className="eyebrow mt-3 mb-1">Simulated result</h3>
                    <div className="num grid grid-cols-2 gap-x-4 gap-y-0.5">
                      <span>Network delay {fmt(r.advisory.simulated_impact.kpis.total_delay_veh_h.baseline, 1)} → {fmt(r.advisory.simulated_impact.kpis.total_delay_veh_h.counterfactual, 1)} veh·h</span>
                      <span>({signed(r.advisory.simulated_impact.kpis.total_delay_veh_h.pct_change, 3, '%')})</span>
                      <span>Local delay {fmt(r.advisory.simulated_impact.local_delay_veh_h.before, 2)} → {fmt(r.advisory.simulated_impact.local_delay_veh_h.after, 2)} veh·h</span>
                      <span>improved {r.advisory.simulated_impact.segments_improved} · worsened {r.advisory.simulated_impact.segments_worsened}</span>
                    </div>
                  </>
                )}
                <h3 className="eyebrow mt-3 mb-1">Limitations</h3>
                <ul className="list-disc pl-4 space-y-0.5 text-ink-2">{r.advisory.limitations.map(x => <li key={x}>{x}</li>)}</ul>
              </Panel>
              <div className="flex flex-col gap-3">
                <div className="h-[260px] border border-line rounded-md bg-panel">
                  <TrafficMap className="size-full" styleFor={styleFor} title="Traffic Simulation Network · options" incidents={incidents} fit={fit} wheel="on-click"
                    legend={<Legend sections={[{ title: 'Options', rows: <>
                      <LegendRow color={STATE_HEX.CRITICAL} weight={6} label="Affected segment" />
                      <LegendRow color="#1d4ed8" weight={5} label="Legal diversion" />
                      <LegendRow color={BETTER} weight={4} dashed label="Candidate target" /></> }]} />} />
                </div>
                <Panel title="Operational options">
                  {r.operational_options.length === 0 ? <div className="text-ink-3">None needed.</div> : (
                    <ul className="space-y-1.5">{r.operational_options.map(o => (
                      <li key={o.action}><b>{o.action}</b>
                        {o.path && <span className="text-ink-2"> via <span className="num">{o.path.join(' → ')}</span> · free-flow {fmt(o.free_flow_time_min, 2)} min vs {fmt(o.direct_free_flow_time_min, 2)} min direct · turns {o.legal_turns_verified ? 'verified legal' : 'NOT verified'}</span>}
                        {o.reason && <span className="text-ink-2"> — {o.reason}</span>}
                      </li>))}</ul>
                  )}
                </Panel>
              </div>
            </div>

            <Panel title={`Candidates · ${r.search.direct_candidates} direct, ${r.search.neighbor_candidates} within ${r.search.max_hops} hops`}
              right={<button onClick={() => simulate()} className="text-accent text-[12px]">Simulate incident only →</button>} bodyClass="p-0">
              {r.candidates_ranked.length + r.candidates_not_simulated.length === 0 ? (
                <Empty>No planning candidate targets this segment or its neighbors within {r.search.max_hops} hops — operational-only response.</Empty>
              ) : (
                <table className="w-full text-[12.5px]">
                  <thead><tr className="text-left text-ink-3 border-b border-line">{['Rank', 'Candidate', 'Type', 'Target', 'Relation', 'Feasibility', 'Cost', 'Δ capacity',
                    'Network delay', 'Local delay', 'Score', ''].map(h => <th key={h} className="font-medium px-3 py-2">{h}</th>)}</tr></thead>
                  <tbody>
                    {[...r.candidates_ranked, ...r.candidates_not_simulated].map((c, i) => (
                      <tr key={c.candidate_id} className="border-b border-line/70">
                        <td className="px-3 py-1.5 num">{c.simulated ? i + 1 : '—'}</td>
                        <td className="px-3 num font-medium">{c.candidate_id}</td>
                        <td className="px-3">{c.intervention_type.replace('_', ' ')}</td>
                        <td className="px-3 num">{c.target_segment}</td>
                        <td className="px-3 text-ink-2">{c.relation}</td>
                        <td className="px-3" title={c.feasibility.checks.map(k => `${k.passed ? '✓' : '✗'} ${k.check}${k.detail ? ` (${k.detail})` : ''}`).join('\n')}>
                          <span className="inline-flex items-center gap-1">{c.feasibility.feasible
                            ? <CheckCircle2 className="size-3.5 text-better" aria-label="feasible" /> : <XCircle className="size-3.5 text-worse" aria-label="infeasible" />}{c.feasibility_band}</span></td>
                        <td className="px-3 num">{c.cost_index}</td>
                        <td className="px-3 num">+{c.capacity_delta_vph}</td>
                        <td className="px-3 num">{c.impact ? signed(c.impact.kpis.total_delay_veh_h.pct_change, 3, '%') : '—'}</td>
                        <td className="px-3 num">{c.local_delay_veh_h ? `${fmt(c.local_delay_veh_h.before, 1)}→${fmt(c.local_delay_veh_h.after, 1)}` : '—'}</td>
                        <td className="px-3 num">{c.ranking ? fmt(c.ranking.score, 2) : c.feasibility.feasible ? 'not simulated' : 'infeasible'}</td>
                        <td className="px-3">{c.feasibility.feasible && (
                          <button onClick={() => simulate(c)} className="inline-flex items-center gap-1 h-6 px-2 border border-ink rounded-sm text-[11px] font-semibold hover:bg-ink hover:text-paper">
                            <Play className="size-3" /> SIMULATE</button>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <p className="px-3 py-2 text-[11px] text-ink-3">Score = weighted network + local delay reduction − worsened segments − normalised cost − feasibility penalty (weights in backend/app/config.py). Every candidate is modelled as its capacity_delta_vph on its target segment.</p>
            </Panel>
            <Panel title="Evidence"><EvidenceTable items={r.advisory.evidence} /></Panel>
          </>
        )}
    </div>
  )
}
