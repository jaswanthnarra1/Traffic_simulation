/** Corridor / flyover analysis contracts (backend: app/corridor.py). Fields the data cannot supply are null with an `unavailable` reason. */
export type Bn = 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL' | 'NO_DATA'
export interface CorridorSegment {
  index: number; segment_id: string; length_m: number; start_m: number; end_m: number; coords: [number, number][]
  road_name: string | null; junction_count: number | null; grid_segment_id: string | null; match_distance_m: number | null
  data_status: 'MATCHED' | 'NO_DATA'; unavailable: Record<string, string>
  road_type?: string; lane_count?: number; free_flow_speed_kmh?: number; signal_count?: number; signal_delay_s?: number
  traffic_volume?: number; road_capacity?: number; capacity_utilization_pct?: number; average_speed?: number; travel_time_min?: number
  delay_min?: number; historical_congestion_pct?: number | null; accident_count?: number; historical_growth_pct?: number | null
  congestion_index: number | null; bottleneck_score: number | null; bottleneck_level: Bn
  current?: { source: string; time: string; speed_kmh: number; state: string; delay_min: number; forecast: { trend: string } | null } | null
}
export interface Candidate {
  name: string; start: [number, number]; end: [number, number]; start_m: number; end_m: number; estimated_length_m: number
  affected_segments: string[]; bottleneck_segments: string[]; reason_for_selection: string; coords: [number, number][]
  existing_grade_separation: string[]
  suitability: { score: number | null; breakdown: Record<string, number | null>; excluded: Record<string, string>; screening_level: 'strong' | 'moderate' | 'weak' | null }
  explanation: { factor: string; text: string }[]
  alternatives: { options: { name: string; supported_by_data: boolean | null; basis: string; note: string | null }[]
    dataset_planning_candidates: { candidate_id: string; segment: string; type: string; capacity_delta_vph: number; cost_index: number; feasibility_band: string }[] }
}
export interface Corridor {
  id: string; mode: 'simulation'; data_notice: string
  provider: { traffic: string; live: boolean; as_of: string | null }
  corridor: { origin: { lat: number; lng: number }; destination: { lat: number; lng: number }; distance_km: number; free_flow_time_min: number
    estimated_travel_time_min: number; segments: number; junctions: [number, number][]; coords: [number, number][]; congestion_index: number | null
    traffic_status: Bn; coverage_pct: number; alternative_routes: number }
  segments: CorridorSegment[]
  bottlenecks: { counts: Record<Bn, number>; segments: string[] }
  candidates: Candidate[]; recommended_analysis: Candidate | null
  confidence: { score: number; components: Record<string, number>; meaning: string; missing_fields: string[] }
  stages: { stage: string; ms: number }[]
  disclaimer: { layers: string[]; text: string }
}
