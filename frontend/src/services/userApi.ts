import type { Explanation, ExplainTarget, GeoResult, Place, RouteResponse, UserAlert, UserConfig, UserTraffic } from '../types/user'

// Public rider API. No operator session, no secrets in the browser: FlowSense's backend talks to the map providers.
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class UserApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

const FALLBACK: Record<number, string> = {
  0: "Can't reach FlowSense right now. Check your connection and try again.",
  404: 'Nothing was found for that request.',
  422: 'Please check the places you entered and try again.',
  429: "You're going a bit fast. Please wait a moment and try again.",
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e                       // cancelled by a newer request: not an error to show
    throw new UserApiError(0, FALLBACK[0])
  }
  if (!res.ok) {
    let detail: string | undefined
    try { const b = await res.json(); if (typeof b.detail === 'string') detail = b.detail } catch { /* not JSON: use the fallback */ }
    throw new UserApiError(res.status, detail ?? FALLBACK[res.status] ?? 'Something went wrong on our side. Please try again.')
  }
  return res.json() as Promise<T>
}

export const isAbort = (e: unknown) => (e as Error)?.name === 'AbortError'
export const errorText = (e: unknown) => (e instanceof UserApiError ? e.message : FALLBACK[0])

const post = (body: unknown, signal?: AbortSignal): RequestInit => ({ method: 'POST', body: JSON.stringify(body), signal })

export const userApi = {
  config: () => call<UserConfig>('/api/user/config'),
  traffic: (step: number) => call<UserTraffic>(`/api/user/traffic?step=${step}`),
  alerts: (step: number) => call<{ alerts: UserAlert[]; time: string }>(`/api/user/alerts?step=${step}`),
  explain: (t: ExplainTarget, step: number) =>
    call<Explanation>('/api/user/explain', post(t.segment_id ? { segment_id: t.segment_id, step } : { lat: t.lat, lon: t.lon, step })),
  geocode: (q: string, signal?: AbortSignal) => call<{ results: GeoResult[] }>(`/api/user/geocode?q=${encodeURIComponent(q)}`, { signal }),
  routes: (origin: { lat: number; lon: number }, destination: { lat: number; lon: number }, step: number, signal?: AbortSignal) =>
    call<RouteResponse>('/api/user/route-options', post({ origin: { lat: origin.lat, lon: origin.lon }, destination: { lat: destination.lat, lon: destination.lon }, step }, signal)),
  nearby: (lat: number, lon: number, categories: string[], radius_m: number, signal?: AbortSignal) =>
    call<{ places: Place[]; radius_m: number }>('/api/user/nearby', post({ lat, lon, categories, radius_m }, signal)),
}
