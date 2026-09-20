import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { MapLegend } from '../components/MapLegend'
import { SegmentCard } from '../components/SegmentCard'
import { TrafficMap, type Fit, type IncidentMark, type SegmentStyle, type TipData } from '../components/TrafficMap'
import { Empty, ErrorState, Loading, Panel, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { useAdjacency } from '../hooks/useAdjacency'
import { useFocusSegment, useTraffic } from '../hooks/useTraffic'
import { api } from '../services/api'
import { fmt } from '../utils/format'
import { SOURCE, impactColor, propagationStyle } from '../utils/mapStyles'
import { corridor } from '../utils/network'
import { SegmentPicker } from './Forecast'

const HORIZONS = [15, 30, 45, 60]

export default function Propagation() {
  const { t, setSeg } = useSim()
  const seg = useFocusSegment()
  const [horizon, setHorizon] = useState(15)
  const pr = useQuery({ queryKey: ['prop', seg, t, horizon], queryFn: () => api.propagation(seg!, t, horizon), enabled: Boolean(seg && t) })
  const risk = useMemo(() => new Map((pr.data?.neighbors ?? []).map(n => [n.segment_id, n])), [pr.data])

  const styleFor = useCallback((id: string): SegmentStyle | null => {
    if (id === seg) return SOURCE
    const n = risk.get(id)
    return (n && propagationStyle(n)) || null
  }, [risk, seg])
  const tooltipFor = useCallback((id: string): TipData | null => {
    const n = risk.get(id)
    if (id === seg) return { title: id, badge: 'source', rows: [['Role', 'incident / source segment']] }
    if (!n) return null
    return { title: id, badge: `${n.direction} · hop ${n.hop}`, rows: [['Impact', n.impact_level], ['Risk', fmt(n.risk_score, 2)],
      ['Time to impact', `~${n.estimated_time_to_impact_min} min`]] }
  }, [risk, seg])
  const adj = useAdjacency()
  const { byId } = useTraffic()
  const incidents = useMemo<IncidentMark[]>(() => {
    const st = seg ? byId.get(seg)?.state : undefined
    return seg && st && st !== 'NORMAL' ? [{ id: seg, severity: st }] : []
  }, [seg, byId])
  // frame the source + its at-risk neighbors; re-frames smoothly when the segment or the horizon changes
  const fit = useMemo<Fit | null>(() => {
    if (!seg) return null
    const ids = pr.data ? [seg, ...pr.data.neighbors.filter(n => n.impact_level !== 'MINIMAL').map(n => n.segment_id)] : adj ? corridor(seg, adj) : []
    return ids.length ? { key: `${seg}-${horizon}`, ids } : null
  }, [seg, horizon, pr.data, adj])

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_400px] gap-3 p-3 h-full min-h-[680px]">
      <div className="flex flex-col gap-3 min-h-0">
        <div className="flex items-center gap-4">
          <SegmentPicker value={seg} onChange={setSeg} />
          <div className="flex gap-1" role="tablist" aria-label="Horizon">{HORIZONS.map(h => (
            <button key={h} role="tab" aria-selected={horizon === h} onClick={() => setHorizon(h)}
              className={`px-2.5 h-7 rounded-sm num ${horizon === h ? 'bg-ink text-paper' : 'border border-line text-ink-2'}`}>+{h}m</button>
          ))}</div>
          <span className="text-ink-3 text-[12px]">Segments whose estimated time to impact is within the horizon.</span>
        </div>
        <div className="flex-1 min-h-[480px] border border-line rounded-md bg-panel">
          <TrafficMap className="size-full" styleFor={styleFor} tooltipFor={tooltipFor} onSelect={setSeg} selected={null}
            incidents={incidents} fit={fit} wheel="always" title="Traffic Simulation Network · propagation"
            overlay={seg ? <SegmentCard seg={seg} t={t} /> : undefined} legend={<MapLegend />} />
        </div>
      </div>
      <Panel title="At-risk neighbors" right={<Tag kind="INFERRED">model estimate</Tag>} className="min-h-0 overflow-auto" bodyClass="p-0">
        {!seg ? <Empty>Select a source segment.</Empty> : pr.isLoading ? <Loading /> : pr.error || !pr.data ? <ErrorState error={pr.error} what="propagation" /> : (
          <>
            <div className="px-3 py-2 text-[12px] text-ink-2 border-b border-line">
              Source <b className="num">{pr.data.source}</b> local speed-ratio drop <b className="num">{fmt(pr.data.source_drop, 3)}</b>.
              {pr.data.source_drop < 0.05 && ' Little to propagate: the source is near its normal speed.'}
            </div>
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-left text-ink-3 border-b border-line">{['Segment', 'Dir.', 'Hop', 'Drop', 'Risk', 'ETA'].map(h =>
                <th key={h} className="font-medium px-3 py-1.5">{h}</th>)}</tr></thead>
              <tbody>
                {pr.data.neighbors.filter(n => n.impact_level !== 'MINIMAL').map(n => (
                  <tr key={n.segment_id} className="border-b border-line/70" title={n.reason}>
                    <td className="px-3 py-1.5 num">{n.segment_id}</td><td className="px-3">{n.direction}</td><td className="px-3 num">{n.hop}</td>
                    <td className="px-3 num">{fmt(n.predicted_speed_ratio_drop, 3)}</td>
                    <td className="px-3 whitespace-nowrap"><span className="inline-block size-2 rounded-full mr-1.5" style={{ background: impactColor(n.impact_level) }} />{n.impact_level}</td>
                    <td className="px-3 num whitespace-nowrap">{n.estimated_time_to_impact_min} min</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="px-3 py-2 text-[11px] text-ink-3">{pr.data.neighbors.filter(n => n.impact_level === 'MINIMAL').length} further neighbors within 3 hops have minimal risk (hidden). {pr.data.model}. Calibration found spillback mostly one hop deep and within one 5-minute step; validation is indirect (see Evaluation).</p>
          </>
        )}
      </Panel>
    </div>
  )
}
