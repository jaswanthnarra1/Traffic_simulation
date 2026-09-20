# Traffic Corridor Analysis (Flyover Candidate Analysis)

Operator page **Flyover** (`/flyover`). Given any origin and destination in Hyderabad, FlowSense generates the real road corridor, analyses it segment by segment, finds bottleneck zones and proposes where a grade-separation study could start and end. It is a **screening aid**, not an engineering decision. The UI keeps three layers apart: *AI/data analysis* (this screen) / *engineering feasibility* (not assessed) / *official infrastructure decision* (not made).

Not hard-coded to any place: `Kukatpally → Miyapur`, `LB Nagar → Uppal`, `Ameerpet → SR Nagar` all go through the same code. Coverage depends on how much of the route the traffic dataset reaches (see Data honesty).

## Algorithm

```
Origin / Destination  (lat, lng; typed, searched or picked on the map)
      |  validate: numeric, inside the service area, 100 m .. 40 km apart
      v
Route            providers.route_options (OSRM demo by default, OpenRouteService with a key): real road geometry,
                 road names and junctions (intersections with 3+ approaches) from the provider's steps
      v
Segmentation     equal stretches of ~300 m along the geometry (config: segment_length_m, max 300 stretches)
      v
Matching         each stretch -> nearest simulated grid segment within 750 m, preferring the one that runs the same way.
                 No segment in reach => NO_DATA (excluded from every score, drawn dashed grey)
      v
Feature          per matched segment, precomputed once (scripts/build_corridor_features.py -> parquet):
extraction       peak-hour flow, capacity utilisation, speed reduction vs midday, delay per km, persistence (share of weekday
                 peak intervals below 70% of free-flow speed), growth (last 5 vs first 5 history days), Webster signal delay,
                 signal/junction density, historical incident records, throughput-ceiling z-score (independent detector)
      v
Normalisation    network-wide 5th..95th percentile -> 0..100 (utilisation is used as-is: it is already a physical ratio)
      v
Congestion       weighted mean (0..100): utilisation, speed reduction, delay, junction/signal, persistence, current state
                 (the FlowSense replay at the analysis time). Weights: corridor_config.json
      v
Bottleneck       weighted mean (0..100): utilisation, speed reduction, delay, junction/signal, persistence, throughput ceiling
                 LOW < 25 <= MODERATE < 50 <= HIGH < 75 <= CRITICAL   (configurable)
      v
Candidate zones  seeds = HIGH+ stretches; seeds separated by <= 1 stretch with data are merged; a no-data gap is never bridged
      v
Start/end        per zone choose the extent [a..b] (within 3 stretches of the zone, at most 2 km, at least 500 m) that maximises
optimisation     sum(bottleneck score - MODERATE threshold): it absorbs MODERATE neighbours and never LOW ones. A long
                 bottleneck is split into several candidates. A 150 m approach margin is added at each end and the start/end
                 coordinates are interpolated exactly on the route
      v
Suitability      weighted mean (0..100) over: traffic pressure, junction bottleneck, delay, capacity utilisation, historical
                 growth, accident records. Components with no data are dropped and the rest re-normalised.
                 feasibility, pedestrian and public-transport are `data_required` and shown as such
      v
Explanation,     reasons built from the actual feature values of the covered segments; alternatives with whether the data
alternatives,    supports each; dataset planning candidates on the matched segments; data confidence
confidence
      v
Visualisation    route coloured by bottleneck level, candidate overlay with S/E markers, junction dots, incident-record markers,
                 segment card, results panel
```

## Outputs per segment

`segment_id, start/end lat/lng, length_m, road_name, road_type, lane_count, free_flow_speed_kmh, junction_count, signal_count (estimated), signal_delay_s, traffic_volume, road_capacity, capacity_utilization_pct, average_speed, travel_time_min, delay_min, historical_congestion_pct, accident_count, historical_growth_pct, current (simulation replay + forecast trend), congestion_index, bottleneck_score/level, features, data_status, match_distance_m`. 

Fields the data cannot supply are `null` with a reason in `unavailable`, never a made-up number: `speed_limit`, `road_width`, `pedestrian_activity`, `public_transport_activity` are `data_required`; `road_name` and `junction_count` are `unavailable` when the routing provider gives no steps.

| Field | Source |
|---|---|
| road name, junctions | routing provider (real OSM data) |
| road type, lanes, free-flow speed, capacity, signals | matched simulated network (approximate) |
| volume, speed, delay, persistence, growth | historical peak hours of the simulated dataset (training period only) |
| current state, forecast trend | FlowSense replay + the existing LightGBM forecaster |
| accident_count | historical incident records on the matched segment (training period), not live reports |

## Models

- **Forecasting** (`POST /api/traffic/predict`): the existing FlowSense LightGBM quantile models (one per metric and horizon). Chosen over deep learning because the data is 436 segments x 15 training days: gradient-boosted trees on lag/seasonal features are more reliable than an LSTM at this size. Validation numbers are those in `EVALUATION_REPORT.md` / `forecast_metrics.json`. Predictions are for the simulated dataset, **not real-time**.
- **Congestion, bottleneck, suitability**: transparent weighted indicators, not trained models. There is no labelled dataset of flyover need, so they cannot be validated as "accuracy". What is measured: `scripts/build_corridor_features.py` benchmarks the bottleneck score against the organizer's `structural_bottleneck` flag and stores it in `artifacts/metrics/corridor_metrics.json` (currently ROC-AUC 0.71, 0.64 without the throughput-ceiling component, precision@16 = 0.06): **weak agreement**, so rankings are screening only. That flag marks peak-throughput ceilings, which is only one facet of a bottleneck.
- Weights and thresholds are design choices in `backend/app/corridor_config.json`. Override the file with `CORRIDOR_CONFIG_PATH`.

## Data confidence

`0..100` from configurable weights: geographic coverage (share of route length matched to data), history volume, freshness (replayed data scores low; a live provider scores 1), model validation (forecaster available), field completeness (13 required fields). It is a data-quality indicator, **not** a probability and **not** engineering certainty.

## Data honesty

- Traffic is the organizer's synthetic dataset placed on a 12x10 grid over central Hyderabad, not measurements of real streets. A real route is matched approximately. Outside the grid (about lat 17.29-17.47, lon 78.34-78.56) stretches have no data. The UI labels everything **Simulation data**.
- Stretches sharing a grid segment share its traffic figures, so candidate edges tend to follow grid-segment boundaries. Real sensor data per road would remove this.
- Historical incident records are not live incidents. No pedestrian or public-transport data exists, so bus priority cannot be assessed.
- A route already passing a flyover/underpass/bridge (by road name) is flagged on the candidate.

## API (operator session required)

`POST /api/corridor/analyze` `{origin:{lat,lng}, destination:{lat,lng}, time?}` returns `id, mode, data_notice, provider, corridor, segments, bottlenecks, candidates, recommended_analysis, confidence, stages, disclaimer`. Also `GET /api/corridor/{id}`, `/segments`, `/bottlenecks`, `/candidates`, `POST /api/flyover/analyze` (candidates only), `GET /api/flyover/model-info`, `POST /api/traffic/predict` `{segment_ids, time?}`. Results of the last 50 analyses are kept in memory (`GET` by id 404s after a restart: run the analysis again). Errors are `{"detail": "<sentence>"}`: 422 invalid input or too short/far, 404 no route, 401 no session, 503 routing or forecaster unavailable.

## Dataset format and import

The corridor analysis reads the project's existing tables (`network`, `nodes`, `traffic_*`, `signal_plans`, `incidents_*`, `planning_candidates`) through `app/data.py`. To bring a new dataset: `python backend/scripts/inspect_dataset.py file.csv` reports columns, missing values, duplicates and which columns look like coordinates, timestamps, road ids and traffic fields; add `--mapping mapping.json` (`{their_column: project_column}`) to check the mapping covers `segment_id, timestamp, flow_vph, speed_kmh`. Nothing is imported blindly. Then run the existing ingestion (`scripts/ingest_data.py`, `validate_data.py`) and `scripts/build_corridor_features.py`. A live feed plugs in through the `TrafficDataProvider` protocol in `corridor.py` (`segment_stats()` and `current()`), selected with `CORRIDOR_TRAFFIC_PROVIDER`.

## Storage

No database migration: DuckDB/Parquet stay as they are (there is no PostgreSQL/PostGIS in this project). Per-segment features are a parquet cache (`artifacts/metadata/corridor_segment_stats.parquet`); analyses live in memory. Moving to PostGIS would add tables `road_segments`, `traffic_observations`, `corridor_analyses`, `bottlenecks`, `infrastructure_candidates` and a spatial index for the matching step.

## Limitations and production upgrades

- Real per-road traffic (probe/sensor feed) instead of a synthetic grid; a live `TrafficDataProvider`.
- Real road attributes (width, speed limit, right of way, utilities) for a feasibility layer; pedestrian and transit data.
- Persist analyses (PostGIS), asynchronous jobs for long corridors, self-hosted routing (the OSRM demo server is not for production).
- A labelled history of grade-separation decisions would allow a trained, validated ranking model.
