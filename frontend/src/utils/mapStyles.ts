import type { SegmentStyle } from '../components/TrafficMap'
import type { PropagationNeighbor, TrafficState } from '../types'
import { STATE_HEX } from './format'

/* Everything returns null for "unremarkable": those segments stay in the faint base network. */

export function trafficStyle(state: TrafficState): SegmentStyle | null {
  switch (state) {
    case 'CRITICAL': return { color: STATE_HEX.CRITICAL, weight: 7, opacity: 0.96 }
    case 'HEAVY': return { color: STATE_HEX.HEAVY, weight: 5, opacity: 0.93 }
    case 'MODERATE': return { color: STATE_HEX.MODERATE, weight: 3.2, opacity: 0.9 }
    case 'NO_DATA': return { color: STATE_HEX.NO_DATA, weight: 1.8, opacity: 0.7, dashed: true }
    default: return null
  }
}

/** Forecast mode: same colours; segments that are normal now but forecast to degrade are dashed ("predicted impact"). */
export function forecastStyle(state: TrafficState, normalNow: boolean): SegmentStyle | null {
  const s = trafficStyle(state)
  return s && normalNow ? { ...s, dashed: true, opacity: 0.85 } : s
}

export const SOURCE: SegmentStyle = { color: STATE_HEX.CRITICAL, weight: 7, opacity: 1 }

/** Propagation: source red → nearby affected orange → lower-emphasis amber. Only these paths animate. */
export function propagationStyle(n: PropagationNeighbor): SegmentStyle | null {
  if (n.impact_level === 'MINIMAL') return null
  const high = n.impact_level === 'HIGH'
  return {
    color: high ? STATE_HEX.HEAVY : STATE_HEX.MODERATE, animate: true,
    weight: n.hop === 1 ? 4.5 : 3, opacity: high ? 0.92 : 0.6,
    label: n.hop === 1 ? `${n.estimated_time_to_impact_min}m` : undefined,
  }
}
export const impactColor = (level: string) => (level === 'HIGH' ? STATE_HEX.HEAVY : level === 'MINIMAL' ? STATE_HEX.NO_DATA : STATE_HEX.MODERATE)
export const RISK = STATE_HEX.MODERATE

/** Simulation: volume/capacity with the same hierarchy (below 0.6 = unremarkable). */
export function vcStyle(vc: number): SegmentStyle | null {
  if (!Number.isFinite(vc)) return { color: STATE_HEX.NO_DATA, weight: 1.8, opacity: 0.7, dashed: true }
  if (vc >= 1.0) return { color: STATE_HEX.CRITICAL, weight: 6.5, opacity: 1 }
  if (vc >= 0.85) return { color: STATE_HEX.HEAVY, weight: 4.8, opacity: 0.95 }
  if (vc >= 0.6) return { color: STATE_HEX.MODERATE, weight: 3.2, opacity: 0.9 }
  return null
}

export const BETTER = '#0f766e'
export const WORSE = '#b4410c'

/** Simulated change in travel time: only segments that moved by more than 1% are emphasised. */
export function changeStyle(deltaMin: number, before: number): SegmentStyle | null {
  const rel = before > 0 ? deltaMin / before : 0
  if (Math.abs(rel) <= 0.01) return null
  const mag = Math.min(1, Math.abs(rel) / 0.2)
  return { color: rel < 0 ? BETTER : WORSE, weight: 3.5 + 3.5 * mag, opacity: 0.8 + 0.2 * mag }
}
