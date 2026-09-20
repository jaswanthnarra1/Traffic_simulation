import type { Level, RouteOption } from '../types/user'

/** Traffic colours for riders. Same hues as the operator map so both portals read as one product. */
export const LEVEL_COLOR: Record<Level, string> = {
  free_flow: '#2f8f5b', moderate: '#d4a21a', heavy: '#e0661b', severe: '#c62828', no_data: '#9ca3af',
}
export const LEVEL_LABEL: Record<Level, string> = {
  free_flow: 'Free flow', moderate: 'Moderate traffic', heavy: 'Heavy traffic', severe: 'Severe congestion', no_data: 'No data',
}
export const LEVEL_ORDER: Level[] = ['free_flow', 'moderate', 'heavy', 'severe', 'no_data']

/** 1 h 05 min / 22 min — always a whole, human number (never NaN / undefined). */
export function minutes(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  const m = Math.max(0, Math.round(v))
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min` : `${m} min`
}
export const km = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? '—' : `${v.toFixed(1)} km`)
export const meters = (m: number) => (m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`)

/** A congested route with a sub-minute delay says so, instead of the contradictory "Heavy traffic · No delay". */
export const delayText = (r: RouteOption) =>
  r.delay_min >= 1 ? `+${r.delay_min} min delay` : r.traffic_level === 'free_flow' || r.traffic_level === 'no_data' ? 'No delay' : 'Less than 1 min delay'

/** "Fri 16 Jan · 13:30" from the simulation timestamp (naive local time, no timezone in the dataset). */
export function simClock(t: string | undefined): string {
  if (!t) return '—'
  const d = new Date(t.replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return '—'
  const day = d.toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', timeZone: 'UTC' })
  return `${day} · ${t.slice(11, 16)}`
}

/** Only http(s) links are rendered for a place's website — never a javascript: URL from map data. */
export const safeUrl = (u: string | null | undefined): string | null => (u && /^https?:\/\//i.test(u) ? u : null)

export const inArea = (p: { lat: number; lon: number }, a: { south: number; west: number; north: number; east: number }) =>
  p.lat >= a.south && p.lat <= a.north && p.lon >= a.west && p.lon <= a.east
