import type { TrafficState } from '../types'

export const STATE_COLOR: Record<TrafficState, string> = {
  NORMAL: 'var(--color-state-normal)',
  MODERATE: 'var(--color-state-moderate)',
  HEAVY: 'var(--color-state-heavy)',
  CRITICAL: 'var(--color-state-critical)',
  NO_DATA: 'var(--color-state-nodata)',
}
// Leaflet paths need literal colours (CSS variables are not resolved inside SVG attributes by all browsers)
export const STATE_HEX: Record<TrafficState, string> = {
  NORMAL: '#2f8f5b', MODERATE: '#d4a21a', HEAVY: '#e0661b', CRITICAL: '#c62828', NO_DATA: '#9ca3af',
}
export const STATES: TrafficState[] = ['NORMAL', 'MODERATE', 'HEAVY', 'CRITICAL', 'NO_DATA']

export const CAUSE_LABEL: Record<string, string> = {
  TRANSIENT_INCIDENT: 'Transient incident',
  RECURRING_BOTTLENECK: 'Recurring bottleneck',
  WEATHER_EVENT_EFFECT: 'Weather / event effect',
  ROADWORK_EFFECT: 'Roadwork effect',
  PROPAGATED_CONGESTION: 'Propagated congestion',
  NORMAL_VARIATION: 'Normal variation',
  UNKNOWN: 'Unknown cause',
  RESOLVED: 'Resolved',
}

export function fmt(v: number | null | undefined, digits = 1, unit = ''): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${v.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}${unit ? ` ${unit}` : ''}`
}

export function signed(v: number | null | undefined, digits = 1, unit = ''): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${v > 0 ? '+' : ''}${fmt(v, digits)}${unit}`
}

export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(digits)}%`

/** Simulation clock: naive timestamps "YYYY-MM-DD HH:MM:SS" (no timezone in the dataset). */
export function shiftTime(t: string, minutes: number): string {
  const d = new Date(t.replace(' ', 'T') + 'Z')
  d.setUTCMinutes(d.getUTCMinutes() + minutes)
  return d.toISOString().slice(0, 19).replace('T', ' ')
}

export function clampTime(t: string, lo: string, hi: string): string {
  return t < lo ? lo : t > hi ? hi : t
}

export const timeOnly = (t: string) => t.slice(11, 16)
export const dateLabel = (t: string) => {
  const d = new Date(t.replace(' ', 'T') + 'Z')
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', timeZone: 'UTC' })
}

/** Divergent colour for a simulated change in travel time (minutes / vehicle). */
export function deltaColor(delta: number): string {
  if (Math.abs(delta) < 0.005) return '#b9b6ae'
  return delta < 0 ? '#0f766e' : '#b4410c'
}

/** Sequential v/c colour (same scale for baseline and counterfactual maps). */
export function vcColor(vc: number): string {
  if (!Number.isFinite(vc)) return STATE_HEX.NO_DATA
  if (vc < 0.6) return STATE_HEX.NORMAL
  if (vc < 0.85) return STATE_HEX.MODERATE
  if (vc < 1.0) return STATE_HEX.HEAVY
  return STATE_HEX.CRITICAL
}

export function riskColor(risk: number): string {
  const a = Math.max(0.15, Math.min(1, risk))
  return `rgba(109, 63, 192, ${a.toFixed(2)})`
}
