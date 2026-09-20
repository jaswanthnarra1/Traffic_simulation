import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ForecastResponse, Metric } from '../types'
import { fmt } from '../utils/format'

const UNITS: Record<Metric, [string, number]> = { speed: ['km/h', 1], flow: ['veh/h', 0], congestion: ['', 3] }

interface Row { x: number; actual?: number | null; p50?: number | null; band?: [number, number] | null; future?: number | null }

export function forecastRows(fc: ForecastResponse, metric: Metric): Row[] {
  const s = fc.forecast[metric]
  const n = s.history.length
  const rows: Row[] = s.history.map((h, i) => ({ x: (i - (n - 1)) * 5, actual: h.value }))
  const map = new Map(rows.map(r => [r.x, r]))
  for (const p of s.forecast) {
    const r = map.get(p.horizon_min) ?? { x: p.horizon_min }
    r.p50 = p.p50
    r.band = p.p10 !== null && p.p90 !== null ? [p.p10, p.p90] : null
    if (!map.has(r.x)) { rows.push(r); map.set(r.x, r) }
  }
  for (const a of fc.actual_future?.[metric] ?? []) {
    const r = map.get(a.horizon_min)
    if (r) r.future = a.value
  }
  return rows.sort((a, b) => a.x - b.x)
}

export function ForecastChart({ fc, metric, height = 220 }: { fc: ForecastResponse; metric: Metric; height?: number }) {
  const [unit, d] = UNITS[metric]
  const rows = forecastRows(fc, metric)
  const backtest = fc.mode === 'BACKTEST'
  return (
    <div style={{ height }} aria-label={`${metric} forecast chart`}>
      <ResponsiveContainer>
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#ebe9e3" vertical={false} />
          <XAxis dataKey="x" type="number" domain={['dataMin', 60]} ticks={[-120, -90, -60, -30, 0, 15, 30, 45, 60]}
            tickFormatter={v => (v === 0 ? 'now' : `${v > 0 ? '+' : ''}${v}`)} tick={{ fontSize: 11, fill: '#7b7e86' }} stroke="#cfccc4" />
          <YAxis tick={{ fontSize: 11, fill: '#7b7e86' }} stroke="#cfccc4" width={48} domain={['auto', 'auto']}
            tickFormatter={v => fmt(v, metric === 'congestion' ? 2 : 0)} />
          <Tooltip formatter={(v: unknown, name: unknown) => {
            const val = Array.isArray(v) ? `${fmt(v[0], d)} – ${fmt(v[1], d)}` : fmt(v as number, d)
            return [`${val} ${unit}`, String(name)]
          }} labelFormatter={v => (v === 0 ? 'now' : `${Number(v) > 0 ? '+' : ''}${v} min`)} contentStyle={{ fontSize: 12, borderRadius: 4 }} />
          <ReferenceLine x={0} stroke="#16181d" strokeDasharray="2 3" />
          <Area dataKey="band" name="P10–P90 band" stroke="none" fill="#1d4ed8" fillOpacity={0.12} isAnimationActive={false} connectNulls />
          <Line dataKey="actual" name="Observed" stroke="#16181d" strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
          <Line dataKey="p50" name="Forecast (P50)" stroke="#1d4ed8" strokeWidth={2} dot={{ r: 2.5 }} isAnimationActive={false} connectNulls />
          {backtest && <Line dataKey="future" name="Recorded future (backtest)" stroke="#b07d09" strokeDasharray="4 3"
            strokeWidth={1.6} dot={{ r: 3, fill: '#b07d09' }} isAnimationActive={false} connectNulls />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
