# RESEARCH.md — NeuraX Smart Cities: Urban Traffic Flow & Incident Intelligence

Hackathon: NeuraX 3.0, Domain 1 — AI in Smart Cities. This document tracks the real, verified state of the project as it's built — what exists, what doesn't, and why each decision was made. See `skills/RESEARCH.md` for the unrelated audit of the vendored `skills/` reference repo (Anthropic's public Agent Skills examples, not part of this project's runtime).

## Project status (2026-09-19)

| Layer | Status |
|---|---|
| Data layer (ingest/validate/store) | **Built and verified** — this document's main subject |
| Backend/API, models, simulation, recommendations, frontend | **Built (FlowSense AI)** — see `BUILD_COMPLETION_REPORT.md`, `ARCHITECTURE.md`, `MODEL_CARD.md`, `EVALUATION_REPORT.md` |

Update (build phase): two findings post-date §4–§10 below. (1) `network.peak_capacity_factor` is 0.72 on exactly the 16 `structural_bottleneck` segments — a label proxy, excluded from the bottleneck detector. (2) Among arterials, flagged bottlenecks are *less* congested on average; the "~40% higher congestion" in §10 of the intelligence report is a road-class effect.

Approved architecture (from planning discussion): Python backend (FastAPI, when built) + React/Next.js frontend + hybrid classical ML (gradient boosting baselines) plus one graph-aware novelty component (spillback/propagation model), full build including a self-constructed robustness harness since the organizer's noise injection only appears in the hidden test set.

## 1. Dataset received

`NEURAX_SMART_CITIES_TRAINING_V2` (organizer-provided, from `C:\Users\Chinnu\Downloads\Telegram Desktop\`). 19 files: 17 CSVs + `DATASET_MANIFEST.json` + a source `README.md`. No archive/ZIP — files provided directly in a folder.

Manifest-declared scale (independently confirmed against every file, not trusted blindly): 436 segments, 120 nodes, 15 training days, 4 validation days, 8 hidden test days (36 hidden test scenarios we never see). Resolution: 5 minutes.

The manifest advertises noise (missing values, duplicates, spikes, stuck sensors, negative readings, row shuffle) as a dataset-wide feature — **verified false for train/validation**: both are perfectly clean (see §4). This noise almost certainly exists only in the hidden test set, which changes how robustness must be tested (self-constructed corruption of a held-out slice, not reliance on organizer noise that isn't present in what we have).

## 2. Dataset inventory (verified counts, not estimated)

| Dataset | Rows | Columns | Purpose | Relevant to |
|---|---|---|---|---|
| `traffic_train.csv` | 1,883,520 | 13 | Per-segment, per-5-min observed traffic state (speed, flow, occupancy, delay, queue, congestion_index) | Detection, forecasting model input |
| `traffic_validation.csv` | 502,272 | 13 | Same schema, held-out 4 days | Model validation |
| `forecast_targets_train.csv` | 366,240 | 14 | **Real ground-truth** speed/flow/congestion at +15/30/45/60min, sampled every 25 min (840 anchors × 436 segments) | Forecast model training labels — organizer explicitly warns not to use as input features |
| `forecast_targets_validation.csv` | 481,344 | 14 | Same, for validation | Local backtesting of forecast accuracy before any hidden-test submission |
| `network.csv` | 436 | 13 | Segment graph: source/target node, road_class, lanes, capacity, free_flow_speed, grade, **structural_bottleneck flag**, importance | Graph-aware spillback modeling; bottleneck prioritization |
| `nodes.csv` | 120 | 5 | Node id + x/y + real lat/lon (Hyderabad-area) | Map rendering, graph topology |
| `signal_plans.csv` | 89 | 5 | Signal cycle/green-ratio/offset per signalized node | Signal-retiming recommendation candidates |
| `turn_restrictions.csv` | 61 | 4 | No-turn / time-window restrictions per node | Realistic routing/diversion constraints |
| `od_demand_profiles.csv` | 1,500 | 5 | Origin-destination demand by purpose (commercial/school/etc) | Drives the before/after intervention simulation |
| `planning_candidates.csv` | 90 | 6 | Pre-defined interventions (capacity_upgrade, lane_addition, signal_retiming, turn_lane, connector) tied to specific segments, with cost_index/feasibility_band | **The answer-shape for infra recommendations** — hidden grading almost certainly checks against a `intervention_reference.csv` we don't have, built from this exact candidate list |
| `incidents_train.csv` | 49 | 7 | Labeled incidents: type, severity, lanes_blocked, time window | Incident-detection supervision |
| `incidents_validation.csv` | 11 | 7 | Same, held-out | Detection validation |
| `roadworks_train.csv` | 8 | 6 | Roadworks: segment, closure_fraction, work_type, time window | Confounder for detection (roadworks ≠ organic congestion) |
| `roadworks_validation.csv` | 3 | 6 | Same, held-out | — |
| `scenario_examples.csv` | 30 | 8 | Labeled incident scenarios explicitly instructing "evaluate baseline vs counterfactual using planning_candidates.csv" | **Template for the exact output format** the hidden 36 test scenarios will require |
| `context_train.csv` | 4,320 | 8 | temperature, rain_intensity, event_level/id, holiday_flag, day_of_week, hour, per 5-min | False-alarm suppression (rain/event ≠ incident) |
| `context_validation.csv` | 1,152 | 8 | Same, held-out | — |

No dataset was marked `UNKNOWN — REQUIRES CONFIRMATION`: every file's purpose was determined from its actual columns plus the problem statement's own evaluation rubric, not guessed.

## 3. Relationships (validated against real data, not assumed from column-name similarity)

All of the following were checked with actual joins (see `scripts/validate_datasets.py`, zero orphans found in every case):

- `segment_id` in `traffic_train/validation`, `forecast_targets_train/validation`, `incidents_*`, `roadworks_*` → exists in `network.segment_id`.
- `target_segment` in `planning_candidates.csv` → exists in `network.segment_id`.
- `source_node`/`target_node` in `network.csv` → exist in `nodes.node_id`.
- `node_id` in `signal_plans.csv` and `turn_restrictions.csv` → exists in `nodes.node_id`.
- `origin_node`/`destination_node` in `od_demand_profiles.csv` → exist in `nodes.node_id`.
- `signal_id` in `network.csv` (non-null) → exists in `signal_plans.signal_id`.

No relationship was assumed and left unchecked.

## 4. Data validation results

Every check below ran against the full dataset (not a sample) via `scripts/validate_datasets.py`. **Zero affected rows on every single check** — full detail in `backend/data/validated/validation_report.md`/`.json`:

- Referential integrity (all relationships in §3): 0 orphans.
- `nodes.lat/lon` within India bounding box: 0 out-of-range.
- `end_time >= start_time` on all incident/roadwork/scenario windows: 0 violations.
- Negative speed/flow/occupancy/queue values: 0.
- `occupancy_pct > 100`: 0.
- `speed_kmh` implausibly above 1.5× the segment's `free_flow_speed_kmh`: 0.
- Full-row duplicates across every file: 0.
- Missing values: only in expected nullable columns (`network.signal_id` for non-signalized segments, `context.event_id` when `event_level = 0`) — not a data-quality defect, a legitimate "no event" encoding.

**No rows were removed or transformed** — there was nothing to fix. This is documented rather than silently skipped because the organizer's manifest claims noise exists; independent verification shows it does not, in train/validation. See `backend/data/metadata/profiles.json` for the full per-column profile.

## 5. Storage architecture decision

**Choice: embedded DuckDB** (`backend/data/db/neurax.duckdb`), not Postgres/SQLite/a hosted DB.

Reasoning: the workload is entirely analytical (time-series aggregation, joins across a small road-network graph, no concurrent writers, no multi-user transactions) over CSVs already sized 40–160MB. DuckDB reads/writes Parquet and CSV natively without a server process, requires zero credentials, and is fast enough to query the full 1.88M-row `traffic_train` table interactively. Running a Postgres container for a hackathon demo would add deployment surface (Docker, connection strings, a service to keep alive) for no analytical benefit this workload needs. This is a judgment call within the previously-approved "Python backend" direction, not a new architectural direction — flagged here rather than decided silently.

Pipeline: `data/raw/*.csv` (byte-identical copies of the organizer files, checksum-verified) → `scripts/preprocess_datasets.py` (typed Parquet in `data/processed/`) → `scripts/import_datasets.py` (`CREATE OR REPLACE TABLE` per dataset into `neurax.duckdb`) → `scripts/verify_import.py` (source vs processed vs imported row-count and relationship parity).

## 6. Data → architecture mapping

| Dataset | Processing | Storage | DB table | API (planned) | ML pipeline role (planned) |
|---|---|---|---|---|---|
| `traffic_train/validation` | Timestamp cast to `TIMESTAMP` | Parquet + DuckDB | `traffic_train`, `traffic_validation` | `/traffic/current`, `/traffic/history` | Detection features, forecaster lag features |
| `forecast_targets_train/validation` | Timestamp cast | Parquet + DuckDB | `forecast_targets_train`, `forecast_targets_validation` | Not exposed directly (labels only) | Forecaster training/backtest labels — never a model input feature |
| `network.csv` | none needed | Parquet + DuckDB | `network` | `/network/segments` | Graph structure for spillback propagation; `structural_bottleneck` flag seeds bottleneck prioritization |
| `nodes.csv` | none needed | Parquet + DuckDB | `nodes` | `/network/nodes` | Map rendering; graph node set |
| `signal_plans.csv`, `turn_restrictions.csv` | none needed | Parquet + DuckDB | `signal_plans`, `turn_restrictions` | `/network/signals` | Signal-retiming candidate context; routing constraints |
| `od_demand_profiles.csv` | none needed | Parquet + DuckDB | `od_demand_profiles` | not directly exposed | BPR-style before/after intervention simulation input |
| `planning_candidates.csv` | none needed | Parquet + DuckDB | `planning_candidates` | `/recommendations/candidates` | Infra-recommendation engine's candidate pool (scored, not invented) |
| `incidents_*`, `roadworks_*` | Timestamp cast | Parquet + DuckDB | `incidents_train/validation`, `roadworks_train/validation` | `/incidents`, `/roadworks` | Incident-detection supervision + false-alarm confounders |
| `scenario_examples.csv` | Timestamp cast | Parquet + DuckDB | `scenario_examples` | `/scenarios` | Output-format template for the advisory/counterfactual generator |
| `context_train/validation` | Timestamp cast | Parquet + DuckDB | `context_train`, `context_validation` | `/context` | False-alarm suppression features (rain/event/holiday) |

No API or ML pipeline code exists yet — this table is the mapping that code will implement against, not a description of code already written. Flagged explicitly per the instruction not to claim connections that don't exist.

## 7. Reproducing the data layer

```
pip install -r backend/requirements.txt

python backend/scripts/inspect_datasets.py     # profiles data/raw -> data/metadata/{profiles.json,inventory.md}
python backend/scripts/validate_datasets.py    # validates data/raw -> data/validated/{validation_report.json,.md}
python backend/scripts/preprocess_datasets.py  # data/raw CSV -> data/processed Parquet (typed)
python backend/scripts/import_datasets.py      # data/processed Parquet -> data/db/neurax.duckdb (idempotent)
python backend/scripts/verify_import.py        # source vs processed vs imported parity + relationship checks
```

Every script is safe to re-run: `preprocess`/`import` overwrite their own outputs deterministically; running `import_datasets.py` twice was tested and produces identical row counts both times (no duplicate accumulation).

## 8. Adding a future dataset (e.g. the eventual hidden-test artifacts, if ever released for local use)

1. Drop the new file(s) into `backend/data/raw/` (never edit in place).
2. Add any new timestamp columns to `TIMESTAMP_COLUMNS` in `preprocess_datasets.py` if applicable.
3. Re-run the four pipeline scripts in order (§7) — each is additive/idempotent per file, so existing tables are untouched unless their own source file changed.
4. If the new dataset introduces a relationship to existing tables, add a check to `validate_datasets.py` rather than assuming it holds.

## 9. Storage/Git strategy

Combined `data/raw` + `data/processed` + `data/db` = ~507MB. `.gitignore` (root) excludes all three from version control; `data/metadata/` and `data/validated/` (small, human-readable reports, ~36KB combined) are tracked. No Git LFS or object storage needed yet at this scale — revisit if the eventual hidden-test data is materially larger.

## 10. Known limitations / remaining work

- No backend/API exists yet to actually serve these tables — §6's API column is a plan, not a fact.
- No ML pipeline exists yet — detection/forecasting/spillback/recommendation models are all unbuilt.
- Robustness testing against the manifest's claimed noise types cannot use organizer data (it isn't present in train/validation) — a synthetic corruption harness must be built separately against a held-out slice.
- `event_id`/`event_level` in `context_*.csv` reference specific named events (`EVT_101_01`, etc.) with no separate events lookup table provided — their semantic detail (what the event actually is) is not decodable beyond the level, confirmed by inspecting the file rather than assumed absent.
