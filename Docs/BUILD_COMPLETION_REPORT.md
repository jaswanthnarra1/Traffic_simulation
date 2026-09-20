# FlowSense AI — Build Completion Report

Build date: 2026-09-19. Measured numbers come from `backend/artifacts/metrics/`. Anything not verified is marked as such.

## 1. What was implemented
The complete pipeline, end to end on the organizer dataset: sanitize → detect → diagnose → forecast → propagate → candidate search → counterfactual simulation → before/after → evidence-based advisory → evaluation. It is exposed through a FastAPI backend and a 7-page React operations console. See `IMPLEMENTATION_PLAN.md` for the plan and the pre-build data findings that changed it.

## 2. Architecture
See `ARCHITECTURE.md`. It has flat backend modules (`backend/app/*.py`), a cached sanitized panel, LRU-cached snapshots per simulation time, and a URL-driven simulation clock in the UI.

## 3. Models
See `MODEL_CARD.md`.
- 12 LightGBM forecasters, with a persistence baseline.
- A statistical epicentre anomaly detector and a rule-based diagnosis, plus a nearest-centroid type likelihood (reported as not beating chance).
- An independent throughput-ceiling bottleneck detector.
- A calibrated hop-decay propagation model.
- A Frank-Wolfe BPR/Webster equilibrium with a pivot point.
- Weighted candidate ranking.

No GNN, LSTM, RL or LLM was used.

## 4. Datasets used
All 17 organizer CSVs through DuckDB:
- **traffic_*:** features and state.
- **forecast_targets_*:** evaluation labels only.
- **incidents_* / roadworks_*:** calibration and evaluation. Roadworks also serve as a runtime context flag, but only while active.
- **context_*:** features and diagnosis.
- **network, nodes, signal_plans, turn_restrictions:** the graph, signal delay, and legal turns.
- **od_demand_profiles:** simulation demand.
- **planning_candidates:** the intervention pool.
- **scenario_examples:** demo templates only.

## 5. Leakage protections (each enforced by a test)
| Rule | Enforcement | Test |
|---|---|---|
| forecast_targets never features | Only evaluation code reads them; no feature named `target_*` except target-hour | `test_forecast_targets_never_in_features` |
| No future traffic in features | Backward shifts only on a complete grid; runtime window ends at t | `test_future_traffic_cannot_enter_feature_windows`, `test_engine_window_ends_at_t` |
| No incident labels at runtime | Snapshot, diagnosis and events run with label reads made to fail | `test_detector_never_reads_incident_labels` |
| Roadworks only while active | `start ≤ t < end` | `test_roadworks_only_active_inside_window` |
| scenario_examples not validation | Not referenced by evaluation or training code | `test_scenario_examples_not_used_for_validation` |
| Organizer bottleneck flag not used by the detector | Neither `structural_bottleneck` nor its proxy `peak_capacity_factor` appears in detection code; both hidden by the API | `test_bottleneck_detector_does_not_read_organizer_flag`, `test_network_hides_organizer_bottleneck_label` |
| Raw data unchanged | SHA-256 snapshot | `test_raw_datasets_unchanged` |

## 6. APIs
16 REST endpoints. See `API.md`.

## 7. UI modules
- **Dashboard:** filters, scenarios, state map, intelligence panel, segment drawer, forecast and impact.
- **Incidents:** detected events and a 6-step timeline.
- **Forecast:** LIVE vs striped BACKTEST mode, bands, horizon table.
- **Propagation:** risk heatmap with a +15…+60 toggle.
- **Interventions:** advisory, ranked candidates with SIMULATE, legal diversion on the map.
- **Simulation:** side-by-side baseline/counterfactual maps, KPI table (absolute and %), side effects, tolerance note.
- **Evaluation:** Validation, Benchmark, Synthetic and Simulation categories kept separate.

The basemap is standard OpenStreetMap tiles (greyed), with a labelled synthetic "Simulation Network" overlay.

## 8. Simulation engine
- A static equilibrium on the turn-aware line graph, with BPR and Webster signal delay from `signal_plans`.
- Frank-Wolfe with line search.
- Demand scaled to the observed veh·km at t.
- The pivot point anchors scenarios on observed flows.
- Scenarios: baseline, incident (capacity multiplier), and intervention (capacity delta).

## 9. Evaluation metrics (validation unless stated)
- **Forecast:** beats persistence on all 12 targets. MAE improvement: flow +27…+36%, speed +3…+19%, congestion +5…+17%. P10–P90 coverage is 70–79% against a nominal 80%.
- **Detection:** slot F1 0.71 (P 0.56, R 0.96), 11/11 incidents, median delay 2 min, 12.5 false-positive slots per day. Train (in-sample) F1 is 0.81.
- **Type diagnosis:** 9% accuracy on validation. It does not beat chance and is documented and shown as weak.
- **Bottleneck benchmark:** precision 1.00, recall 0.125 at the pre-registered threshold, ranking AUC 0.968.
- **Propagation (indirect):** hit rate 91%, recall 53%, control base rate 19%.
- **Simulation (estimate):**
  - All routes legal.
  - Capacity loss raises simulated delay for 11/11 incidents.
  - The raw OD assignment correlates only 0.10–0.18 with observed flows, which is why scenarios are pivoted on observed flows.

## 10. Robustness results (synthetic)
With 5% injection on a validation slice, the sanitized pipeline gave:

| Noise | Detection F1 | Speed MAE +15 (km/h) |
|---|---|---|
| Clean | 0.923 | 0.543 |
| Missing | 0.923 | 0.549 |
| Duplicates | 0.923 | 0.543 |
| Spikes | 0.923 | 0.544 |
| Stuck | 0.923 | 0.543 |
| Negatives | 0.941 | 0.544 |
| Shuffle | 0.923 | 0.543 |
| Combined | 0.873 | 0.550 |

Without sanitizing, negatives and combined noise collapse detection F1 to 0.011 and 0.012, and flow MAE rises to 204–215 under spikes. Confidence falls with data quality (0.88 clean → 0.76 combined). See `ROBUSTNESS_REPORT.md`.

## 11. Tests passed
- **Backend:** 27/27 pytest (leakage ×7 rules, turn restrictions, legal detours and assigned routes, simulation validity, sanitizer, features, detector, API ×9).
- **Frontend:** 8/8 vitest; `tsc -b` and `vite build` succeed.
- **Manual browser check:** Dashboard, Interventions, Simulation, Propagation and Evaluation rendered against the live API with no console errors.
- **Not verified:** Docker. The files are provided, but Docker was not installed in the build environment.

## 12. Known limitations
See the README "Known limitations". Summary:
- Incident type is not identifiable.
- The bottleneck threshold is conservative.
- Spillback is one hop deep.
- Network-level simulated deltas are small and often within the solver tolerance; there is no intervention ground truth.
- Time-window turns are enforced at peak hours by assumption.
- Recommendations take about 10–20 s uncached.
- The public OSM tile server is for demo-scale use only.
- Labels appear incomplete [INF].

## 13. Environment variables
All optional: `DATA_DIR`, `NEURAX_DB_PATH`, `MODEL_DIR`, `CORS_ALLOWED_ORIGINS`, `DEMO_TIME`, plus the public `VITE_API_BASE_URL` and `VITE_TILE_URL`. **Zero API keys.** See `.env.example`.

## 14. Run locally
See README → Setup / Run. The short version: `pip install -r backend/requirements.txt`, run the 7 scripts in `backend/scripts`, start `uvicorn app.api:app` and `npm run dev`.

## 15. Deploy
- `docker compose up --build` (untested here). The backend mounts `backend/data` and `backend/artifacts`.
- Alternatively, run the backend on any Python host with those two folders and the frontend as a static build (`npm run build`, then serve `dist/`) with `VITE_API_BASE_URL` pointing at it. Set `CORS_ALLOWED_ORIGINS` accordingly.
- Hosting accounts are deployment credentials and never belong in the repo.

## 16. Hackathon demo flow
See `FINAL_DEMO_CHECKLIST.md`. The story is: incident on R0360 detected in 2 min → diagnosed as transient → forecast recovery → 1-hop spillback → neighbor-based candidates plus a legal diversion → R0384 where the direct candidate is *out-ranked* after simulation → evaluation evidence.

## Acceptance criteria

| Area | Status |
|---|---|
| Data: raw untouched, relationships validated, leakage-safe features | Done (tests) |
| Detection: congestion, anomaly, incident intelligence, recurring bottleneck | Done. Type likelihood weak by evidence; bottleneck recall low at the pre-registered threshold |
| Forecast: 4 horizons × 3 metrics, baseline comparison, uncertainty | Done |
| Graph: construction, neighbors, propagation, visualization | Done |
| Simulation: OD loaded, routing, turn restrictions, BPR, baseline, counterfactual, before/after | Done |
| Recommendations: direct, neighbor, feasibility, ranking, operational fallback | Done |
| Explainability: evidence trail, confidence, limitations, category labels | Done |
| Robustness: 6 noise types + combined, degradation report | Done |
| Frontend: basemap, overlay, state map, drilldown, incidents, forecast, propagation, interventions, simulation, evaluation | Done (manual browser check) |
| Engineering: API/frontend tests, reproducible training, docs, `.env.example`, no secrets, Docker documented | Done. Docker untested |
