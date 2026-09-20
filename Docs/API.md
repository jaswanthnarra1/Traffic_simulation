# FlowSense AI — REST API

Base: `http://localhost:8000`. Interactive docs are at `/docs` (OpenAPI).

Time-dependent routes take `?t=` as an ISO timestamp. It is floored to the 5-minute grid, must lie within 2026-01-01 00:00 → 2026-01-19 23:55, and defaults to `DEMO_TIME`.

Errors: 404 = unknown segment, candidate or scenario. 422 = invalid time or body. 503 = model or metrics artifacts missing.

There is no free-form SQL and no file paths. Table names come from a whitelist, and segment and candidate IDs are regex-validated in request bodies.

**Authentication:** every route except `/api/health` and `/api/auth/*` needs the `fs_session` cookie set by `POST /api/auth/login`. Without it the response is `401 {"detail": "Not authenticated"}`. With curl, use `-c/-b cookies.txt`.

| Method | Path | Returns |
|---|---|---|
| POST | `/api/auth/login` | Body `{login_id, password}`. `200` returns `{login_id, display_name, expires_at}` and sets an HttpOnly session cookie. `401` returns `"Invalid login ID or password."` (same for either field). `422` for malformed input, with submitted values never echoed |
| POST | `/api/auth/logout` | Clears the session cookie |
| GET | `/api/auth/me` | The current session user, or `401` |
| GET | `/api/health` | `{status, models_available, database}` |
| GET | `/api/meta` | Data periods, demo time, detector variant and threshold, state thresholds, horizons, mode labels |
| GET | `/api/network` | Segment GeoJSON (offset per direction), graph summary, nodes, bounds, synthetic-network disclaimer |
| GET | `/api/network/segments` | Static segment attributes (`structural_bottleneck` and its proxy `peak_capacity_factor` removed) |
| GET | `/api/traffic/current?t=` | `mode: LIVE_SIMULATION`, context, state counts, one row per segment: state, speed, flow, occupancy, travel/free-flow time, delay, queue, congestion, capacity utilization, speed ratio, anomaly score, anomalous, confidence, data quality, road class, signalized, recurring_bottleneck_detected |
| GET | `/api/traffic/segment/{id}?t=` | Static attributes, state row, **evidence** list, **diagnosis** (cause, reasons, onset, context, type likelihood, confidence) |
| GET | `/api/forecast/{id}?t=&mode=live\|backtest` | 2 h of history plus P10/P50/P90 at now, +15, +30, +45 and +60 for speed, flow and congestion. `backtest` adds `actual_future` (recorded values) and is labelled as such |
| GET | `/api/forecast-map?t=&horizon=15\|30\|45\|60` | Forecast traffic state of **every** segment at +horizon (P50 speed, speed ratio, congestion, state), for the map's forecast mode. A read-only view of the same numbers `/api/forecast/{id}` returns; `422` for any other horizon |
| GET | `/api/detection?t=` | Segments that are non-normal or anomalous, with cause and reasons |
| GET | `/api/incidents?t=` | Detected events (contiguous flagged runs) in the last 3 h. Never reads the labels |
| GET | `/api/propagation/{id}?t=&horizon=` | Neighbors with hop, direction, predicted drop, risk, impact level, ETA and reason. `horizon` (5–60) keeps ETA ≤ horizon |
| GET | `/api/recommendations/{id}?t=` | Advisory (recommendation, kind, reasons, confidence, evidence, simulated impact, limitations), ranked simulated candidates with feasibility checks and score components, unsimulated candidates, operational options (legal detour), search counts |
| GET | `/api/candidates` | All 90 planning candidates |
| POST | `/api/simulation/run` | Body `{time?, incident_segment? (R\d{4}), capacity_reduction (0–0.9), candidate_id? (PLAN\d{4})}`. The baseline is the current network (with the incident); the counterfactual adds the candidate, or clears the incident if no candidate is given. Returns KPIs (baseline, counterfactual, abs, %), segments improved/worsened, side effects, convergence and tolerance, per-segment before/after |
| GET | `/api/evaluation/{forecast\|detection\|bottleneck\|propagation\|simulation\|robustness}` | The stored metrics JSON |
| GET | `/api/scenarios` | The 30 organizer worked examples with demo times and a note that they are not independent validation |
| GET | `/api/scenarios/{id}` | A scenario plus the detector's state and diagnosis at its demo time |

Example:

```bash
curl -c cookies.txt -X POST localhost:8000/api/auth/login -H 'content-type: application/json'   -d '{"login_id":"tgpolice","password":"tgpolice"}'
curl -b cookies.txt "localhost:8000/api/recommendations/R0360?t=2026-01-16T13:30:00"
curl -b cookies.txt -X POST localhost:8000/api/simulation/run -H 'content-type: application/json' \
  -d '{"time":"2026-01-16 16:45:00","incident_segment":"R0384","capacity_reduction":0.5,"candidate_id":"PLAN0383"}'
```

## Rider (public) API: `/api/user/*`

No operator session required; nothing here exposes operator data. Errors are `{"detail": "<readable sentence>"}` (422 invalid input, 429 rate limited, 404 no route, 502/503/504 provider unavailable or slow). Details in `USER_PORTAL.md`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/user/config` | Mode, traffic-source label, replay clock, service area, provider names, place categories. No keys |
| GET | `/api/user/traffic?step=0..23` | `zones[]`: `segment_id, traffic_level, status, speed_kmh, congestion, updated_at, path`. Normal segments omitted |
| GET | `/api/user/alerts?step=` | `alerts[]`: `type, severity, title, message, segment_id, lat, lon, confidence` |
| GET | `/api/user/geocode?q=` | `results[]`: `id, label, sublabel, lat, lon, kind` (Nominatim, service area only) |
| POST | `/api/user/route-options` | Body `{origin:{lat,lon}, destination:{lat,lon}, step}` returns `routes[]` (ETA, distance, delay, traffic level, forecast, coverage, stretches, coords, ranking, label), `diversion`, `alerts` |
| POST | `/api/user/nearby` | Body `{lat, lon, categories[], radius_m 200..5000}` returns `places[]` (missing fields are `null`) |

### `POST /api/user/explain` (public, 40/min per IP)

Why is traffic heavy here? Body: `{"segment_id": "R0360", "step": 6}` or `{"lat": 17.426, "lon": 78.521, "step": 6}` (the most congested simulated segment within 750 m, else the nearest within 1.5 km).

Returns `status` (`normal` | `congestion` | `disruption_detected` | `unavailable`), `headline`, `summary`, `traffic_level`, `updated_at`, `cause` (`label`, `certainty` reported/likely/possible/unknown, `confidence` 0-1 or null, `confidence_label`, `type`, `type_note`), `evidence[]` (`signal`, `message`, `metric`, `observed`, `reference`, `change_pct`, `unit`, `segment_id`, `timestamp`) and `forecast` (`now`, `horizons` {`15m`,`30m`,`60m`: `level`, `label`, `confidence`}, `trend`, `why`, `spread`). No model internals. See `USER_PORTAL.md`.

### `POST /api/auth/logout`

Now also revokes the presented session token server-side (a copied cookie stops working), besides clearing the cookie.

## Corridor / flyover analysis (operator session required)

All routes below are protected like every non-public `/api/*` route (401 without a session). Full algorithm and field sources: `TRAFFIC_CORRIDOR_ANALYSIS.md`. Data is simulation data and the response says so (`mode: "simulation"`, `data_notice`).

| Method | Path | Notes |
|---|---|---|
| POST | `/api/corridor/analyze` | `{origin:{lat,lng}, destination:{lat,lng}, time?}` returns `id, mode, data_notice, provider, corridor{distance_km, estimated_travel_time_min, traffic_status, coverage_pct, coords, junctions}, segments[], bottlenecks{counts, segments}, candidates[], recommended_analysis, confidence{score, components, meaning, missing_fields}, stages[], disclaimer`. 422 invalid/outside area/<100 m/>40 km, 404 no route, 503 provider |
| GET | `/api/corridor/{id}` | The stored result (last 50 in memory; 404 afterwards) |
| GET | `/api/corridor/{id}/segments` | Segments + `field_sources` |
| GET | `/api/corridor/{id}/bottlenecks` | HIGH/CRITICAL segments with details |
| GET | `/api/corridor/{id}/candidates` | Candidates + disclaimer + confidence |
| POST | `/api/flyover/analyze` | Same body; candidates and recommended analysis only |
| GET | `/api/flyover/model-info` | Config, benchmark, forecaster description |
| POST | `/api/traffic/predict` | `{segment_ids[], time?}`: +15/+30/+60 min levels and confidence from the LightGBM forecaster, plus historical peak. Not real-time |

A candidate: `name, start[lat,lng], end[lat,lng], start_m, end_m, estimated_length_m, affected_segments, bottleneck_segments, reason_for_selection, existing_grade_separation, suitability{score, breakdown, weights_used, excluded, screening_level}, explanation[{factor,text}], alternatives{options[{name, supported_by_data(true|false|null), basis}], dataset_planning_candidates}`.

### Infrastructure recommendation (operator session required)

Simulation / demo data: land, cost and impact are simulated and labelled (`mode: "SIMULATION"`, `labels`, `disclaimer`). See `INFRASTRUCTURE_RECOMMENDATION.md`.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/infrastructure/analyze` | `{origin:{lat,lng}, destination:{lat,lng}}` returns `corridor_id, corridor` (full corridor analysis), `traffic` (zone metrics), `bottlenecks`, `land_evaluation` (SIMULATED), `interventions[]` (flyover, road_extension, road_widening, junction_redesign, signal_optimization, traffic_diversion: `applicable`, `reason_not_applicable`, `scores{traffic_impact, land_feasibility, construction_feasibility, cost_efficiency, long_term_impact, overall}`, `simulated_impact{before, after, congestion_change_pct/pts, speed_change_pct, delay_reduction_min, emissions_change_pct}`, `estimated_cost_cr`), `recommendation{action, headline, summary, why[], candidate, simulated_impact, caution}`, `flyover`, `candidate`, `confidence{overall, components, simulated}`, `labels`, `disclaimer` |
| POST | `/api/infrastructure/simulate` | `{corridor_id, intervention}` returns `label: "SIMULATED IMPACT"`, `current_vs_after`, `planning_horizon`, `method`, `disclaimer`. 404 no analysis yet, 422 unknown/inapplicable intervention |
