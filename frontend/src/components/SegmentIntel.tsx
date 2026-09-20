import { useQuery } from '@tanstack/react-query'
import { ArrowRight, X } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../services/api'
import { fmt, pct, signed } from '../utils/format'
import { CauseText, EvidenceTable, ErrorState, Loading, StateBadge, Tag } from './ui'

function Section({ title, children, tag }: { title: string; children: React.ReactNode; tag?: React.ReactNode }) {
  return (
    <section className="border-t border-line px-3 py-2.5">
      <div className="flex items-center justify-between mb-1.5"><h3 className="eyebrow">{title}</h3>{tag}</div>
      {children}
    </section>
  )
}

export function SegmentIntel({ seg, t, onClose }: { seg: string; t: string; onClose?: () => void }) {
  const { search } = useLocation()
  const d = useQuery({ queryKey: ['segment', seg, t], queryFn: () => api.segment(seg, t) })
  const fc = useQuery({ queryKey: ['forecast', seg, t, 'live'], queryFn: () => api.forecast(seg, t, 'live') })
  const pr = useQuery({ queryKey: ['prop', seg, t, null], queryFn: () => api.propagation(seg, t) })
  const rec = useQuery({ queryKey: ['rec', seg, t], queryFn: () => api.recommendations(seg, t), staleTime: Infinity })

  if (d.isLoading) return <Loading label="Loading segment" />
  if (d.error || !d.data) return <ErrorState error={d.error} what="segment data" />
  const { segment, state, diagnosis: dg, evidence } = d.data
  return (
    <div className="text-[12.5px]">
      <header className="px-3 py-2.5 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2"><span className="text-[16px] font-semibold num">{seg}</span><StateBadge state={state.state} /></div>
          <div className="text-ink-3 mt-0.5">{String(segment.road_class)} · {String(segment.lanes)} lanes · capacity {fmt(Number(segment.capacity_vph), 0)} veh/h
            {state.signalized ? ' · signalized' : ''} · {String(segment.source_node)}→{String(segment.target_node)}</div>
        </div>
        {onClose && <button onClick={onClose} aria-label="Close segment panel" className="p-1 text-ink-3 hover:text-ink"><X className="size-4" /></button>}
      </header>
      <div className="grid grid-cols-3 gap-x-3 gap-y-2 px-3 pb-3">
        <Mini label="Speed" v={fmt(state.speed_kmh, 1, 'km/h')} />
        <Mini label="Flow" v={fmt(state.flow_vph, 0, 'veh/h')} />
        <Mini label="Congestion" v={fmt(state.congestion_index, 3)} />
        <Mini label="Queue" v={fmt(state.queue_veh, 0, 'veh')} />
        <Mini label="Delay" v={fmt(state.delay_min, 2, 'min')} />
        <Mini label="Data quality" v={pct(state.data_quality, 0)} />
      </div>

      <Section title="Anomaly" tag={<Tag kind="DETECTED">{dg.detected ? 'detected' : 'not flagged'}</Tag>}>
        <div className="flex gap-4">
          <Mini label="Score" v={fmt(state.anomaly_score, 3)} />
          <Mini label="Confidence" v={`${dg.confidence_label} (${fmt(dg.confidence, 2)})`} />
          {dg.detected && <Mini label="Duration" v={`${dg.duration_min} min`} />}
        </div>
      </Section>

      <Section title="Diagnosis" tag={<Tag kind="INFERRED">rule-based</Tag>}>
        <div className="font-semibold"><CauseText cause={dg.cause} /></div>
        <ul className="list-disc pl-4 text-ink-2 mt-1 space-y-0.5">{dg.reasons.map(r => <li key={r}>{r}</li>)}</ul>
        {dg.incident_type && (
          <div className="mt-2 text-ink-2">
            <span className="text-ink font-medium">Likely incident type: </span>
            {dg.incident_type.type === 'UNKNOWN' ? 'undetermined' : dg.incident_type.type.replace('_', ' ')}
            <span className="text-ink-3"> · {dg.incident_type.confidence} confidence</span>
            <div className="text-[11px] text-ink-3 mt-0.5">{dg.incident_type.validation_note}</div>
          </div>
        )}
        <details className="mt-2">
          <summary className="cursor-pointer text-accent">Why is this segment {state.state.toLowerCase()}? — evidence</summary>
          <div className="mt-1.5"><EvidenceTable items={evidence} /></div>
        </details>
      </Section>

      <Section title="Forecast" tag={<Tag kind="LIVE">P50 · P10–P90</Tag>}>
        {fc.isLoading ? <Loading /> : fc.error || !fc.data ? <ErrorState error={fc.error} what="forecast" /> : (
          <table className="w-full num text-[12px]">
            <thead><tr className="text-ink-3 text-left"><th className="font-medium"></th>
              {fc.data.forecast.speed.forecast.map(p => <th key={p.horizon_min} className="font-medium text-right">{p.horizon_min ? `+${p.horizon_min}` : 'now'}</th>)}</tr></thead>
            <tbody>
              {(['speed', 'congestion'] as const).map(m => (
                <tr key={m}><td className="text-ink-2 font-sans">{m}</td>
                  {fc.data.forecast[m].forecast.map(p => <td key={p.horizon_min} className="text-right"
                    title={`P10 ${fmt(p.p10, 3)} · P90 ${fmt(p.p90, 3)}`}>{fmt(p.p50, m === 'speed' ? 1 : 3)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Propagation" tag={<Tag kind="INFERRED">model</Tag>}>
        {pr.isLoading ? <Loading /> : pr.error || !pr.data ? <ErrorState error={pr.error} what="propagation" /> :
          pr.data.neighbors.filter(n => n.impact_level !== 'MINIMAL').length === 0 ? <div className="text-ink-3">No neighbor at risk.</div> : (
            <ul className="space-y-0.5">
              {pr.data.neighbors.filter(n => n.impact_level !== 'MINIMAL').slice(0, 5).map(n => (
                <li key={n.segment_id} className="flex justify-between"><span className="num">{n.segment_id}
                  <span className="text-ink-3 font-sans"> · {n.direction}</span></span>
                  <span className="num">{n.impact_level} · ~{n.estimated_time_to_impact_min} min</span></li>
              ))}
            </ul>
          )}
      </Section>

      <Section title="Interventions" tag={<Tag kind="SIMULATED">simulated</Tag>}>
        {rec.isLoading ? <Loading label="Searching and simulating candidates" /> : rec.error || !rec.data ? <ErrorState error={rec.error} what="recommendation" /> : (
          <div>
            <div className="font-semibold">{rec.data.advisory.recommendation}</div>
            <div className="text-ink-3">Confidence {rec.data.advisory.confidence} · {rec.data.search.direct_candidates} direct / {rec.data.search.neighbor_candidates} neighbor candidates</div>
            {rec.data.advisory.simulated_impact && (
              <div className="num mt-1">Simulated network delay {signed(rec.data.advisory.simulated_impact.kpis.total_delay_veh_h.pct_change, 2, '%')}</div>
            )}
            <Link to={{ pathname: '/interventions', search }} className="inline-flex items-center gap-1 text-accent mt-1.5">
              Full advisory <ArrowRight className="size-3.5" /></Link>
          </div>
        )}
      </Section>
    </div>
  )
}

function Mini({ label, v }: { label: string; v: string }) {
  return <div><div className="eyebrow">{label}</div><div className="num text-ink">{v}</div></div>
}
