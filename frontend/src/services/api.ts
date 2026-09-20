import type { Corridor } from '../types/corridor'
import type { Infrastructure, Simulation } from '../types/infrastructure'
import type {
  DetectedEvent, ForecastMap, ForecastResponse, Meta, NetworkResponse, PropagationResponse, Recommendation, Scenario,
  SegmentDetail, SimulationResponse, TrafficSnapshot,
} from '../types'

// Only a public base URL lives in the browser — the app has no secrets to expose.
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    // credentials: the HttpOnly session cookie travels with every call (also when the API is on another origin)
    res = await fetch(`${BASE}${path}`, { ...init, credentials: 'include', headers: { 'Content-Type': 'application/json', ...init?.headers } })
  } catch {
    throw new ApiError(0, 'Backend unreachable')
  }
  if (!res.ok) {
    let detail = res.statusText
    try { detail = JSON.stringify((await res.json()).detail ?? detail) } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

const qs = (p: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams()
  Object.entries(p).forEach(([k, v]) => v !== undefined && v !== null && v !== '' && s.set(k, String(v)))
  const str = s.toString()
  return str ? `?${str}` : ''
}

export interface SessionUser { login_id: string; display_name: string; expires_at: number }

export const api = {
  login: (login_id: string, password: string) =>
    req<SessionUser>('/api/auth/login', { method: 'POST', body: JSON.stringify({ login_id, password }) }),
  analyzeCorridor: (origin: { lat: number; lng: number }, destination: { lat: number; lng: number }) =>
    req<Corridor>('/api/corridor/analyze', { method: 'POST', body: JSON.stringify({ origin, destination }) }),
  analyzeInfrastructure: (origin: { lat: number; lng: number }, destination: { lat: number; lng: number }) =>
    req<Infrastructure>('/api/infrastructure/analyze', { method: 'POST', body: JSON.stringify({ origin, destination }) }),
  simulateInfrastructure: (corridor_id: string, intervention: string) =>
    req<Simulation>('/api/infrastructure/simulate', { method: 'POST', body: JSON.stringify({ corridor_id, intervention }) }),
  flyoverModelInfo: () => req<Record<string, any>>('/api/flyover/model-info'), // eslint-disable-line @typescript-eslint/no-explicit-any
  logout: () => req<{ authenticated: false }>('/api/auth/logout', { method: 'POST' }),
  /** Current session, or null when not signed in (401 is an answer here, not an error). */
  me: () => req<SessionUser>('/api/auth/me').catch(e => {
    if (e instanceof ApiError && e.status === 401) return null
    throw e
  }),
  meta: () => req<Meta>('/api/meta'),
  network: () => req<NetworkResponse>('/api/network'),
  traffic: (t: string) => req<TrafficSnapshot>(`/api/traffic/current${qs({ t })}`),
  segment: (id: string, t: string) => req<SegmentDetail>(`/api/traffic/segment/${encodeURIComponent(id)}${qs({ t })}`),
  forecast: (id: string, t: string, mode: 'live' | 'backtest') =>
    req<ForecastResponse>(`/api/forecast/${encodeURIComponent(id)}${qs({ t, mode })}`),
  forecastMap: (t: string, horizon: number) => req<ForecastMap>(`/api/forecast-map${qs({ t, horizon })}`),
  incidents: (t: string) => req<{ time: string; source: string; events: DetectedEvent[] }>(`/api/incidents${qs({ t })}`),
  propagation: (id: string, t: string, horizon?: number) =>
    req<PropagationResponse>(`/api/propagation/${encodeURIComponent(id)}${qs({ t, horizon })}`),
  recommendations: (id: string, t: string) => req<Recommendation>(`/api/recommendations/${encodeURIComponent(id)}${qs({ t })}`),
  simulate: (body: { time: string; incident_segment?: string; capacity_reduction?: number; candidate_id?: string }) =>
    req<SimulationResponse>('/api/simulation/run', { method: 'POST', body: JSON.stringify(body) }),
  evaluation: <T = Record<string, unknown>>(kind: string) => req<T>(`/api/evaluation/${kind}`),
  candidates: () => req<{ candidate_id: string; target_segment: string; intervention_type: string; capacity_delta_vph: number;
    cost_index: number; feasibility_band: string }[]>('/api/candidates'),
  scenarios: () => req<{ note: string; scenarios: Scenario[] }>('/api/scenarios'),
}
