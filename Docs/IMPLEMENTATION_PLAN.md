# FlowSense AI — Implementation Plan

AI Traffic Intelligence & Intervention Simulator. Built on the organizer dataset `NEURAX_SMART_CITIES_TRAINING_V2` (see `DATASET_INTELLIGENCE_REPORT.md`, `RESEARCH.md`). Tags: **[FACT]** verified in data, **[INF]** inference, **[DESIGN]** our choice, **[UNK]** unknown.

## 1. Pre-build findings that shape the design (verified during planning)

| Finding | Tag | Design consequence |
|---|---|---|
| `congestion_index ≈ 1 − speed/free_flow_speed` (incident rows: 0.5795 + 0.4205 = 1) | FACT (sampled) | Speed ratio is the primary state signal; congestion is not an independent detector input |
| During labeled incidents speed ratio falls to ≈0.58 / 0.42 / 0.28 by severity 1/2/3; flow drops; queue stays 0 | FACT | Detection is data-supported; incident *type* is not separable from traffic shape → type = low-confidence likelihood |
| `peak_capacity_factor = 0.72` on exactly the 16 `structural_bottleneck=1` segments, 1.0 elsewhere | FACT | Treated as a label proxy: **never** an input to the bottleneck detector |
| Bottleneck segments are *less* congested than other arterials on average (AUC of mean congestion = 0.30) | FACT | "Most congested = bottleneck" is wrong for this data |
| Peak/off-peak throughput amplification (p99 v/c peak ÷ off-peak) separates bottlenecks (AUC 0.97 among arterials) | FACT (exploratory, train only) | Independent detector = throughput-ceiling detector, threshold fixed by robust-z rule, not tuned on the flag |
| During incidents, 1-hop upstream **and** downstream neighbors rise from ci≈0.027 to ≈0.26 | FACT | Propagation in both directions, decay and lag calibrated on train incidents |
| Rain ≥0.3 lowers network speed ratio 0.98→0.89; events raise flow and congestion network-wide | FACT | Detector subtracts the network-wide shift at t (context effect ≠ incident) |
| Signals sit at the segment's **target** node (325/325) | FACT | Webster uniform delay applied to signalized links; retiming needs `signal_id` |
| Turn restrictions are all consistent `(from.target = node = to.source)`; `time_window` rows carry **no times** | FACT / UNK | [DESIGN] time_window enforced during peak hours only (documented assumption) |
| OD total demand 401,506 vph ≫ observed network flow | FACT | [DESIGN] single demand scale calibrated so assigned veh·km = observed veh·km at t |

## 2. Architecture

```
backend/data/raw (immutable CSV) ─► processed Parquet ─► neurax.duckdb   (existing, verified)
                                                     │
backend/app/  data.py        DuckDB access, sanitizer (dedupe/negatives/spikes/stuck/missing/shuffle), quality score
              features.py    leakage-safe (S×T array) feature builder — backward shifts only
              detection.py   baseline, anomaly score, status, diagnosis, type likelihood, events, bottleneck detector
              forecasting.py LightGBM ×12 (speed/flow/congestion × 15/30/45/60), residual bands, persistence baseline
              graph.py       segment graph, line graph with turn restrictions, neighbors, legal detours
              propagation.py explicit decay/lag spillback model (calibrated), indirect evaluation
              simulation.py  static BPR + Webster signal delay, MSA assignment on the turn-aware line graph
              recommendations.py candidate search (direct → neighbor → operational), feasibility, simulate & rank, advisories
              engine.py      snapshot at time t (cached): state + anomaly + forecast + evidence
              evaluation.py  all metrics; robustness.py corruption harness
              api.py         FastAPI + Pydantic
backend/scripts/  ingest_data, validate_data, build_features, build_network, train_models, evaluate_models, run_robustness
backend/artifacts/ models/ metrics/ metadata/
frontend/         React + TS + Vite + Tailwind + TanStack Query + Router + Recharts + React-Leaflet + Lucide
```

[DESIGN] Flat modules inside `backend/app/` rather than one folder per module — each module is one file; fewer files, same separation. Data stays at `backend/data/` (existing, verified location preserved).

## 3. Data flow & leakage controls

1. Raw CSV never written. `validate_data.py` records SHA-256 of every raw file; a test re-hashes them (TEST 7).
2. Traffic is loaded as a **complete (segment × 5-min) grid** after sanitizing, so a lag of k steps is exactly k×5 min. Features use only `lag(k≥0)` and trailing rolling windows. Labels use a separate `future()` function that the feature builder never calls.
3. `forecast_targets_*` are read **only** by `evaluation.py` (TEST 1 greps feature code and checks feature names).
4. Incident / roadwork labels: used for baseline cleaning (offline), threshold calibration, evaluation. Runtime detector input = traffic + context at ≤ t only. Roadworks shown as context only while `start ≤ t < end`.
5. `event_id` never used; `event_level > 0` used only as "event present".
6. `scenario_examples` only feeds the Scenarios demo list (TEST 4).
7. `structural_bottleneck` / `peak_capacity_factor` never feed the bottleneck detector; only the evaluation compares against them.

## 4. Model strategy

- **State**: speed ratio → NORMAL ≥0.85 / MODERATE ≥0.70 / HEAVY ≥0.50 / CRITICAL <0.50 [DESIGN].
- **Anomaly**: robust z of speed-ratio drop vs. segment×hour×daytype baseline (train, incident/roadwork windows excluded) minus network-wide shift, plus flow-drop z. Variants compared on train, threshold by train F1, reported on validation.
- **Diagnosis**: transparent rules → TRANSIENT_INCIDENT / RECURRING_BOTTLENECK / WEATHER_EVENT_EFFECT / ROADWORK_EFFECT / NORMAL_VARIATION / UNKNOWN. Type likelihood: nearest-centroid on train incident shape, UNKNOWN when weak.
- **Forecast**: persistence baseline first; LightGBM on Δ(target − now), train days 1–12, calibration days 13–15 (residual P10/P90), evaluation on `forecast_targets_validation`.
- **Propagation**: explicit hop decay × neighbor load, per-hop lag — both calibrated on train incidents. No GNN.
- **Simulation**: static user-equilibrium approximation (MSA, BPR α=0.15 β=4) on the turn-aware line graph.
- **Recommendation**: no learned recommender; simulate each feasible candidate, rank by configurable weights.

## 5. API

`/api/health, /api/meta, /api/network, /api/network/segments, /api/traffic/current, /api/traffic/segment/{id}, /api/forecast/{id}, /api/detection, /api/incidents, /api/propagation/{id}, /api/recommendations/{id}, POST /api/simulation/run, /api/evaluation/{forecast|detection|robustness|bottleneck|propagation|simulation}, /api/scenarios, /api/scenarios/{id}`. All time-dependent routes take `?t=` (ISO, floored to 5-min grid, validated against data range). Full reference in `API.md`.

## 6. Frontend

Pages: Dashboard, Incidents, Forecast, Propagation, Interventions, Simulation, Evaluation. Leaflet + OSM basemap with a labeled "Simulation Network" overlay (the grid is synthetic, not real Hyderabad roads). Global simulation clock in the top bar; LIVE mode never shows future actuals; Forecast page has an explicit BACKTEST toggle.

## 7. Testing

pytest: raw immutability, forecast_targets never in features, future-perturbation invariance of features, detector ignores incident labels, scenario_examples not used in evaluation, turn restrictions block illegal transitions, simulation validity (conservation, finite times, connectivity), sanitizer, API endpoints. Frontend: vitest on formatting/state logic. Smoke: full-stack API run.

## 8. Deployment

`docker-compose.yml` (backend uvicorn + frontend nginx). Local: `uvicorn app.api:app` + `npm run dev`. No API keys required.

## 9. Environment variables

`APP_ENV`, `DATA_DIR`, `MODEL_DIR`, `CORS_ALLOWED_ORIGINS`, `VITE_API_BASE_URL` — all optional with working defaults. No LLM is implemented (explanations are deterministic templates over computed evidence), so no LLM variables are declared.

## 10. Acceptance criteria

The checklist in the build brief §52; status recorded in `BUILD_COMPLETION_REPORT.md` with measured numbers from `backend/artifacts/metrics/`.
