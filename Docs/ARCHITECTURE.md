# FlowSense AI — Architecture

```
                    backend/data/raw/*.csv   (organizer data, immutable, SHA-256 snapshotted)
                               │ scripts/ingest_data.py
                               ▼
                   processed/*.parquet ──► db/neurax.duckdb (17 tables, read-only at runtime)
                               │ app/data.py: load_traffic + sanitize()
                               ▼
      Panel: complete (436 segments × 5-min) grid, float32, + per-cell "valid" mask
      cached at data/features/panel_full.npz (scripts/build_features.py)
                               │
        ┌──────────────────────┼──────────────────────────────┐
        │ offline (train)       │ runtime (engine.Snapshot(t)) │ offline (evaluate)
        ▼                       ▼                              ▼
 train_models.py         window = panel[t-299 … t]      evaluate_models.py
  detector baseline      ├ detection.components         run_robustness.py
  threshold (train F1)   ├ score / state / confidence    (only place forecast_targets
  type centroids         ├ diagnose + evidence            is read, as labels)
  recurring bottlenecks  ├ forecasting.predict (12 LGBM)
  propagation calib.     ├ propagation.propagate
  12 forecasters         └ recommendations.recommend ─► simulation.scenario (pivot point,
        │                                               Frank-Wolfe on turn-aware line graph)
        ▼
 artifacts/models, metadata, metrics  ◄─────────────── api.py (FastAPI, Pydantic) ◄── frontend (React)
```

## Two portals, one backend

```
                FlowSense AI
                     |
          +----------+-----------+
          |                      |
   Operator Portal          User Portal (/user)
   (session cookie)         (public, rate limited)
          |                      |
          +------- Backend ------+
                     |
     +---------------+----------------+
     |               |                |
 Dataset         Routing          Places / search
 intelligence    provider         provider
                 (OSRM / ORS)     (Overpass / Nominatim)
```

The rider portal only calls `/api/user/*`. `user_api.py`, `user_traffic.py` and `providers.py` implement it; see `USER_PORTAL.md`.

## Backend modules (`backend/app/`)

| Module | Responsibility |
|---|---|
| `config.py` | Paths and every tunable constant, each tagged [DESIGN] / [INF] |
| `data.py` | Read-only DuckDB access. `sanitize()` handles shuffle, duplicates, negatives, impossible values, spikes, stuck runs, missing rows and limited forward-fill. Also the panel cache, quality score and active roadworks (`start ≤ t < end`) |
| `features.py` | 60 leakage-safe features: lags, trailing means, slopes, "same slot yesterday", neighbor state at t, time/context, static. `future()` is the single forward shift, used for labels only |
| `detection.py` | Baseline (segment × hour × day type), anomaly variants, epicentre rule, state thresholds, confidence, events, rule-based diagnosis, type likelihood, recurring-bottleneck detector, evidence objects |
| `forecasting.py` | 12 LightGBM models on Δ(target − now), residual bands by state bucket, schema check on load |
| `graph.py` | NetworkX node graph, neighbors, line-graph turn pairs (restrictions as transitions), legal detours, GeoJSON |
| `propagation.py` | Hop-decay × load model, calibration, indirect evaluation |
| `simulation.py` | BPR + Webster delay, Frank-Wolfe equilibrium, pivot-point scenarios, before/after comparison with tolerance |
| `recommendations.py` | Candidate search (direct → ≤2 hops → operational), feasibility checks, simulate + rank, advisory with reasons, evidence, confidence and limitations |
| `engine.py` | `Snapshot(t)`: everything known at t, LRU-cached |
| `evaluation.py`, `robustness.py` | Metrics and the synthetic corruption harness |
| `user_api.py`, `user_traffic.py`, `providers.py` | Rider portal: public router with limits, real-route x simulated-traffic logic, third-party adapters |
| `corridor.py`, `corridor_api.py`, `corridor_config.json` | Flyover candidate analysis: route -> ~300 m segments -> per-segment features -> congestion/bottleneck -> candidate zones -> start/end optimisation -> suitability/explanation/confidence. Reuses `data`, `engine`/`forecasting`, `providers` and `user_traffic`; traffic comes through the `TrafficDataProvider` seam. See `TRAFFIC_CORRIDOR_ANALYSIS.md` |
| `infrastructure.py`, `infrastructure_config.json` | AI infrastructure recommendation: compares six interventions for the corridor's bottleneck zone (BPR before/after on real zone features), scores them, recommends one, simulates impact. Land/infrastructure come from an `InfrastructureDataProvider` (built-in: SIMULATED). See `INFRASTRUCTURE_RECOMMENDATION.md` |
| `user_explain.py` | "Why is traffic heavy?": rider-language cause, evidence and forecast translated from the existing diagnosis (`/api/user/explain`); no second model |
| `api.py` | REST endpoints (see `API.md`) and warm-up of the demo snapshot at startup |

**Design choices:**
- Flat modules, one file each, rather than a folder per module.
- Data stays at `backend/data/` (the verified existing location).
- Runtime reads the sanitized panel cache instead of querying DuckDB on every request. Slicing it at t is causal and matches what a fresh sanitize of the window would produce.

## Frontend (`frontend/src/`)

- `hooks/useSim` keeps the simulation clock and selected segment in the URL (`?t=&seg=`), so pages, links and reloads stay consistent.
- `services/api.ts` is a typed fetch client. The base URL is its only config, and there are no secrets.
- `components/`: `TrafficMap` (Leaflet + pale OSM basemap; the 436-segment network is a single faint canvas layer drawn once, with only significant segments drawn above it in ordered panes: casings → problems → selected → incident markers; smooth camera, controlled zoom, synced comparison maps, tile-failure notice), `SegmentCard` (floating segment card), `MapLegend`, `ForecastChart`, `SegmentIntel` (drill-down drawer), and `ui` (panels, tags, evidence and KPI tables, loading and error states).
- Pages: Dashboard, Incidents, Forecast, Propagation, Interventions, Simulation, Evaluation.
- Colour carries meaning:
  - Traffic-state colours appear only for traffic state.
  - Violet appears only for propagation risk.
  - Teal and rust appear only for simulated better/worse.
  - Everything else is neutral.
- Mode tags keep the data categories apart: LIVE, BACKTEST, VALIDATION, BENCHMARK, SYNTHETIC and SIMULATED.

## Data flow per request

`GET /api/traffic/current?t=…` → `engine.get(t)`. This floors t to the grid, validates the range and slices the panel to ≤ t. It then scores the anomaly, predicts all segments × 12 models, and caches the snapshot. The first snapshot takes about 3 s (loading models and the panel); later ones take about 1 s.

`GET /api/recommendations/{seg}` → Snapshot, then a pivot-point reference assignment (cached), then one Frank-Wolfe run per feasible candidate (≤ 6). This takes about 10–20 s the first time and is cached afterwards.
