import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { CloudRain, PartyPopper, CalendarOff, Activity } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { ForecastChart } from '../components/ForecastChart'
import { SegmentIntel } from '../components/SegmentIntel'
import { MapLegend } from '../components/MapLegend'
import { SegmentCard } from '../components/SegmentCard'
import { LayerToggles, TrafficMap, type Fit, type IncidentMark, type SegmentStyle, type TipData } from '../components/TrafficMap'
import { CauseText, Empty, ErrorState, Loading, Panel, StateBadge, StateDot, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { useAdjacency } from '../hooks/useAdjacency'
import { useFocusSegment, useTraffic } from '../hooks/useTraffic'
import { api } from '../services/api'
import type { Metric, TrafficState } from '../types'
import { STATES, fmt, signed, timeOnly } from '../utils/format'
import { forecastStyle, propagationStyle, SOURCE, trafficStyle } from '../utils/mapStyles'
import { corridor } from '../utils/network'

export default function Dashboard() {
  const { t, seg, setSeg, update } = useSim()
  const traffic = useTraffic()
  const [visible, setVisible] = useState<Set<TrafficState>>(new Set(STATES))
  const [anomOnly, setAnomOnly] = useState(false)
  const [roadClass, setRoadClass] = useState<'all' | 'arterial' | 'collector'>('all')
  const scen = useQuery({ queryKey: ['scenarios'], queryFn: api.scenarios, staleTime: Infinity })
  const inc = useQuery({ queryKey: ['incidents', t], queryFn: () => api.incidents(t), enabled: Boolean(t) })

  // map layers: traffic problems first, network as faint context, propagation only on request
  const [layerTraffic, setLayerTraffic] = useState(true)
  const [layerNetwork, setLayerNetwork] = useState(true)
  const [layerProp, setLayerProp] = useState(false)
  const [layerIncidents, setLayerIncidents] = useState(true)
  const [horizon, setHorizon] = useState(0)                      // 0 = current, else forecast +N min
  const focus = useFocusSegment()
  const adj = useAdjacency()
  const prop = useQuery({ queryKey: ['prop', focus, t, null], queryFn: () => api.propagation(focus!, t),
    enabled: Boolean(layerProp && focus && t) })
  const propById = useMemo(() => new Map((layerProp ? prop.data?.neighbors ?? [] : []).map(n => [n.segment_id, n])), [layerProp, prop.data])
  const fmap = useQuery({ queryKey: ['forecast-map', t, horizon], queryFn: () => api.forecastMap(t, horizon),
    enabled: horizon > 0 && Boolean(t), placeholderData: keepPreviousData })
  const fById = useMemo(() => new Map(horizon > 0 ? (fmap.data?.segments ?? []).map(x => [x.segment_id, x]) : []), [horizon, fmap.data])

  const styleFor = useCallback((id: string): SegmentStyle | null => {
    if (layerProp && id === focus) return SOURCE
    const n = propById.get(id)
    const ps = n && propagationStyle(n)
    if (ps) return ps
    const r = traffic.byId.get(id)
    if (!r) return trafficStyle('NO_DATA')
    const f = fById.get(id)                                       // forecast state when a horizon is chosen
    const state = f?.state ?? r.state
    const shown = visible.has(state) && (!anomOnly || r.anomalous) && (roadClass === 'all' || r.road_class === roadClass)
    if (!layerTraffic || !shown) return null
    return f ? forecastStyle(state, r.state === 'NORMAL') : trafficStyle(state)
  }, [traffic.byId, visible, anomOnly, roadClass, layerTraffic, layerProp, focus, propById, fById])

  const incidents = useMemo<IncidentMark[]>(() => (layerIncidents
    ? (traffic.data?.segments ?? []).filter(r => r.anomalous).map(r => ({ id: r.segment_id, severity: r.state })) : []), [layerIncidents, traffic.data])

  // camera: frame the corridor when a segment is selected; frame source + affected neighbors when propagation is on.
  // The key ignores time and horizon, so stepping the clock or the forecast never moves the map.
  const fit = useMemo<Fit | null>(() => {
    if (layerProp && focus && prop.data) {
      return { key: `prop-${focus}`, ids: [focus, ...prop.data.neighbors.filter(n => n.impact_level !== 'MINIMAL').map(n => n.segment_id)] }
    }
    return seg && adj ? { key: `seg-${seg}`, ids: corridor(seg, adj) } : null
  }, [layerProp, focus, prop.data, seg, adj])

  const tooltipFor = useCallback((id: string): TipData | null => {
    const r = traffic.byId.get(id)
    if (!r) return { title: id, rows: [['Status', 'no data']] }
    const f = fById.get(id)
    return { title: id, badge: `${r.state} · ${r.road_class}`, rows: [
      ['Speed', `${fmt(r.speed_kmh, 1)} km/h`], ['Flow', `${fmt(r.flow_vph, 0)} veh/h`], ['Congestion', fmt(r.congestion_index, 3)],
      ['Queue', `${fmt(r.queue_veh, 0)} veh`], ['Delay', `${fmt(r.delay_min, 2)} min`], ['Anomaly score', fmt(r.anomaly_score, 3)],
      ...(f ? [[`Forecast +${horizon}`, `${f.state.toLowerCase()} · ${fmt(f.speed_kmh, 1)} km/h`] as [string, string]] : []),
    ] }
  }, [traffic.byId, fById, horizon])

  const horizonBar = (
    <div className="fs-card !rounded-sm p-0.5 flex text-[11px]" role="tablist" aria-label="Map time">
      {[0, 15, 30, 45, 60].map(h => (
        <button key={h} role="tab" aria-selected={horizon === h} onClick={() => setHorizon(h)}
          className={`px-2 h-6 rounded-[3px] num ${horizon === h ? 'bg-ink text-paper' : 'text-ink-2 hover:bg-paper'}`}>{h === 0 ? 'Current' : `+${h}`}</button>
      ))}
    </div>
  )

  const s = traffic.data
  return (
    <div className="grid grid-cols-[210px_minmax(0,1fr)_330px] 2xl:grid-cols-[232px_minmax(0,1fr)_360px] gap-3 p-3 h-full min-h-[760px]">
      {/* LEFT: filters + scenarios */}
      <div className="flex flex-col gap-3 min-h-0">
        <Panel title="Filters">
          <fieldset className="space-y-1">
            <legend className="sr-only">Traffic states</legend>
            {STATES.map(st => (
              <label key={st} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={visible.has(st)} onChange={() => setVisible(v => {
                  const n = new Set(v); if (n.has(st)) n.delete(st); else n.add(st); return n
                })} />
                <StateDot state={st} /><span className="flex-1">{st.replace('_', ' ')}</span>
                <span className="num text-ink-3">{s?.summary.states[st] ?? 0}</span>
              </label>
            ))}
          </fieldset>
          <label className="flex items-center gap-2 mt-3 cursor-pointer">
            <input type="checkbox" checked={anomOnly} onChange={e => setAnomOnly(e.target.checked)} /> Detected anomalies only
          </label>
          <label className="block mt-3">
            <span className="eyebrow">Road class</span>
            <select value={roadClass} onChange={e => setRoadClass(e.target.value as typeof roadClass)}
              className="mt-1 w-full h-7 border border-line rounded-sm bg-paper px-1">
              <option value="all">All</option><option value="arterial">Arterial</option><option value="collector">Collector</option>
            </select>
          </label>
        </Panel>
        <Panel title="Scenarios" className="flex-1 min-h-0 flex flex-col" bodyClass="flex-1 min-h-0 overflow-auto">
          <p className="px-3 pt-2 text-[11px] text-ink-3">Organizer worked examples (training period). Selecting one moves the clock to 10 min after its start. Labels are reference only — the detector never reads them.</p>
          {scen.isLoading ? <Loading /> : scen.error ? <ErrorState error={scen.error} what="scenarios" /> : (
            <ul className="p-2">
              {scen.data!.scenarios.map(sc => (
                <li key={sc.scenario_id}>
                  <button className="w-full text-left px-2 py-1.5 rounded-sm hover:bg-paper flex justify-between gap-2"
                    onClick={() => update({ t: sc.demo_time, seg: sc.target_segment })}>
                    <span><span className="num">{sc.target_segment}</span> <span className="text-ink-3">{sc.organizer_incident_type.replace('_', ' ')}</span></span>
                    <span className="num text-ink-3 text-[11px]">{sc.start_time.slice(5, 16)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {/* CENTER: map + bottom panels */}
      <div className="flex flex-col gap-3 min-h-0">
        <div className="flex-1 min-h-[420px] border border-line rounded-md bg-panel">
          {traffic.error ? <ErrorState error={traffic.error} what="traffic" /> : (
            <TrafficMap className="size-full" styleFor={styleFor} tooltipFor={tooltipFor} onSelect={setSeg} selected={seg}
              incidents={incidents} showNetwork={layerNetwork} fit={fit} wheel="always"
              controls={<div className="flex flex-wrap gap-1.5 items-start">{horizonBar}<LayerToggles layers={[
                { label: 'Traffic state', on: layerTraffic, set: setLayerTraffic },
                { label: 'Simulation network', on: layerNetwork, set: setLayerNetwork },
                { label: `Propagation${focus ? ` · ${focus}` : ''}`, on: layerProp, set: setLayerProp, disabled: !focus },
                { label: 'Incidents', on: layerIncidents, set: setLayerIncidents },
              ]} /></div>}
              badge={horizon > 0 ? <span className="fs-chip !bg-[#fff6dc] !border-state-moderate text-[#8a6a0e] font-semibold">
                FORECAST +{horizon} min · model P50{fmap.isFetching ? ' · updating…' : ''}</span> : undefined}
              overlay={seg ? <SegmentCard seg={seg} t={t} /> : undefined}
              legend={<MapLegend />} />
          )}
        </div>
        <BottomPanels seg={seg} t={t} />
      </div>

      {/* RIGHT: traffic intelligence / segment drawer */}
      <div className="bg-panel border border-line rounded-md min-h-0 overflow-auto">
        {seg ? <SegmentIntel seg={seg} t={t} onClose={() => setSeg(null)} /> : (
          <div>
            <div className="px-3 h-9 flex items-center border-b border-line"><h2 className="eyebrow">Traffic intelligence</h2></div>
            {traffic.isLoading ? <Loading /> : !s ? <ErrorState error={traffic.error} what="traffic" /> : (
              <>
                <div className="grid grid-cols-2 gap-3 p-3">
                  <Big label="Detected anomalies" v={String(s.summary.anomalous_segments)} />
                  <Big label="Mean speed ratio" v={fmt(s.summary.network_mean_speed_ratio, 3)} />
                </div>
                <div className="px-3 pb-3 flex flex-wrap gap-x-4 gap-y-1 text-ink-2">
                  <span className="inline-flex items-center gap-1"><CloudRain className="size-3.5" /> rain {fmt(s.context.rain_intensity, 2)}</span>
                  <span className="inline-flex items-center gap-1"><PartyPopper className="size-3.5" /> event {s.context.event_present ? 'yes' : 'no'}</span>
                  <span className="inline-flex items-center gap-1"><CalendarOff className="size-3.5" /> holiday {s.context.holiday ? 'yes' : 'no'}</span>
                  <span className="inline-flex items-center gap-1"><Activity className="size-3.5" /> network shift {signed(s.context.network_shift, 3)}</span>
                </div>
                <div className="border-t border-line px-3 py-2 flex justify-between items-center">
                  <h3 className="eyebrow">Detected events · last 3 h</h3><Tag kind="DETECTED" />
                </div>
                {inc.isLoading ? <Loading /> : inc.error ? <ErrorState error={inc.error} what="events" /> :
                  inc.data!.events.length === 0 ? <Empty>No anomalies detected in the last 3 hours.</Empty> : (
                    <ul>
                      {inc.data!.events.map(e => (
                        <li key={`${e.segment_id}-${e.start}`}>
                          <button onClick={() => setSeg(e.segment_id)} className="w-full text-left px-3 py-2 border-t border-line/70 hover:bg-paper">
                            <div className="flex justify-between"><span className="num font-medium">{e.segment_id}</span>
                              {e.state ? <StateBadge state={e.state} /> : <span className="text-ink-3">resolved</span>}</div>
                            <div className="text-ink-2"><CauseText cause={e.cause} />{e.confidence_label ? ` · ${e.confidence_label} confidence` : ''}</div>
                            <div className="num text-ink-3 text-[11px]">since {timeOnly(e.start)} · {e.duration_min} min</div>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                <p className="px-3 py-3 text-[11px] text-ink-3 border-t border-line">Click any segment on the map for its intelligence panel.</p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function Big({ label, v }: { label: string; v: string }) {
  return <div><div className="eyebrow">{label}</div><div className="num text-[22px] font-medium">{v}</div></div>
}

function BottomPanels({ seg, t }: { seg: string | null; t: string }) {
  const [metric, setMetric] = useState<Metric>('speed')
  const fc = useQuery({ queryKey: ['forecast', seg, t, 'live'], queryFn: () => api.forecast(seg!, t, 'live'), enabled: Boolean(seg && t) })
  const rec = useQuery({ queryKey: ['rec', seg, t], queryFn: () => api.recommendations(seg!, t), enabled: Boolean(seg && t), staleTime: Infinity })
  if (!seg) return <Panel title="Forecast & impact"><Empty>Select a segment on the map to see its 15–60 min forecast and simulated intervention impact.</Empty></Panel>
  return (
    <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] gap-3 h-[270px]">
      <Panel title={`Forecast · ${seg}`} className="min-h-0" right={
        <div className="flex gap-1" role="tablist">{(['speed', 'flow', 'congestion'] as Metric[]).map(m => (
          <button key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)}
            className={`px-2 h-6 rounded-sm text-[11px] ${metric === m ? 'bg-ink text-paper' : 'text-ink-2 hover:bg-paper'}`}>{m}</button>
        ))}</div>}>
        {fc.isLoading ? <Loading /> : fc.error || !fc.data ? <ErrorState error={fc.error} what="forecast" /> : <ForecastChart fc={fc.data} metric={metric} height={200} />}
      </Panel>
      <Panel title="Simulated impact of the recommendation" className="min-h-0 overflow-auto" right={<Tag kind="SIMULATED">simulation estimate</Tag>}>
        {rec.isLoading ? <Loading label="Searching and simulating candidates" /> : rec.error || !rec.data ? <ErrorState error={rec.error} what="recommendation" /> : (
          <div className="space-y-2">
            <div className="text-[14px] font-semibold">{rec.data.advisory.recommendation}</div>
            <div className="text-ink-3">Confidence {rec.data.advisory.confidence}</div>
            {rec.data.advisory.simulated_impact ? (
              <div className="grid grid-cols-2 gap-2 num">
                <ImpactStat label="Network delay" k={rec.data.advisory.simulated_impact.kpis.total_delay_veh_h} unit="veh·h" />
                <ImpactStat label="Average speed" k={rec.data.advisory.simulated_impact.kpis.avg_speed_kmh} unit="km/h" higherBetter />
                <div><div className="eyebrow">Local delay (seg + neighbors)</div>
                  {fmt(rec.data.advisory.simulated_impact.local_delay_veh_h.before, 2)} → {fmt(rec.data.advisory.simulated_impact.local_delay_veh_h.after, 2)} veh·h</div>
                <div><div className="eyebrow">Segments improved / worsened</div>
                  {rec.data.advisory.simulated_impact.segments_improved} / {rec.data.advisory.simulated_impact.segments_worsened}</div>
              </div>
            ) : <div className="text-ink-2">{rec.data.advisory.reasons.at(-1)}</div>}
          </div>
        )}
      </Panel>
    </div>
  )
}

function ImpactStat({ label, k, unit, higherBetter }: { label: string; k: { baseline: number; counterfactual: number; pct_change: number | null }; unit: string; higherBetter?: boolean }) {
  const good = k.pct_change === null || k.pct_change === 0 ? null : (k.pct_change > 0) === Boolean(higherBetter)
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div>{fmt(k.baseline, 2)} → {fmt(k.counterfactual, 2)} {unit}
        <span className={good === null ? 'text-ink-3' : good ? 'text-better' : 'text-worse'}> ({signed(k.pct_change, 2, '%')})</span></div>
    </div>
  )
}
