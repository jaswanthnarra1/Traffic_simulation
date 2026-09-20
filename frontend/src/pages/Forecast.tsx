import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ForecastChart } from '../components/ForecastChart'
import { useNetwork } from '../components/TrafficMap'
import { Empty, ErrorState, Loading, Panel, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { useFocusSegment } from '../hooks/useTraffic'
import { api } from '../services/api'
import type { Metric } from '../types'
import { fmt, signed } from '../utils/format'

export function SegmentPicker({ value, onChange }: { value: string | null; onChange: (s: string) => void }) {
  const net = useNetwork()
  const ids = net.data?.geojson.features.map(f => f.properties.segment_id) ?? []
  return (
    <label className="flex items-center gap-2">
      <span className="eyebrow">Segment</span>
      <select value={value ?? ''} onChange={e => onChange(e.target.value)} className="num h-7 border border-line rounded-sm bg-paper px-1">
        {!value && <option value="">—</option>}
        {ids.map(id => <option key={id} value={id}>{id}</option>)}
      </select>
    </label>
  )
}

const UNIT: Record<Metric, [string, number]> = { speed: ['km/h', 2], flow: ['veh/h', 0], congestion: ['', 4] }

export default function Forecast() {
  const { t, setSeg } = useSim()
  const seg = useFocusSegment()
  const [metric, setMetric] = useState<Metric>('speed')
  const [mode, setMode] = useState<'live' | 'backtest'>('live')
  const fc = useQuery({ queryKey: ['forecast', seg, t, mode], queryFn: () => api.forecast(seg!, t, mode), enabled: Boolean(seg && t) })
  const backtest = mode === 'backtest'
  const [unit, d] = UNIT[metric]
  return (
    <div className="p-3 flex flex-col gap-3">
      <div className="flex items-center gap-4 flex-wrap">
        <SegmentPicker value={seg} onChange={setSeg} />
        <div className="flex gap-1" role="tablist" aria-label="Metric">{(['speed', 'flow', 'congestion'] as Metric[]).map(m => (
          <button key={m} role="tab" aria-selected={metric === m} onClick={() => setMetric(m)}
            className={`px-2.5 h-7 rounded-sm ${metric === m ? 'bg-ink text-paper' : 'border border-line text-ink-2 hover:border-ink-3'}`}>{m}</button>
        ))}</div>
        <div className="ml-auto flex border border-line rounded-sm overflow-hidden" role="tablist" aria-label="Mode">
          <button role="tab" aria-selected={!backtest} onClick={() => setMode('live')}
            className={`px-3 h-7 ${!backtest ? 'bg-ink text-paper' : 'text-ink-2'}`}>Live simulation</button>
          <button role="tab" aria-selected={backtest} onClick={() => setMode('backtest')}
            className={`px-3 h-7 ${backtest ? 'bg-state-moderate text-ink' : 'text-ink-2'}`}>Evaluation / backtest</button>
        </div>
      </div>

      {backtest && (
        <div className="backtest-stripes border border-state-moderate rounded-sm px-3 py-2 text-[12px]">
          <b>BACKTEST MODE.</b> Recorded future values are overlaid to judge the forecast. They are read only for display —
          the forecast shown is identical to live mode and never uses them.
        </div>
      )}

      {!seg ? <Panel><Empty>Select a segment.</Empty></Panel> : fc.isLoading ? <Panel><Loading /></Panel> :
        fc.error || !fc.data ? <Panel><ErrorState error={fc.error} what="forecast" /></Panel> : (
          <>
            <Panel title={`${metric} · ${seg} · issued ${t.slice(0, 16)}`} right={<Tag kind={backtest ? 'BACKTEST' : 'LIVE'} />}>
              <ForecastChart fc={fc.data} metric={metric} height={340} />
              <p className="text-[11px] text-ink-3 mt-1">{fc.data.uncertainty}. Band coverage measured on validation is reported on the Evaluation page.</p>
            </Panel>
            <Panel title="Horizons" bodyClass="p-0">
              <table className="w-full num text-[12.5px]">
                <thead><tr className="text-left text-ink-3 border-b border-line font-sans">
                  {['Horizon', `P10 ${unit}`, `P50 ${unit}`, `P90 ${unit}`, 'Change vs now', ...(backtest ? ['Recorded', 'Error'] : [])].map(h =>
                    <th key={h} className="font-medium px-3 py-2">{h}</th>)}</tr></thead>
                <tbody>
                  {fc.data.forecast[metric].forecast.slice(1).map(p => {
                    const now = fc.data.forecast[metric].forecast[0].p50
                    const actual = fc.data.actual_future?.[metric].find(a => a.horizon_min === p.horizon_min)?.value ?? null
                    return (
                      <tr key={p.horizon_min} className="border-b border-line/70">
                        <td className="px-3 py-1.5 font-sans">+{p.horizon_min} min</td>
                        <td className="px-3">{fmt(p.p10, d)}</td><td className="px-3 font-medium">{fmt(p.p50, d)}</td><td className="px-3">{fmt(p.p90, d)}</td>
                        <td className="px-3">{p.p50 !== null && now !== null ? signed(p.p50 - now, d) : '—'}</td>
                        {backtest && <><td className="px-3 text-[#8a6a0e]">{fmt(actual, d)}</td>
                          <td className="px-3">{actual !== null && p.p50 !== null ? signed(p.p50 - actual, d) : '—'}</td></>}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </Panel>
          </>
        )}
    </div>
  )
}
