import type { ForecastResponse } from '../types'

export type Trend = 'worsening' | 'recovering' | 'stable'

/** Speed trend from the segment's own forecast: now vs the given horizon, relative to free-flow speed (±5% = a real move). */
export function speedTrend(fc: ForecastResponse | undefined, freeFlow: number, horizon = 30): { trend: Trend; now: number; then: number } | null {
  const pts = fc?.forecast.speed.forecast
  const now = pts?.find(p => p.horizon_min === 0)?.p50
  const then = pts?.find(p => p.horizon_min === horizon)?.p50
  if (now == null || then == null || !(freeFlow > 0)) return null
  const d = (then - now) / freeFlow
  return { trend: d < -0.05 ? 'worsening' : d > 0.05 ? 'recovering' : 'stable', now, then }
}
