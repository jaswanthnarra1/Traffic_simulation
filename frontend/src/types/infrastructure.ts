/** Infrastructure recommendation contracts (backend: app/infrastructure.py). Land and infrastructure values are SIMULATED and labelled so. */
import type { Corridor } from './corridor'

export interface ImpactState { speed_kmh: number; congestion_pct: number; delay_min: number }
export interface Impact {
  before: ImpactState; after: ImpactState; capacity_change_pct: number; speed_change_pct: number; congestion_change_pct: number
  congestion_change_pts: number; delay_reduction_min: number; emissions_change_pct: number; data_status: 'SIMULATED'
}
export interface Scores { traffic_impact: number; land_feasibility: number; construction_feasibility: number; cost_efficiency: number; long_term_impact: number; overall: number }
export interface Intervention {
  id: string; label: string; applicable: boolean; reason_not_applicable: string | null; scores: Scores | null
  simulated_impact: Impact | null; length_km: number | null; estimated_cost_cr: number | null
}
export interface Land {
  data_status: string; label: string; land_availability_pct: number; government_land_pct: number; private_property_impact_pct: number
  structures_affected: number; row_availability_pct: number; utility_conflict: string; environmental_constraint: string; construction_difficulty: number
}
export interface Recommendation {
  action: string; headline: string; summary: string; why: string[]; data_status: string; disclaimer: string; overall_score?: number; caution?: string | null
  simulated_impact?: { congestion_change_pct: number; congestion_change_pts: number; peak_travel_time_change_min: number; speed_change_pct: number; emissions_change_pct: number }
  candidate?: { start: [number, number]; end: [number, number]; start_label: string; end_label: string; length_km: number; estimated_cost_cr: number } | null
}
export interface InfraConfidence { overall: number; components: Record<'traffic' | 'road_network' | 'land' | 'infrastructure', number>; simulated: string[]; meaning: string }
export interface Infrastructure {
  mode: 'SIMULATION'; corridor_id: string; corridor: Corridor; disclaimer: string
  labels: { traffic: string; land: string; infrastructure: string; banner: string }
  traffic: { zone_congestion_pct?: number | null; zone_delay_min?: number; zone_avg_speed_kmh?: number; zone_volume_vph?: number; zone_capacity_utilization_pct?: number
    zone_incidents?: number; zone_length_km?: number; congestion_index: number | null; traffic_status: string }
  bottlenecks: { zones: { name: string; start_segment: string; end_segment: string; bottleneck_segments: string[]; length_km: number }[] }
  land_evaluation: Land | null; interventions: Intervention[]; recommendation: Recommendation; flyover?: Intervention | null
  candidate?: { name: string; start: [number, number]; end: [number, number]; estimated_length_m: number; affected_segments: string[]; suitability: { score: number | null } }
  confidence: InfraConfidence
}
export interface Simulation {
  mode: 'SIMULATION'; label: string; intervention: { id: string; label: string }; current_vs_after: Impact
  planning_horizon: Impact & { demand_growth_pct: number }; method: string; disclaimer: string
}
