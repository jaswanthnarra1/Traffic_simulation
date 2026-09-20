import { useQuery } from '@tanstack/react-query'
import { Check } from 'lucide-react'
import { ForecastChart } from '../components/ForecastChart'
import { CauseText, Empty, ErrorState, Loading, Panel, StateBadge, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { api } from '../services/api'
import { fmt, signed, timeOnly } from '../utils/format'

export default function Incidents() {
  const { t, seg, setSeg } = useSim()
  const inc = useQuery({ queryKey: ['incidents', t], queryFn: () => api.incidents(t), enabled: Boolean(t) })
  const events = inc.data?.events ?? []
  const focus = seg ?? events[0]?.segment_id ?? null
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] gap-3 p-3">
      <Panel title="Detected events · last 3 hours" right={<Tag kind="DETECTED">from traffic + context only</Tag>} bodyClass="p-0">
        {inc.isLoading ? <Loading /> : inc.error ? <ErrorState error={inc.error} what="events" /> : events.length === 0 ? (
          <Empty>No anomalies detected in the 3 hours up to {t.slice(0, 16)}. Step the clock or pick a scenario on the Dashboard.</Empty>
        ) : (
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-left text-ink-3 border-b border-line">
              {['Segment', 'Severity', 'Cause', 'Confidence', 'Start', 'Duration', 'Likely type'].map(h => <th key={h} className="font-medium px-3 py-2">{h}</th>)}
            </tr></thead>
            <tbody>
              {events.map(e => (
                <tr key={`${e.segment_id}-${e.start}`} onClick={() => setSeg(e.segment_id)} tabIndex={0}
                  onKeyDown={k => k.key === 'Enter' && setSeg(e.segment_id)}
                  className={`border-b border-line/70 cursor-pointer hover:bg-paper ${focus === e.segment_id ? 'bg-paper' : ''}`}>
                  <td className="px-3 py-2 num font-medium">{e.segment_id}</td>
                  <td className="px-3">{e.state ? <StateBadge state={e.state} /> : <span className="text-ink-3">resolved</span>}</td>
                  <td className="px-3"><CauseText cause={e.cause} /></td>
                  <td className="px-3">{e.confidence_label ?? '—'}</td>
                  <td className="px-3 num">{timeOnly(e.start)}</td>
                  <td className="px-3 num">{e.duration_min} min{e.ongoing ? ' · ongoing' : ''}</td>
                  <td className="px-3 text-ink-2">{e.likely_type ? (e.likely_type === 'UNKNOWN' ? 'undetermined' : e.likely_type.replace('_', ' ')) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="px-3 py-2 text-[11px] text-ink-3">"Detected incident" = the detector flagged local incident-like behavior. "Likely type" is a separate, weak inference (see Evaluation) and is shown as undetermined when evidence is weak.</p>
      </Panel>
      {focus ? <IncidentDetail seg={focus} t={t} /> : <Panel title="Incident detail"><Empty>No event selected.</Empty></Panel>}
    </div>
  )
}

function IncidentDetail({ seg, t }: { seg: string; t: string }) {
  const d = useQuery({ queryKey: ['segment', seg, t], queryFn: () => api.segment(seg, t) })
  const fc = useQuery({ queryKey: ['forecast', seg, t, 'live'], queryFn: () => api.forecast(seg, t, 'live') })
  const pr = useQuery({ queryKey: ['prop', seg, t, null], queryFn: () => api.propagation(seg, t) })
  const rec = useQuery({ queryKey: ['rec', seg, t], queryFn: () => api.recommendations(seg, t), staleTime: Infinity })
  if (d.isLoading) return <Panel title="Incident detail"><Loading /></Panel>
  if (!d.data) return <Panel title="Incident detail"><ErrorState error={d.error} what="segment" /></Panel>
  const { state, diagnosis: dg, evidence } = d.data
  const speedEv = evidence.find(e => e.metric === 'speed')
  const f = fc.data?.forecast.congestion.forecast
  const atRisk = pr.data?.neighbors.filter(n => n.impact_level === 'HIGH' || n.impact_level === 'MEDIUM') ?? []
  const steps: [string, boolean, React.ReactNode][] = [
    ['Normal', true, <>Baseline speed for this hour and day type: <b className="num">{fmt(speedEv?.baseline, 1)} km/h</b></>],
    ['Anomaly', dg.detected, dg.detected ? <>Flagged at <b className="num">{dg.run_start ? timeOnly(dg.run_start) : '—'}</b>; speed now <b className="num">{fmt(state.speed_kmh, 1)} km/h</b> ({signed(speedEv?.diff_pct, 1, '%')}); score {fmt(state.anomaly_score, 3)}</> : 'Not flagged by the detector at this time.'],
    ['Incident-like behavior', dg.cause === 'TRANSIENT_INCIDENT', <><CauseText cause={dg.cause} /> — {dg.reasons[0]}</>],
    ['Forecast', Boolean(f), f ? <>Congestion now {fmt(f[0].p50, 3)} → +30 min {fmt(f[2].p50, 3)} → +60 min {fmt(f[4].p50, 3)} (P50)</> : 'Loading forecast…'],
    ['Propagation', atRisk.length > 0, pr.data ? (atRisk.length ? <>{atRisk.length} neighbors at medium/high risk: <span className="num">{atRisk.slice(0, 5).map(n => n.segment_id).join(', ')}</span></> : 'No neighbor at medium/high risk.') : 'Loading…'],
    ['Recommendation', Boolean(rec.data), rec.data ? <>{rec.data.advisory.recommendation} · confidence {rec.data.advisory.confidence}</> : rec.isLoading ? 'Searching and simulating candidates…' : 'Unavailable'],
  ]
  return (
    <div className="flex flex-col gap-3">
      <Panel title={`Incident detail · ${seg}`} right={<StateBadge state={state.state} />}>
        <ol className="relative">
          {steps.map(([label, done, body], i) => (
            <li key={label} className="grid grid-cols-[22px_1fr] gap-2 pb-3">
              <div className="flex flex-col items-center">
                <span className={`size-5 rounded-full grid place-items-center border ${done ? 'bg-ink border-ink text-paper' : 'border-line-strong text-ink-3'}`}>
                  {done ? <Check className="size-3" /> : <span className="text-[10px]">{i + 1}</span>}</span>
                {i < steps.length - 1 && <span className="w-px flex-1 bg-line-strong mt-1" />}
              </div>
              <div><div className="font-semibold">{label}</div><div className="text-ink-2">{body}</div></div>
            </li>
          ))}
        </ol>
      </Panel>
      <Panel title="Speed · last 2 h and forecast" right={<Tag kind="LIVE" />}>
        {fc.data ? <ForecastChart fc={fc.data} metric="speed" height={200} /> : fc.isLoading ? <Loading /> : <ErrorState error={fc.error} what="forecast" />}
      </Panel>
    </div>
  )
}
