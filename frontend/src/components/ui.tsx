import { AlertTriangle, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { ApiError } from '../services/api'
import type { Evidence, Kpi, TrafficState } from '../types'
import { CAUSE_LABEL, STATE_COLOR, fmt, signed } from '../utils/format'

export function Panel({ title, right, children, className = '', bodyClass = '' }:
  { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`bg-panel border border-line rounded-md ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 px-3 h-9 border-b border-line">
          <h2 className="eyebrow">{title}</h2>
          {right}
        </header>
      )}
      <div className={bodyClass || 'p-3'}>{children}</div>
    </section>
  )
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-ink-3 py-6 justify-center">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  )
}

/** Never fabricates a value: failures render as an explicit "No data available". */
export function ErrorState({ error, what = 'data' }: { error: unknown; what?: string }) {
  const e = error as ApiError
  const msg = e?.status === 0 ? 'Backend unreachable — start the API (see README).'
    : e?.status === 503 ? `Model artifacts unavailable: ${e.message}`
      : e?.status === 404 ? 'Not found.' : e?.message ?? 'Unknown error'
  return (
    <div role="alert" className="flex items-start gap-2 text-ink-2 py-4 px-1">
      <AlertTriangle className="size-4 mt-0.5 text-state-heavy shrink-0" aria-hidden />
      <div><div className="font-medium text-ink">No {what} available</div><div className="text-ink-3">{msg}</div></div>
    </div>
  )
}

export function StateDot({ state }: { state: TrafficState }) {
  return <span className="inline-block size-2 rounded-full" style={{ background: STATE_COLOR[state] }} aria-hidden />
}

export function StateBadge({ state }: { state: TrafficState }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold tracking-wide">
      <StateDot state={state} />{state.replace('_', ' ')}
    </span>
  )
}

const TAG_STYLE: Record<string, string> = {
  LIVE: 'border-ink text-ink',
  BACKTEST: 'border-state-moderate text-[#8a6a0e] bg-[#fff6dc]',
  VALIDATION: 'border-accent text-accent',
  SYNTHETIC: 'border-ink-3 text-ink-2',
  SIMULATED: 'border-better text-better',
  DETECTED: 'border-ink text-ink',
  INFERRED: 'border-ink-3 text-ink-2 border-dashed',
  BENCHMARK: 'border-ink-3 text-ink-2',
}
export function Tag({ kind, children }: { kind: keyof typeof TAG_STYLE | string; children?: ReactNode }) {
  return (
    <span className={`inline-flex items-center h-5 px-1.5 border rounded-sm text-[10px] font-semibold tracking-wider uppercase ${TAG_STYLE[kind] ?? TAG_STYLE.SYNTHETIC}`}>
      {children ?? kind}
    </span>
  )
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="eyebrow truncate">{label}</div>
      <div className="num text-[15px] font-medium text-ink leading-6">{value}</div>
      {sub && <div className="text-[11px] text-ink-3">{sub}</div>}
    </div>
  )
}

export const CauseText = ({ cause }: { cause: string }) => <>{CAUSE_LABEL[cause] ?? cause}</>

const METRIC_LABEL: Record<string, string> = {
  speed: 'Speed', local_speed_ratio_drop: 'Local speed-ratio drop', flow: 'Flow', congestion_index: 'Congestion index',
  queue_change_15min: 'Queue change (15 min)', largest_neighbor_drop: 'Largest neighbor drop',
  network_wide_shift: 'Network-wide shift', rain_intensity: 'Rain intensity', event_present: 'Event present',
  roadwork_active: 'Roadwork active', data_quality: 'Data quality',
}

export function EvidenceTable({ items }: { items: Evidence[] }) {
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-ink-3 border-b border-line">
          <th className="font-medium py-1">Signal</th><th className="font-medium text-right">Now</th>
          <th className="font-medium text-right">Normal</th><th className="font-medium text-right">Δ</th>
          <th className="font-medium text-right">Threshold</th>
        </tr>
      </thead>
      <tbody>
        {items.map(e => {
          const bool = e.metric === 'event_present' || e.metric === 'roadwork_active'
          const val = bool ? (e.value ? 'Yes' : 'No') : fmt(e.value, e.unit === 'veh/h' ? 0 : 2, e.unit)
          return (
            <tr key={e.metric} className="border-b border-line/70 align-top" title={e.note ?? e.source}>
              <td className="py-1 pr-2">{METRIC_LABEL[e.metric] ?? e.metric}
                {e.note && e.metric === 'roadwork_active' && e.value ? <span className="text-ink-3"> · {e.note}</span> : null}</td>
              <td className="num text-right">{val}</td>
              <td className="num text-right text-ink-2">{e.baseline === null ? '—' : fmt(e.baseline, e.unit === 'veh/h' ? 0 : 2)}</td>
              <td className="num text-right">{e.diff_pct === null ? '—' : signed(e.diff_pct, 1, '%')}</td>
              <td className="num text-right text-ink-3">{e.threshold === null ? '—' : fmt(e.threshold, 2)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

const KPI_LABEL: Record<string, [string, number, boolean]> = {
  // label, digits, lower-is-better
  total_delay_veh_h: ['Total delay (veh·h)', 1, true],
  total_travel_time_veh_h: ['Total travel time (veh·h)', 0, true],
  avg_speed_kmh: ['Average speed (km/h)', 2, false],
  avg_congestion_index: ['Congestion index (flow-weighted)', 4, true],
  over_capacity_links: ['Over-capacity segments', 0, true],
  vehicles_on_over_capacity_links_vph: ['Affected vehicles (veh/h on over-capacity segments)', 0, true],
}

export function KpiTable({ kpis }: { kpis: Record<string, Kpi> }) {
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-ink-3 border-b border-line">
          <th className="font-medium py-1">KPI</th><th className="font-medium text-right">Baseline</th>
          <th className="font-medium text-right">Simulated</th><th className="font-medium text-right">Abs. change</th>
          <th className="font-medium text-right">% change</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(KPI_LABEL).filter(([k]) => kpis[k]).map(([k, [label, d, lowerBetter]]) => {
          const v = kpis[k]
          const good = v.abs_change === 0 ? null : (v.abs_change < 0) === lowerBetter
          const color = good === null ? '' : good ? 'text-better' : 'text-worse'
          return (
            <tr key={k} className="border-b border-line/70">
              <td className="py-1 pr-2">{label}</td>
              <td className="num text-right text-ink-2">{fmt(v.baseline, d)}</td>
              <td className="num text-right">{fmt(v.counterfactual, d)}</td>
              <td className={`num text-right ${color}`}>{signed(v.abs_change, d)}</td>
              <td className={`num text-right ${color}`}>{v.pct_change === null ? '—' : signed(v.pct_change, 2, '%')}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-ink-3 py-6 text-center">{children}</div>
}
