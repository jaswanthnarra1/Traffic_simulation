import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'
import { CAUSE_LABEL, fmt } from '../utils/format'
import { speedTrend } from '../utils/insight'
import { useNetwork } from './TrafficMap'
import { StateBadge } from './ui'

const TREND_TEXT = { worsening: 'Worsening by +30 min', recovering: 'Recovering by +30 min', stable: 'Stable through +30 min' } as const
const TREND_COLOR = { worsening: 'text-state-critical', recovering: 'text-better', stable: 'text-ink-2' } as const

/**
 * Compact floating card for the selected segment. It reuses the queries the side panel already runs, so it costs no
 * extra requests. Street names are deliberately absent: the segment is synthetic, so we show its ID, dataset node
 * coordinates and a pointer to the basemap labels — never an invented street.
 */
export function SegmentCard({ seg, t }: { seg: string; t: string }) {
  const net = useNetwork()
  const d = useQuery({ queryKey: ['segment', seg, t], queryFn: () => api.segment(seg, t) })
  const fc = useQuery({ queryKey: ['forecast', seg, t, 'live'], queryFn: () => api.forecast(seg, t, 'live') })
  if (!d.data) return <div className="fs-card px-3 py-2 text-[12px] text-ink-3 num">{seg} · loading…</div>

  const { segment, state, diagnosis: dg } = d.data
  const nodes = new Map((net.data?.nodes ?? []).map(n => [n.node_id, n]))
  const a = nodes.get(String(segment.source_node)), b = nodes.get(String(segment.target_node))
  const loc = a && b ? `${fmt((a.lat + b.lat) / 2, 4)}, ${fmt((a.lon + b.lon) / 2, 4)}` : null
  const tr = speedTrend(fc.data, Number(segment.free_flow_speed_kmh))
  return (
    <div className="fs-card px-3 py-2 text-[12px] w-[300px] max-w-full" title="A simulation segment of the organizer dataset, not a mapped street. Read street and area names from the basemap.">
      <div className="flex items-center justify-between gap-3">
        <div><span className="num font-semibold text-[13.5px]">{seg}</span> <span className="text-ink-3">Simulation Segment</span></div>
        <StateBadge state={state.state} />
      </div>
      <div className="mt-1 leading-4">
        {dg.detected && <span className="eyebrow text-state-critical mr-1.5">Incident detected</span>}
        <span className="font-medium text-ink">{CAUSE_LABEL[dg.cause] ?? dg.cause}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] leading-4">
        <span><span className="text-ink-3">Confidence </span><span className="num">{Math.round(dg.confidence * 100)}%</span></span>
        <span><span className="text-ink-3">Speed </span><span className="num">{fmt(state.speed_kmh, 1)} km/h</span></span>
        <span className={tr ? TREND_COLOR[tr.trend] : 'text-ink-3'}>{tr ? TREND_TEXT[tr.trend] : fc.isLoading ? 'forecast loading…' : 'forecast unavailable'}</span>
      </div>
      <div className="mt-1.5 pt-1.5 border-t border-line text-[10.5px] text-ink-3 num leading-4">
        {String(segment.source_node)} → {String(segment.target_node)} · {String(segment.road_class)}{loc ? <><br />{loc} · segment midpoint</> : ''}
      </div>
    </div>
  )
}
