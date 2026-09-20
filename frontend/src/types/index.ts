export type TrafficState = 'NORMAL' | 'MODERATE' | 'HEAVY' | 'CRITICAL' | 'NO_DATA'

export interface Evidence {
  metric: string; value: number | null; baseline: number | null; diff_pct: number | null
  threshold: number | null; unit: string; source: string; note: string | null
}

export interface SegmentRow {
  segment_id: string; state: TrafficState; speed_kmh: number | null; flow_vph: number | null
  occupancy_pct: number | null; travel_time_min: number | null; free_flow_time_min: number
  delay_min: number | null; queue_veh: number | null; congestion_index: number | null
  capacity_utilization: number | null; speed_ratio: number | null; anomaly_score: number
  anomalous: boolean; confidence: number; data_quality: number; road_class: string
  signalized: boolean; recurring_bottleneck_detected: boolean
}

export interface Context {
  temperature_c: number; rain_intensity: number; event_present: boolean; holiday: boolean; network_shift: number
}

export interface TrafficSnapshot {
  time: string; mode: string; in_training_period: boolean; context: Context
  summary: { states: Record<string, number>; anomalous_segments: number; mean_data_quality: number
    network_mean_speed_ratio: number; segments_without_data: number }
  segments: SegmentRow[]
}

export interface TypeLikelihood { type: string; confidence: string; likelihoods: Record<string, number>; validation_note?: string }

export interface Diagnosis {
  cause: string; reasons: string[]; onset_step: number; duration_min: number; run_start: string | null
  context: { rain_intensity: number; event_present: boolean; network_shift: number; roadwork_active: boolean }
  incident_type: TypeLikelihood | null; detected: boolean; confidence: number; confidence_label: string
}

export interface SegmentDetail {
  time: string
  segment: Record<string, string | number | null> & { segment_id: string }
  state: SegmentRow; evidence: Evidence[]; diagnosis: Diagnosis
}

export interface ForecastPoint { horizon_min: number; p50: number | null; p10: number | null; p90: number | null }
export interface ForecastSeries { history: { time: string; value: number | null }[]; forecast: ForecastPoint[] }
export type Metric = 'speed' | 'flow' | 'congestion'
export interface ForecastResponse {
  time: string; segment_id: string; mode: 'LIVE' | 'BACKTEST'; uncertainty: string
  forecast: Record<Metric, ForecastSeries>
  actual_future?: Record<Metric, { horizon_min: number; value: number | null }[]>
}

export interface DetectedEvent {
  segment_id: string; start: string; last_flagged: string; duration_min: number; ongoing: boolean
  peak_score: number; state: TrafficState | null; cause: string; confidence: number | null
  confidence_label: string | null; likely_type: string | null
}

export interface PropagationNeighbor {
  segment_id: string; hop: number; direction: 'upstream' | 'downstream'; predicted_speed_ratio_drop: number
  risk_score: number; impact_level: string; estimated_time_to_impact_min: number; reason: string
}
export interface PropagationResponse { time: string; source: string; source_drop: number; model: string; neighbors: PropagationNeighbor[] }

export interface Kpi { baseline: number; counterfactual: number; abs_change: number; pct_change: number | null }
export interface SegmentEffect {
  segment_id: string; time_before_min: number; time_after_min: number; flow_before: number; flow_after: number
  vc_before: number; vc_after: number; delta_time_min: number
}
export interface Convergence { baseline_gap: number; counterfactual_gap: number; tolerance_veh_h: number; network_change_within_tolerance: boolean }

export interface Candidate {
  candidate_id: string; intervention_type: string; target_segment: string; relation: string; hop: number
  capacity_delta_vph: number; cost_index: number; feasibility_band: string
  feasibility: { feasible: boolean; checks: { check: string; passed: boolean; detail?: string | null }[] }
  simulated: boolean
  impact?: { kpis: Record<string, Kpi>; segments_improved: number; segments_worsened: number
    worst_side_effects: SegmentEffect[]; convergence: Convergence; focus: SegmentEffect[] }
  local_delay_veh_h?: { before: number; after: number }
  ranking?: { score: number; components: Record<string, number>; weights: Record<string, number> }
}

export interface Recommendation {
  segment_id: string; time: string; state: TrafficState; diagnosis: Diagnosis; incident_capacity_reduction_assumed: number
  advisory: { recommendation: string; kind: 'none' | 'candidate' | 'operational'; reasons: string[]; confidence: string
    evidence: Evidence[]; limitations: string[]
    simulated_impact: { kpis: Record<string, Kpi>; local_delay_veh_h: { before: number; after: number }
      segments_improved: number; segments_worsened: number; label: string } | null }
  candidates_ranked: Candidate[]; candidates_not_simulated: Candidate[]
  operational_options: { action: string; path?: string[]; legal_turns_verified?: boolean; free_flow_time_min?: number
    direct_free_flow_time_min?: number; reason?: string }[]
  search: { direct_candidates: number; neighbor_candidates: number; max_hops: number; simulated: number }
}

export interface SimulationResponse {
  time: string; label: string; baseline_description: string; counterfactual_description: string
  kpis: Record<string, Kpi>; segments_improved: number; segments_worsened: number
  worst_side_effects: SegmentEffect[]; largest_improvements: SegmentEffect[]; convergence: Convergence
  focus: SegmentEffect[] | null; per_segment: SegmentEffect[]
}

export interface NetworkResponse {
  geojson: GeoJSON.FeatureCollection<GeoJSON.LineString, { segment_id: string; road_class: string; lanes: number; source_node: string; target_node: string }>
  nodes: { node_id: string; x: number; y: number; lat: number; lon: number }[]
  summary: Record<string, unknown>; bounds: [[number, number], [number, number]]; disclaimer: string
}

export interface Meta {
  product: string; data_range: [string, string]; train_period: [string, string]; validation_period: [string, string]
  demo_time: string; model_version: string; detector: { variant: string; threshold: number }
  state_thresholds: Record<string, number>; horizons_min: number[]; labels: Record<string, string>
}

export interface Scenario {
  scenario_id: string; target_segment: string; start_time: string; end_time: string; demo_time: string
  organizer_incident_type: string; organizer_severity: number; direct_candidate: boolean
}

export interface ForecastMap {
  time: string; mode: 'FORECAST'; horizon_min: number; source: string
  segments: { segment_id: string; speed_kmh: number | null; speed_ratio: number | null; congestion_index: number | null; state: TrafficState }[]
}
