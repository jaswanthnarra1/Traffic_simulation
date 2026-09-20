# AI Infrastructure Recommendation

Operator page **Infrastructure** (`/flyover`, built on the [corridor analysis](TRAFFIC_CORRIDOR_ANALYSIS.md)). For any origin and destination in Hyderabad it finds the bottleneck zone, then **compares** six interventions instead of assuming a flyover: flyover / grade separator, road extension, road widening, junction redesign, signal optimization, traffic diversion. It ends with a recommendation, a land and construction feasibility card, a simulated before/after, and data confidence.

**Everything about land, infrastructure, cost and future impact is SIMULATION / DEMO DATA** and is labelled so on every card. It is a planning aid, not engineering approval; the required disclaimer is always visible.

## Flow

```
Select origin + destination (search or map)  ->  Run Corridor Analysis / Analyze Corridor
  phase 1  POST /api/corridor/analyze          route, segments, traffic, bottlenecks, candidate zone   (map draws the route)
  phase 2  POST /api/infrastructure/analyze    land evaluation, intervention comparison, recommendation (cards appear)
  then     POST /api/infrastructure/simulate   current vs simulated after, for the chosen intervention
```

UI states: no corridor ("Select a road corridor to begin infrastructure analysis.") / ready ("Ready to analyze.") / analysing (phase 1: corridor, traffic, bottlenecks; phase 2: land, interventions) / complete / simulation running / simulation complete / error (plain language, corridor results kept).

## What is real and what is simulated

| Item | Source |
|---|---|
| Route, road names, junctions | routing provider (real OSM) |
| Volume, speed, utilisation, delay, signals, lanes, incidents, growth | corridor engine: dataset (simulated traffic, matched approximately to the road) |
| Bottleneck zone, start/end, suitability | corridor engine (transparent weighted score) |
| Land availability, ROW, structures, utility and environmental constraints, construction difficulty | **`SimulationInfrastructureProvider`: SIMULATED**, deterministic per zone (seeded by the zone coordinates), so a corridor always shows the same values |
| Costs | unit costs x length in `infrastructure_config.json` (demo assumptions, not official estimates) |
| Impact of each intervention | **BPR volume-delay model** (the project's `BPR_ALPHA/BETA`) on the zone's measured utilisation and speed; the effect sizes below are demo assumptions |

Per-intervention effects (all in `backend/app/infrastructure_config.json`): capacity gain (flyover 35%, extension 28%, widening +1 lane = 1/lanes from the real lane count, junction 10%, signals 5%), signal delay removed (flyover 80%, junction 25%, signals 20%), demand shift (diversion 10%), land need, base construction feasibility, unit cost. Emissions change = delay reduction x an elasticity of 0.5 (assumption).

## Scoring (0-100, weights configurable)

`overall = 0.35 traffic impact + 0.20 land feasibility + 0.15 construction feasibility + 0.15 cost efficiency + 0.15 long-term impact`

- **Traffic impact**: relative reduction in speed-based congestion (`1 - speed / free-flow`) from the BPR before/after.
- **Land feasibility**: `100 - land_need x (private property impact + min(structures, 30))`, from the land provider.
- **Construction feasibility**: base score of the type minus difficulty x sensitivity, minus utility-conflict penalty.
- **Cost efficiency**: traffic impact per crore, relative to the best applicable option.
- **Long-term impact**: the same reduction with demand grown by 20% (planning horizon assumption).
- Interventions that do not apply are shown with a reason (no signals, no junction, no alternative route) and are not scored.
- Recommended = highest overall among applicable, if overall >= 45 and traffic impact >= 5; otherwise "No major infrastructure intervention indicated". When the zone's peak congestion is mild (< 20% below free-flow speed) a caution says gains are modest in absolute terms.

## Data confidence

Traffic (corridor data quality), road network (share of the route with real road names), land (65%, simulated), infrastructure (60%, simulated); overall is their weighted mean. It is not the probability that a project would succeed or be built.

## API (operator session required)

- `POST /api/infrastructure/analyze` `{origin:{lat,lng}, destination:{lat,lng}}` returns `mode:"SIMULATION"`, `corridor_id`, `corridor` (the full corridor analysis), `traffic` (zone metrics), `bottlenecks`, `land_evaluation`, `interventions[]` (`scores`, `simulated_impact{before,after,...}`, `estimated_cost_cr`, `applicable`), `recommendation{action, headline, summary, why[], candidate{start,end,length_km,estimated_cost_cr}, simulated_impact, caution}`, `flyover`, `candidate`, `confidence`, `labels`, `disclaimer`. 422 invalid input / insufficient traffic observations, 404 no route, 503 provider.
- `POST /api/infrastructure/simulate` `{corridor_id, intervention}` returns `label:"SIMULATED IMPACT"`, `current_vs_after`, `planning_horizon` (demand grown by the configured %), `method`, `disclaimer`. 404 before an analysis exists, 422 unknown or inapplicable intervention.

The UI renders these responses only: it contains no result numbers, so swapping the provider changes what is shown without UI changes.

## Replacing the simulated data with real GIS data

1. Implement `InfrastructureDataProvider` (`app/infrastructure.py`): `land(zone) -> dict` with the same keys (`land_availability_pct`, `government_land_pct`, `private_property_impact_pct`, `structures_affected`, `row_availability_pct`, `utility_conflict`, `environmental_constraint`, `construction_difficulty`, plus `data_status` and `label`), `confidence() -> {land, infrastructure}`, `name`, `simulated = False`. `zone` carries `length_km` and the start/end key; a real provider would query parcels, ROW and utility layers along the candidate geometry (`corridor` result `candidates[].coords`).
2. Register it in `PROVIDERS` and set `INFRASTRUCTURE_PROVIDER`. The labels and the "simulated" badges follow `provider.simulated` automatically, and a test proves the ranking reacts to the provider's land data.
3. Replace the unit costs and effect sizes in `infrastructure_config.json` (or `INFRASTRUCTURE_CONFIG_PATH`) with engineering values; for live traffic implement `TrafficDataProvider` (see the corridor doc).

## Limitations

- Traffic is the simulated dataset matched approximately to real roads; zones can be only mildly congested in absolute terms, in which case the recommendation says so.
- The intervention effects are assumptions of a macroscopic model, not microsimulation or engineering design. Cost, land and impact are demo figures.
- Results are cached in memory (last 50); simulate needs an analysis in the same server session.
- The card lives on the Infrastructure page (with the map), not in the Dashboard's right-hand panel.
