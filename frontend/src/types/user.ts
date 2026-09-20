/** Rider-portal contracts (backend: app/user_api.py). Rider language only — no operator/model internals. */
export type Level = 'free_flow' | 'moderate' | 'heavy' | 'severe' | 'no_data'

export interface UserConfig {
  mode: 'simulation'
  traffic_source: string
  simulation: { start: string; step_minutes: number; steps: number; default_step: number }
  service_area: { south: number; west: number; north: number; east: number }
  center: { lat: number; lon: number }
  providers: { geocoding: string; routing: string; places: string }
  categories: { id: string; label: string }[]
}

export interface Zone {
  segment_id: string; traffic_level: Level; status: string; speed_kmh: number | null
  congestion: number | null; updated_at: string; path: [number, number][]
}
export interface UserTraffic { mode: 'simulation'; step: number; time: string; updated_at: string; counts: Record<Level, number>; zones: Zone[] }

export interface UserAlert {
  type: string; severity: string; title: string; message: string
  segment_id: string | null; lat: number | null; lon: number | null; confidence: string; route_id?: string
}

export interface GeoResult { id: string; label: string; sublabel: string | null; lat: number; lon: number; kind: string | null }

export interface Stretch { level: Level; length_m: number; coords: [number, number][] }
export interface RouteOption {
  id: string; default: boolean; distance_km: number; duration_min: number; free_flow_min: number; delay_min: number
  traffic_level: Level; traffic_label: string; expected_worsening_min: number; forecast: Record<string, number>
  simulation_coverage_pct: number; stretches: Stretch[]; worst: { lat: number; lon: number; level: Level } | null
  coords: [number, number][]; ranking: number; recommended: boolean; label: string
}
export interface Diversion {
  recommended: true; current_route_id: string; alternative_route_id: string; current_eta_min: number; alternative_eta_min: number
  estimated_saving_min: number; alternative_traffic_level: Level; confidence: 'Low' | 'Medium'; reason: string
}
export interface RouteResponse {
  mode: 'simulation'; step: number; time: string; routing_provider: string; traffic_note: string
  routes: RouteOption[]; diversion: Diversion | null; alerts: UserAlert[]
}

export interface ExplainTarget { lat: number; lon: number; segment_id?: string | null; open?: boolean }
export interface ForecastPoint { level: Level; label: string; confidence: 'Low' | 'Medium' | 'High' }
export interface Explanation {
  status: 'normal' | 'congestion' | 'disruption_detected' | 'unavailable'
  headline: string; summary: string; traffic_level: Level; updated_at: string
  cause: { label: string; certainty: 'reported' | 'likely' | 'possible' | 'unknown'; confidence: number | null; confidence_label: string | null; type: string | null; type_note: string | null } | null
  evidence: { signal: string; message: string }[]
  forecast: { now: Level; now_label: string; horizons: Record<string, ForecastPoint>; trend: 'worsening' | 'easing' | 'steady'; why?: string; spread?: string } | null
}

export interface Place {
  id: string; category: string; category_label: string; name: string | null; lat: number; lon: number
  address: string | null; distance_m: number; opening_hours: string | null; phone: string | null; website: string | null
}

/** A chosen location: where you are, a searched place, a dropped pin, or a nearby place. */
export interface Pt { label: string; lat: number; lon: number; source: 'gps' | 'search' | 'pin' | 'place' }
