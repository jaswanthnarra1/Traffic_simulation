# NEURAX SMART CITIES — DATASET INTELLIGENCE REPORT

Scope: `NEURAX_SMART_CITIES_TRAINING_V2` only. No software, API, or model was built to produce this report — every number below comes from read-only SQL queries (DuckDB) against the untouched dataset, run and captured during this audit (`backend/scripts/dataset_intelligence_analysis.py`, output in `backend/data/metadata/dataset_intelligence.json`). Facts are distinguished from inference throughout; anything not explicitly defined by `DATASET_MANIFEST.json` or the source `README.md` is labeled **"Meaning inferred from values/relationships."**

---

## 1. Executive Summary

This is a clean, internally consistent, synthetic dataset describing a 436-segment / 120-node bidirectional grid road network (Hyderabad-area coordinates) over 15 training days + 4 validation days at 5-minute resolution, plus network topology, signals, turn restrictions, OD demand, 90 pre-defined infrastructure candidates, incident/roadwork logs, and 30 worked incident scenarios. Zero data-quality defects were found in train/validation (no nulls beyond legitimately-nullable fields, no negatives, no duplicates, no referential-integrity violations, no timestamp disorder) — contradicting the manifest's claim that noise (missing values, spikes, stuck sensors, negatives, row shuffle) is present; that noise is therefore either exclusive to the hidden 8-day test set, or an unrealized manifest aspiration. **Critically verified**: `forecast_targets_*.csv` values are byte-for-byte identical to the actual future `traffic_*.csv` observation at the matching segment/horizon (0.0 mean and max absolute difference across speed, flow, and congestion, at all four horizons, in both train and validation — 100% of 366,240 + 481,344 rows checked). This is the dataset's single most important structural fact and the primary leakage vector to design around. The dataset strongly supports statistical/gradient-boosting forecasting and detection, lightweight grid-based propagation (not a GNN — the graph is a plain bidirectional grid with degree ∈ {2,3,4}), and a defensible before/after intervention comparison using `od_demand_profiles.csv` against `planning_candidates.csv`. It does **not** support deep incident classification (49 train / 11 validation labeled incidents — too few for a data-hungry classifier), nor any claim about hidden-test noise robustness without a self-built corruption harness, since that noise isn't observable in what we have.

## 2. Complete File Inventory

| File | Rows | Columns | Time Range | Primary Key | Purpose | Role |
|---|---|---|---|---|---|---|
| `traffic_train.csv` | 1,883,520 | 13 | 2026-01-01 00:00 → 2026-01-15 23:55 (5-min) | (timestamp, segment_id) | Observed per-segment traffic state | TRAINING DATA / RAW INPUT |
| `traffic_validation.csv` | 502,272 | 13 | 2026-01-16 00:00 → 2026-01-19 23:55 (5-min) | (timestamp, segment_id) | Same, held out | VALIDATION DATA / RAW INPUT |
| `forecast_targets_train.csv` | 366,240 | 14 | 840 anchors, ~every 25 min, within training period | (timestamp, segment_id) | Future speed/flow/congestion at +15/30/45/60m | FORECAST LABEL / GROUND TRUTH |
| `forecast_targets_validation.csv` | 481,344 | 14 | 1,104 anchors, ~every 5 min, within validation period | (timestamp, segment_id) | Same, held out | FORECAST LABEL / GROUND TRUTH |
| `network.csv` | 436 | 13 | n/a (static) | segment_id | Directed road-segment graph edges | NETWORK STRUCTURE |
| `nodes.csv` | 120 | 5 | n/a (static) | node_id | Intersection/node coordinates | NETWORK STRUCTURE |
| `signal_plans.csv` | 89 | 5 | n/a (static) | signal_id | Signal timing per signalized node | CONSTRAINT |
| `turn_restrictions.csv` | 61 | 4 | n/a (static) | none (composite: node_id+from_segment+to_segment) | Turn bans/time-window restrictions | CONSTRAINT |
| `od_demand_profiles.csv` | 1,500 | 5 | n/a (static baseline) | od_id | Origin-destination baseline demand | DEMAND |
| `planning_candidates.csv` | 90 | 6 | n/a (static) | candidate_id | Pre-defined infrastructure interventions | PLANNING |
| `incidents_train.csv` | 49 | 7 | 2026-01-01 → 2026-01-15 (within training period, exact min/max not separately queried beyond confirming inside range) | incident_id | Labeled training incidents | GROUND TRUTH |
| `incidents_validation.csv` | 11 | 7 | 2026-01-16 → 2026-01-19 | incident_id | Labeled validation incidents | GROUND TRUTH |
| `roadworks_train.csv` | 8 | 6 | 2026-01-02 → 2026-01-12 | work_id | Scheduled roadworks, training period | CONTEXT / CONSTRAINT |
| `roadworks_validation.csv` | 3 | 6 | 2026-01-16 → 2026-01-19 | work_id | Same, validation period | CONTEXT / CONSTRAINT |
| `scenario_examples.csv` | 30 | 8 | subset of training period | scenario_id | Worked incident→recommendation examples | SCENARIO |
| `context_train.csv` | 4,320 | 8 | 2026-01-01 00:00 → 2026-01-15 23:55 (5-min) | timestamp | Weather/event/calendar context | CONTEXT |
| `context_validation.csv` | 1,152 | 8 | 2026-01-16 00:00 → 2026-01-19 23:55 (5-min) | timestamp | Same, held out | CONTEXT |
| `DATASET_MANIFEST.json` | n/a | n/a | n/a | n/a | Declares scale/noise/hidden-test claims | DOCUMENTATION |
| `README.md` (dataset) | n/a | n/a | n/a | n/a | Usage instructions, leakage warning | DOCUMENTATION |
| *(not provided)* `scenario_ground_truth.csv`, `intervention_reference.csv` | — | — | — | — | Declared in manifest as existing, withheld | HIDDEN/EVALUATION REFERENCE |

No filename was assumed — every row above reflects an actual `DESCRIBE`/`count(*)` query against the file.

## 3. Dataset-by-Dataset Analysis

**`traffic_train.csv` / `traffic_validation.csv`** — 13 columns: `timestamp, segment_id, source_node, target_node, speed_kmh, flow_vph, occupancy_pct, travel_time_min, free_flow_time_min, delay_min, queue_length_veh, congestion_index, sensor_quality`. `sensor_quality` is constant `1.0` across all 2,385,792 combined rows (zero variance) — the field exists in the schema but carries no information in train/validation; it may vary in the hidden test (where "stuck sensors" is claimed). Every segment (436) appears at every timestamp with zero completeness violations in both splits — the panel is fully balanced, no missing (timestamp, segment) pairs.

**`forecast_targets_train.csv` / `forecast_targets_validation.csv`** — see §7, the most consequential finding in this report.

**`network.csv`** — 436 directed edges over 120 nodes. `x`/`y` in `nodes.csv` range exactly 0–11 and 0–9 (12×10 = 120) — this is a **regular grid graph**, not an organic street network; see §9.

**`nodes.csv`** — `lat` 17.300–17.462, `lon` 78.350–78.548, a real Hyderabad-area bounding box, linearly mapped from the `x`/`y` grid (confirmed: 10 distinct lat values for 10 `y` values, 12 distinct lon values for 12 `x` values).

**`signal_plans.csv`** — 89 rows, one per signalized node; all 89 are referenced by `network.signal_id` (0 unused).

**`turn_restrictions.csv`** — 61 rows, 3 restriction types (`no_turn` 18, `no_left` 20, `time_window` 23); all `from_segment`/`to_segment`/`node_id` references resolve (0 orphans).

**`od_demand_profiles.csv`** — 1,500 OD pairs, all 120 nodes appear as both origin and destination somewhere, 0 self-pairs (origin=destination never occurs), total baseline demand 401,506 vehicles/hour across the network.

**`planning_candidates.csv`** — 90 candidates, **exactly one candidate per target segment** (90 distinct `target_segment` values, 0 segments with more than one candidate) — there is no "pick the best of several options for this segment" scenario in the provided data; the choice space is one option per segment or none.

**`incidents_train.csv` / `incidents_validation.csv`** — see §8.

**`roadworks_train.csv` / `roadworks_validation.csv`** — 8 and 3 rows respectively; too few for statistical modeling, useful mainly as a confounder-awareness check (see §17) and as demonstration data for "don't confuse roadwork-induced slowdown with an incident."

**`scenario_examples.csv`** — see §15.

**`context_train.csv` / `context_validation.csv`** — see §11.

## 4. Column Dictionary

Only columns whose meaning is not explicit from name+values are annotated as inferred; the rest are self-evident from name/units. Full per-column null/range/distinct data lives in `backend/data/metadata/dataset_intelligence.json` — the tables below summarize what matters for modeling decisions.

### traffic_train.csv / traffic_validation.csv (identical schema)

| Column | Type | Null % | Range (train) | Meaning | Derived? | Feature? | Leakage Risk |
|---|---|---|---|---|---|---|---|
| timestamp | TIMESTAMP | 0 | 2026-01-01→01-15 | Observation time | Raw | Yes (as time features) | None |
| segment_id | VARCHAR | 0 | 436 distinct | Road segment key | Raw | Yes (as categorical/join key) | None |
| source_node/target_node | VARCHAR | 0 | 120 distinct each | Segment endpoints | Raw (denormalized from network.csv) | Yes, via join | None |
| speed_kmh | DOUBLE | 0 | 8.38–60.0, mean 43.5 | Observed speed | Raw | Yes | **High if taken from a timestamp ≥ prediction time** |
| flow_vph | DOUBLE | 0 | 0–4502.2, mean 791.4 | Observed flow | Raw | Yes | Same as above |
| occupancy_pct | DOUBLE | 0 | 7.0–98.0, mean 32.8 | Sensor occupancy | Raw | Yes | Same as above |
| travel_time_min | DOUBLE | 0 | 0.774–10.842 | Observed travel time | Derived from speed+length (inferred — not documented, but travel_time ≈ length/speed matches the free_flow_time/free_flow_speed ratio pattern) | Yes | Same as above |
| free_flow_time_min | DOUBLE | 0 | 0.774–3.582, only 392 distinct values (≈ per-segment constant) | Segment's free-flow travel time | Derived (static per segment: length_km/free_flow_speed_kmh) | Rarely — it's a network constant, not a traffic signal | None (static) |
| delay_min | DOUBLE | 0 | 0–7.808, mean 0.05 | travel_time − free_flow_time (inferred) | Derived | Yes | Same as speed/flow |
| queue_length_veh | DOUBLE | 0 | 0–1603.3, median 0.0 (heavily right-skewed) | Estimated queued vehicles | Raw/simulated | Yes | Same as speed/flow |
| congestion_index | DOUBLE | 0 | 0–0.7206, median 0.0085 | Composite congestion score (formula not documented — **meaning inferred from values/relationships**: correlates with delay/queue, bounded [0,1]) | Derived | Yes | Same as speed/flow |
| sensor_quality | DOUBLE | 0 | constant 1.0 | Sensor reliability flag | Raw | No — zero variance in train/val | None currently; **watch this column in hidden test** — it's the manifest's declared home for "stuck sensor"/quality noise |

### forecast_targets_train.csv / forecast_targets_validation.csv

| Column | Type | Meaning | Feature? | Leakage Risk |
|---|---|---|---|---|
| timestamp | TIMESTAMP | Anchor time (the "now" the forecast is issued from) | Yes, to align with traffic history | None |
| segment_id | VARCHAR | Segment key | Yes | None |
| target_speed/flow/congestion_{15,30,45,60}m | DOUBLE | **Verified identical (§7) to the actual future traffic_*.csv row** at anchor+horizon | **NEVER** — organizer explicitly states these are labels, and this audit proves they are literal future ground truth | **MAXIMUM. This is the dataset's central leakage vector.** |

### network.csv

| Column | Type | Null % | Range | Meaning | Feature? | Leakage Risk |
|---|---|---|---|---|---|---|
| segment_id | VARCHAR | 0 | 436 unique | Edge key | Join key | None |
| source_node/target_node | VARCHAR | 0 | 120 each | Directed edge endpoints | Yes (graph structure) | None |
| road_class | VARCHAR | 0 | {arterial 182, collector 254} | Road classification | Yes | None |
| lanes | BIGINT | 0 | 1–3 | Lane count | Yes | None |
| free_flow_speed_kmh | DOUBLE | 0 | {30,60,...} 4 distinct | Speed limit / uncongested speed | Yes (static) | None |
| capacity_vph | DOUBLE | 0 | 900–3105, 6 distinct | Segment capacity | Yes (static) | None |
| length_km | DOUBLE | 0 | 0.751–1.791 | Segment length | Yes (static) | None |
| grade_pct | DOUBLE | 0 | −3.96–4.0 | Road gradient — **meaning inferred**, not explained in docs beyond the name | Possibly (minor) | None |
| signal_id | VARCHAR | 25.46% (111/436) | 89 distinct | FK to signal_plans; null = unsignalized | Yes (presence flag) | None |
| structural_bottleneck | BIGINT | 0 | {0: 420, 1: 16} | **Organizer-provided ground-truth flag for chronic bottleneck segments** | Yes — but see leakage note below | **Medium**: this is a label the organizer supplies for us to *find independently*; using it directly as a detector output would be "answer key as feature," though using it to validate/calibrate a detector we build is legitimate and arguably intended |
| importance | DOUBLE | 0 | 0.563–1.435, all 436 distinct | Network centrality/importance score — **meaning inferred** (continuous, no two segments share a value, looks like a computed centrality metric) | Yes | None |
| peak_capacity_factor | DOUBLE | 0 | {0.72, 1.0} | Peak-hour capacity derating | Yes (static) | None |

### nodes.csv

| Column | Type | Meaning | Feature? |
|---|---|---|---|
| node_id | VARCHAR | Node key | Join key |
| x, y | BIGINT | Grid coordinates (0–11, 0–9) — **confirmed synthetic grid layout**, not organic GPS-derived | Graph structure |
| lat, lon | DOUBLE | Real-world coordinates, linear function of x/y | Map rendering |

### incidents_*.csv / roadworks_*.csv / scenario_examples.csv / context_*.csv / signal_plans.csv / turn_restrictions.csv / od_demand_profiles.csv / planning_candidates.csv

Full column-level stats for these are in `dataset_intelligence.json`; the values that matter for design decisions are called out in their dedicated sections below (§8–§14) rather than repeated here, to avoid restating the same numbers twice.

One column worth flagging explicitly: **`context.event_id`/`event_level`** — train uses levels `{0,1,3}` with IDs `EVT_101_01`/`EVT_101_02`; validation uses levels `{0,2}` with IDs `EVT_202_01`/`EVT_202_02`. **The two splits never share an event level or event ID.** No lookup table defines what these levels/IDs mean beyond the number itself — this is a genuine documentation gap, not an inference on our part.

## 5. Dataset Relationship Map

```
TRAFFIC (traffic_train/validation)
   │ segment_id
   ▼
SEGMENT (network.csv, PK segment_id)
   │ source_node, target_node
   ▼
NODE (nodes.csv, PK node_id)
   │ node_id
   ▼
SIGNAL_PLANS (signal_id)  ──constrains──▶  intersections in NETWORK
NODE ◀── origin_node/destination_node ── OD_DEMAND_PROFILES
SEGMENT ◀── from_segment/to_segment/node_id ── TURN_RESTRICTIONS
SEGMENT ◀── segment_id ── INCIDENTS_train/validation
SEGMENT ◀── segment_id ── ROADWORKS_train/validation
SEGMENT ◀── target_segment ── PLANNING_CANDIDATES
SEGMENT ◀── target_segment ── SCENARIO_EXAMPLES ── (30/30 exactly match an INCIDENTS_train row)
TIMESTAMP (traffic/context) ──drives──▶ FORECAST_TARGETS (= future TRAFFIC, verified §7)
CONTEXT (weather/event/holiday) ── aligned 1:1 by timestamp with TRAFFIC
```

Every arrow above was validated with an actual join (0 orphans in every case — see `backend/data/validated/validation_report.md` and the relationship checks in this audit), not assumed from column-name similarity.

**Combined picture**: TRAFFIC + CONTEXT + ROADWORKS + INCIDENTS all key off (segment_id, timestamp) and can be joined into one time-indexed panel per segment. NETWORK + NODES + SIGNALS + TURN_RESTRICTIONS form the static graph/constraint layer. OD_DEMAND + PLANNING_CANDIDATES form the simulation layer (not time-indexed — baseline demand and static candidates). SCENARIOS is a bridge artifact that packages one INCIDENTS_train row together with an instruction to consult PLANNING_CANDIDATES — it is the organizer's worked example of how the other layers should be combined into one deliverable.

## 6. Time-Series Structure

- **Resolution**: exactly 300 seconds (5 minutes), calculated from consecutive-timestamp gaps, not assumed. Verified for `traffic_train`, `traffic_validation`, `context_train`, `context_validation` — 100% of gaps are 300s in all four files (top_gaps_seconds shows a single value with count = n−1 in every case).
- **Training period**: 2026-01-01 00:00:00 → 2026-01-15 23:55:00 (15 days × 288 slots/day = 4,320 timestamps — exact match).
- **Validation period**: 2026-01-16 00:00:00 → 2026-01-19 23:55:00 (4 days × 288 = 1,152 timestamps — exact match).
- **Hidden test period**: not present in this package; manifest declares 8 days, 1,004,544 observation rows (= 8×288×436, consistent arithmetic, but **not independently verifiable** since we don't have the file).
- **Segment completeness**: 0 violations — every one of 436 segments appears at every one of 4,320 (train) / 1,152 (validation) timestamps. No missing (segment, timestamp) pairs in what we have.
- **Forecast-target anchor density — a real, previously-unremarked asymmetry**: training anchors are sparse (840 anchors, dominant gap 1,500s = 25 minutes, 19.4% of all training timestamps); validation anchors are dense (1,104 anchors, dominant gap 300s = 5 minutes, **95.8%** of all validation timestamps). Both show a small number of larger gaps (14 in train, 3 in validation) at day boundaries (15 and 4 days respectively, consistent with one boundary gap per day transition). **This means validation forecast evaluation is near-continuous while training forecast supervision is sparse — a local backtest against `forecast_targets_validation.csv` is a much closer proxy to how the hidden test likely evaluates forecasts than the training file's density suggests.**
- **Context/incident/roadwork timestamps**: `context_*` shares the exact 5-minute grid with `traffic_*`. `incidents_*`/`roadworks_*`/`scenario_examples` timestamps are arbitrary (not grid-aligned) start/end times — they mark real-world event windows, not sensor readings, and must be resampled/aligned onto the 5-minute grid before use as model features.

## 7. Forecast Target Verification (CRITICAL)

Mathematically verified, not assumed: for **every** row in both `forecast_targets_train.csv` (366,240 rows) and `forecast_targets_validation.csv` (481,344 rows), and for **every** horizon (+15/+30/+45/+60 minutes) and **every** metric (speed, flow, congestion):

```
target_<metric>_<h>m(timestamp=t, segment=s)  ==  traffic_<split>.<metric>(timestamp=t+h, segment=s)
```

Result: **100% exact match, mean absolute difference = 0.0, max absolute difference = 0.0**, across all 3 metrics × 4 horizons × 2 splits (24 verification queries, zero exceptions). This was computed by joining the two files on `(segment_id, timestamp + horizon)` and comparing values directly — not inferred from documentation.

**Implication, stated plainly**: `forecast_targets_*.csv` is not an independently-simulated forecast product — it is a deterministic copy of the future `traffic_*.csv` row. The organizer's own README warns "do not treat forecast target files as model input features," and this verification shows *why* that warning is load-bearing: the targets and the future ground truth are the same number.

## FORECAST LEAKAGE RULES

The following must never be available to a model at the moment it produces a prediction for time `t`:

- `forecast_targets_train.csv` / `forecast_targets_validation.csv` — confirmed identical to future ground truth (§7). Labels only, never features, for any timestamp being predicted.
- Any row of `traffic_train.csv` / `traffic_validation.csv` with `timestamp > t` for the segment/segments being predicted — since forecast targets are literally these future rows, having the file loaded in memory (as it will be, since it's provided as one flat file, not streamed) creates a real risk of accidentally indexing forward instead of backward when building lag/rolling features.
- `incidents_*.csv` rows with `start_time > t` (future incidents) — using an incident that hasn't started yet as a detection/forecast feature is temporal leakage.
- `roadworks_*.csv` rows with `start_time > t` — same reasoning.
- `context_*.csv` rows with `timestamp > t`, *except* where a feature is legitimately using a forecast weather product (not the case here — context here is observed, not forecast, so future context is not knowable at prediction time).
- `network.structural_bottleneck` used as a **detector output** rather than as an evaluation/prioritization aid — see the leakage table below for the nuance.
- `scenario_examples.csv` — confirmed (§9 audit) to be built from exact rows of `incidents_train.csv`; do not use it as an independent validation set for anything trained on `incidents_train.csv`, since 100% of it is already in the training incident labels.

## 8. Incident Ground Truth Analysis

- **Counts**: 49 training incidents, 11 validation incidents.
- **Types (train)**: lane_blockage 15, accident_like 14, stalled_vehicle 12, demand_surge 4, road_closure 4.
- **Types (validation)**: lane_blockage 7, accident_like 3, stalled_vehicle 1 — **demand_surge and road_closure never appear in validation**, confirmed by direct query (0 unseen-in-train types the other direction, but validation's type set is a strict subset).
- **Severity**: train skews mild (severity 1: 25, 2: 16, 3: 8); validation skews more severe (severity 1: 2, 2: 6, 3: 3) — a real, measured distribution shift.
- **Duration**: train avg 31.0 min (range 10–63); validation avg 29.5 min (range 13–59) — comparable.
- **Segment overlap train↔validation**: **0** — confirmed by set intersection. Validation incidents occur on entirely different segments than training incidents (46 distinct train segments, 11 distinct validation segments, zero shared).
- **Incident/roadwork overlap**: 0 incidents temporally+spatially overlap a roadwork in the training data — the provided data does not exercise the "simultaneous incident and roadwork" confound, though a robust detector should still not assume this holds in the hidden test.
- **Ground truth vs model input**: `incidents_*.csv` should be treated as **GROUND TRUTH for training/evaluating a detector**, never as a runtime input to that same detector — a real-time system cannot know "there is an incident of type X, severity Y" before detecting it; it can only observe `traffic_*`/`context_*` and infer incident-like behavior from abnormal speed/flow/queue/congestion patterns, which is exactly what the problem statement's "decision-support system that can infer what is happening" describes. Incident labels are legitimately used to (a) train a classifier that maps observed-anomaly-shape → likely incident type, and (b) score detection accuracy — not as a live feature.

## 9. Network Graph Analysis

- **Structure**: 436 directed edges, 120 nodes. `nodes.x`/`nodes.y` span exactly 0–11 and 0–9 — a **12×10 regular grid** (12×10 = 120, exact). Out-degree and in-degree distributions are identical: degree 2 → 4 nodes (the 4 grid corners), degree 3 → 36 nodes (the grid perimeter, non-corner: 2×(12−2)+2×(10−2) = 36, exact), degree 4 → 80 nodes (interior: 120−4−36 = 80, exact). This is conclusive: **the network is a synthetic bidirectional grid, not an organically-shaped city topology** — a fact the coordinate data proves directly, not an assumption from generic road-network knowledge.
- **Reciprocity**: 218 reciprocal edge pairs = exactly 436/2 — **every single segment has a reverse-direction counterpart**. The network is fully bidirectional.
- **Self-loops**: 0. **Duplicate directed edges**: 0. **Isolated nodes**: 0. Clean graph, no structural defects.
- **Road class**: arterial 182, collector 254 (no highway/local class present).
- **Should we use a GNN?** No — evaluated honestly against the actual structure, not against "graph AI sounds innovative": a GNN's value comes from learning complex, irregular message-passing patterns over an intricate topology. Here the topology is a uniform-degree grid with only two road classes and a handful of distinct capacity/speed values (6 and 4 distinct values respectively). A lightweight, explicit propagation rule (e.g., diffuse a congestion signal to the 2–4 directly-adjacent segments, weighted by shared capacity/distance) is fully justified by this structure and is easier to explain (a rubric-scored criterion) than a trained GNN would be. **Graph-based propagation is justified by the data; a GNN specifically is not — the grid is too small and too regular to need one.**

## 10. Structural Bottleneck Analysis

- **Count**: 16 of 436 segments (3.67%) flagged `structural_bottleneck = 1`.
- **Road class**: 100% of flagged segments are `arterial` (16/16) — bottlenecks are concentrated on the higher-class road type, not spread evenly.
- **Importance**: bottleneck segments average 1.147 vs. 0.898 for non-bottleneck (a real, measured ~28% difference) — the flag correlates with the network's own importance metric.
- **Observed congestion**: average `congestion_index` on bottleneck segments in `traffic_train` is 0.0333 vs. 0.0237 on non-bottleneck segments — **~40% higher**, confirmed from the actual traffic observations, not just the static flag. This means the `structural_bottleneck` label is corroborated by observed behavior, not an arbitrary tag.
- **Distinguishing a temporary incident from a recurring structural bottleneck** (the report's explicit ask): a temporary incident is a `incidents_*.csv`-logged event with a bounded `start_time`/`end_time` (minutes to ~1 hour) on a segment that need not be flagged `structural_bottleneck`; a recurring bottleneck is a `network.structural_bottleneck=1` segment whose elevated `congestion_index` is a *persistent statistical property across the full 15-day training window*, not tied to any single incident window. Practically: check whether elevated congestion at a segment correlates with logged incident windows (transient explanation) or persists at similar times of day across many days with no incident logged (structural explanation, matches the bottleneck flag's own definition).
- **Candidate coverage — a real limitation**: only **3 of the 16** bottleneck segments have a `planning_candidates.csv` row whose `target_segment` directly matches them. The other 13 have no direct candidate — any intervention recommendation for those 13 must reason about a *neighboring* segment's candidate (e.g., widening an adjacent feeder), which is a materially harder and less-supported claim than a direct match.

## 11. Context & Roadwork Analysis

- **Temperature**: train mean 25.63°C (σ 3.11, range 18.64–31.57); validation mean 25.01°C (σ 3.11, range 19.87–31.17) — comparable.
- **Rain intensity — a real, measured shift**: train mean 0.0097 (max 0.484); validation mean 0.0430 (max 0.651) — **validation is on average ~4.4× rainier than training**, a genuine distribution shift a model must not be blindsided by.
- **Holiday flag — a real, measured shift**: train is 26.7% holiday (1,152/4,320); validation is exactly 50% holiday (576/1,152). Validation over-represents holidays relative to training.
- **Event level — a documentation gap, not resolved by us**: train's `event_level` values are {0,1,3}; validation's are {0,2}. The two splits share no non-zero event level, and no lookup table defines what levels 1/2/3 mean beyond the integer. This must be flagged to the team as an open question rather than guessed at.
- **Roadworks**: 8 training / 3 validation records — too few for any statistical claim; closure_fraction ranges 0.24–0.65 (train) and 0.25–0.54 (validation), average duration 365 min (train) vs. 455 min (validation) (small-sample, not asserted as a reliable shift).

## 12. Signal & Turn Restriction Analysis

- **Signals**: 89 signal plans, all referenced by `network.signal_id` (0 unused plans). 325 of 436 segments (74.5%) are signalized (`signal_id` non-null); the remaining 111 (25.5%) are not. Cycle times: 90–120s (4 distinct values); green ratios 0.421–0.619; offsets 0–87s.
- **Turn restrictions**: 61 rows, types `no_turn` (18), `no_left` (20), `time_window` (23). All `from_segment`/`to_segment`/`node_id` references resolve against `network.csv`/`nodes.csv` (0 orphans) — verified, not assumed.
- **Why they matter for diversions**: any diversion/rerouting recommendation that ignores `turn_restrictions.csv` can propose an illegal maneuver (e.g., routing traffic through a `no_left` restriction); any recommendation touching a signalized node should be checked against `signal_plans.csv` timing (a `signal_retiming` candidate in `planning_candidates.csv` is only meaningful in the context of the node's actual current cycle/green-ratio).

## 13. OD Demand Analysis

- 1,500 OD pairs, 120 distinct origins, 120 distinct destinations (full node coverage both directions), 0 self-pairs, total baseline demand 401,506 vehicles/hour network-wide.
- Purpose split: commercial (384 pairs, 104,712 vph), commute (384, 104,438), school (377, 100,364), mixed (355, 91,992) — roughly even across the four categories.
- **What this supports**: a static, baseline-demand traffic-assignment-style simulation (e.g., a BPR volume-delay function driven by these OD flows over the network graph) is directly supported — the data provides exactly the inputs such a model needs (origin, destination, baseline volume). **What it does not support**: time-varying demand (there is one static `base_demand_vph` per OD pair, no time-of-day demand curve provided), so any "demand surge at 8am" behavior must come from the observed `traffic_train` patterns, not from a documented OD time series. Rerouting/counterfactual simulation is supported at the level of "how does baseline flow redistribute if a segment's capacity changes," not at the level of "how does an individual driver's route choice change."

## 14. Planning Candidate Analysis

- 90 candidates, one per targeted segment (0 segments with multiple candidate options — confirmed).
- Intervention types: lane_addition (24), capacity_upgrade (19), connector (18), turn_lane (15), signal_retiming (14).
- `capacity_delta_vph`: 250–900 (5 distinct values); `cost_index`: 2–18 (5 distinct values); `feasibility_band`: medium (43), low (29), high (18).
- **Before/after support**: the dataset supports a *capacity-side* before/after comparison directly (candidate's `capacity_delta_vph` can be added to `network.capacity_vph` and re-run through a volume-delay function against `od_demand_profiles.csv` baseline demand to estimate a new equilibrium speed/delay). It does **not** provide a documented before/after outcome to check our simulation against — there is no `intervention_reference.csv` in this package (the manifest names it as a hidden file), so any before/after number we produce is our own simulation's output, not something verifiable against organizer ground truth until the hidden evaluation. **Limitation**: only 3/16 structural bottlenecks have a direct candidate (§10) — most candidate-to-bottleneck reasoning will be indirect.

## 15. Scenario Analysis

- 30 scenarios, all `scenario_type = incident` (no other scenario type exists in this file).
- Incident-type mix: accident_like (10), stalled_vehicle (9), lane_blockage (8), road_closure (2), demand_surge (1).
- Severity: 1 (15), 2 (11), 3 (4) — skews mild, matching training incidents' skew.
- **Critical finding**: **all 30 of 30 scenarios exactly match a row in `incidents_train.csv`** on `(target_segment, start_time, end_time, incident_type)` — confirmed by direct join, not inferred. `scenario_examples.csv` is not independent data; it is a curated, labeled subset of `incidents_train.csv` packaged with an instruction to run the candidate-evaluation workflow.
- Only **8 of 30** scenarios (26.7%) have a `planning_candidates.csv` row whose `target_segment` directly matches the scenario's `target_segment` — the remaining 22 (73.3%) require either a "no direct intervention available, recommend operational-only response" conclusion or reasoning about a neighboring segment's candidate. **Confirmed: most scenarios require neighboring-segment reasoning, not direct lookup.**
- Only **2 of 30** scenarios sit on a `structural_bottleneck=1` segment — most scenarios are transient-incident cases on non-bottleneck segments, not disguised recurring-bottleneck cases.
- **What this reveals about the judging workflow**: the intended output shape per incident is "detect/receive an incident → check for a directly-targeting planning candidate → if none, reason about nearby candidates or conclude none is warranted → produce a baseline-vs-counterfactual comparison." The low direct-candidate hit rate (27%) means a system that only ever checks for an exact segment match will visibly under-perform on most scenarios — this is a real, evidenced argument for building neighbor-aware candidate search rather than exact-match lookup.

## 16. Hidden-Test / Robustness Analysis

**Explicitly documented** (from `DATASET_MANIFEST.json` and dataset `README.md`, quoted/paraphrased faithfully):
- Hidden test period: 8 days, 1,004,544 observation rows, 36 test scenarios.
- Noise categories the manifest lists as dataset features generally: "missing values, duplicates, spikes, stuck sensors, impossible negative readings, row shuffle."
- Two files are declared as existing but withheld: `scenario_ground_truth.csv`, `intervention_reference.csv`.
- README: "Do not treat forecast target files as model input features," and the challenge is explicitly framed as more than forecasting — reasoning about incidents, propagation, diversions, recurring bottlenecks, and counterfactual interventions is named as an explicit expectation.

**Inferred, not documented (labeled as such)**:
- Because every noise category the manifest lists was checked and found **absent** from `traffic_train.csv`/`traffic_validation.csv` (0 nulls beyond legitimate ones, 0 negatives, 0 duplicates, perfectly ordered timestamps — see `backend/data/validated/validation_report.md`), it is a reasonable **inference**, not a documented fact, that this noise is reserved for the hidden 8-day test file specifically. This cannot be verified without that file.
- It is a reasonable **inference** that the 36 hidden test scenarios follow the same shape as `scenario_examples.csv` (given the near-identical column set implied by the shared `candidate_interventions` instruction pattern), but this is inferred from the training scenario file's design intent, not confirmed against the withheld hidden scenario file.
- `sensor_quality`'s constant value of 1.0 in train/validation, paired with the manifest's "stuck sensors" claim, makes it a reasonable **inference** that this column is where sensor-quality degradation will appear in the hidden test — but this is inferred from the column's name and current constancy, not documented behavior.

## 17. Data Leakage Risks

# DATA LEAKAGE THREATS

| Threat | Why it leaks | Severity | Correct handling |
|---|---|---|---|
| Using `forecast_targets_*.csv` as a model feature (even indirectly, e.g., joined in for "context") | Verified identical to future ground truth (§7) | **Critical** | Use only as training/evaluation labels; never join into a feature-building step |
| Building lag/rolling features from `traffic_validation.csv` without enforcing a strict `timestamp ≤ t` cutoff | The entire validation file is available at once, not streamed — a naive rolling-window implementation can accidentally include future rows | **Critical** | Explicitly filter every feature query to `timestamp ≤ t` before any window/aggregation function runs |
| Using `incidents_*.csv`/`roadworks_*.csv` rows with `start_time > t` as detector input | Future events aren't observable at time t in a real deployment | **High** | Filter to `start_time ≤ t` (or better: don't use incident labels as detector *input* at all — see §8) |
| Treating `scenario_examples.csv` as an independent validation set for an incident model trained on `incidents_train.csv` | 100% of scenarios are exact `incidents_train` rows (§15) | **High** | Never use scenario_examples for held-out evaluation of anything trained on incidents_train; it is training data restated |
| Using `network.structural_bottleneck` directly as a detector's *output claim* ("we detected this bottleneck") rather than as a target the detector should independently rediscover | This is organizer-provided ground truth for what a good detector *should* find, not a legitimate detection result | **Medium** | Use it to *score* an independently-built bottleneck-detection method (e.g., "our persistence-based detector recovered X/16 flagged segments"), never surface it as if the system found it live |
| Computing "future demand" features from `od_demand_profiles.csv` | There is only one static baseline value per OD pair — no time dimension exists to leak, but a team could mistakenly treat it as if it varied by time and build a spurious feature | **Low** (more a correctness risk than leakage) | Treat as a static network property, not a time series |
| Cross-contamination between `context_train`/`context_validation` `event_id` values if a team assumes shared semantics | Train and validation event levels/IDs never overlap (§11) — assuming `EVT_101_01`-style IDs generalize to validation's `EVT_202_*` IDs is unfounded | **Low-Medium** | Treat `event_id` as split-specific; use `event_level` (the numeric severity) cautiously given the level-set mismatch, and document the gap rather than paper over it |

## 18. Data Quality Risks

Per the audit, verified with real queries (see `backend/data/validated/validation_report.md` for the full run): **zero** referential-integrity violations, **zero** negative-value violations, **zero** full-row duplicates, **zero** out-of-range coordinates, **zero** end-before-start timestamp violations, across every file checked. The only quality-adjacent observations worth flagging as risks for *modeling*, not data defects per se:

- `sensor_quality` has zero variance (always 1.0) in train/validation — cannot be used to learn anything about sensor reliability from the data we have; any robustness logic built around this column is untested against real variation until the hidden test.
- `context.event_id`/`event_level` value-set mismatch between train and validation (§11) — not a corruption, but an unexplained discontinuity that could confuse a model that treats event level as an ordinal signal with consistent meaning across splits.
- Only 8 (train) / 3 (validation) roadwork records and 49 (train) / 11 (validation) incident records — small enough that any statistical claim about incident/roadwork *distributions* (beyond simple counts) carries real sampling uncertainty; the numbers in §8/§11 are reported as measured facts about *this sample*, not as claims about a larger population.
- `planning_candidates.csv`'s 1-candidate-per-segment structure and low (3/16, 8/30) direct-match rates to bottlenecks/scenarios (§10, §15) are a genuine scope limitation, not a defect — flagged again here because it constrains what "before/after" claims can honestly cover.

No records were removed, imputed, or transformed during this audit — there was nothing to fix.

## 19. Train vs Validation Differences

| Aspect | Train | Validation | Verified shift? |
|---|---|---|---|
| Rain intensity (mean) | 0.0097 | 0.0430 | Yes — ~4.4× higher in validation |
| Holiday flag proportion | 26.7% | 50.0% | Yes — validation over-represents holidays |
| Event levels present | {0,1,3} | {0,2} | Yes — disjoint non-zero sets |
| Incident severity mix | skews mild (1:25, 2:16, 3:8) | skews more severe (1:2, 2:6, 3:3) | Yes |
| Incident types present | 5 types | 3 types (subset of train's) | Yes |
| Incident segments | 46 distinct | 11 distinct | 0 overlap with train |
| Forecast-target anchor density | 19.4% of timestamps | 95.8% of timestamps | Yes — large, previously-unremarked asymmetry (§6) |
| Traffic/context resolution, segment completeness, network topology | identical | identical | No shift (static/topology is shared) |

These are measured differences in the data provided, not predictions about the hidden test — but they are the strongest available evidence for how a hidden test *might* differ from training, since the organizer already demonstrates train→validation shift along several axes.

## 20. What the Dataset Supports

| Capability | Verdict | Why |
|---|---|---|
| Congestion detection | **SUPPORTED** | Rich, clean, high-frequency `congestion_index`/speed/occupancy signal with a documented structural-bottleneck baseline to calibrate against |
| Abnormal traffic detection | **SUPPORTED** | Same signal, plus context (weather/event/holiday) to condition a baseline and reduce false alarms |
| Incident detection (inferring "something is happening" from traffic/context) | **SUPPORTED** | Incident labels exist to train/score a detector; traffic signal shows measurable behavior around bottleneck segments |
| Incident *classification* (which of the 5 types) | **PARTIALLY SUPPORTED** | Only 49 training examples across 5 classes (as few as 4 per class for demand_surge/road_closure) — enough to attempt, not enough to trust a fine-grained classifier's accuracy claim |
| 15-minute forecasting | **SUPPORTED** | Verified exact labels (§7), dense validation anchors, rich lag-feature history available |
| 30/45/60-minute forecasting | **SUPPORTED** | Same target verification holds at all four horizons |
| Graph-based spillback prediction | **PARTIALLY SUPPORTED** | Graph structure is real and clean (§9), but the provided data contains no explicit multi-segment "this incident caused congestion two segments away" labeled example to validate a propagation model against — propagation must be modeled and evaluated indirectly (e.g., via observed correlation between adjacent segments' congestion during known incident windows), not directly supervised |
| Diversion recommendation | **PARTIALLY SUPPORTED** | Turn restrictions and network topology support *feasibility-checking* a diversion; there is no labeled "correct diversion route" to learn from or validate against |
| Network-level simulation | **SUPPORTED** | OD demand + network capacity are sufficient inputs for a defensible volume-delay-function-style simulation |
| Infrastructure recommendation | **PARTIALLY SUPPORTED** | 90 well-specified candidates exist, but only cover a fraction of bottleneck/scenario segments directly (§10, §15, §14) |
| Before/after intervention evaluation | **PARTIALLY SUPPORTED** | We can compute our own before/after simulation (capacity + OD demand), but cannot check it against organizer ground truth (`intervention_reference.csv` is withheld) |
| Robustness evaluation | **NOT SUPPORTED by the provided data alone** | The noise the manifest describes is absent from train/validation (§16) — any robustness claim requires a self-built corruption harness, not organizer-provided noisy data |
| Explainability | **SUPPORTED** | Every derived quantity (delay, congestion_index, candidate scores) can be traced to concrete input columns; nothing here requires an unexplainable black box |

## 21. What the Dataset Does NOT Support

- A trained, high-confidence incident *type* classifier (too few labeled examples per class).
- A directly-supervised spillback/propagation model (no multi-segment causal labels).
- Verifiable robustness claims against the manifest's declared noise types (none of that noise exists in what we have).
- A learned "optimal diversion route" (no route-level ground truth).
- Verified before/after intervention accuracy (the reference file is withheld).
- Any claim about hidden-test-period conditions beyond what train→validation shift already demonstrates (§19) — we have no visibility into the 8 hidden days.
- Time-varying OD demand modeling (only one static baseline value per pair).
- Any semantic interpretation of `event_level`/`event_id` beyond "a numbered category exists" — no lookup table is provided.

## 22. Mapping to Hackathon Judging Criteria

| Criterion (marks) | Supporting dataset(s) | Evidence available | What cannot be claimed yet |
|---|---|---|---|
| Congestion/incident detection (10) | traffic_*, context_*, incidents_*, network.structural_bottleneck | Clean, high-frequency signal; labeled incidents for scoring; bottleneck flag for calibration | False-alarm control against *hidden* noise conditions — untestable without the hidden file |
| Forecasting (10) | traffic_*, forecast_targets_* | Verified-exact labels at all 4 horizons (§7), dense validation anchors | Accuracy under hidden-test noise; accuracy on truly unseen demand patterns beyond what train/validation shift already shows |
| Adaptive recommendations (10) | scenario_examples, planning_candidates, network, turn_restrictions, signal_plans | 30 worked examples showing the expected reasoning shape; real constraint data to check feasibility | Whether our recommendation matches the organizer's own reference (`intervention_reference.csv` withheld) |
| Robustness (10) | none of the provided files exercise this directly | Train/validation itself shows some distribution shift (§19) to test generalization against | The manifest's specific noise types (missing/spikes/stuck sensors/negatives/shuffle) — must be self-injected to test at all |
| Explainability/confidence (5) | all files, since every field is traceable | Every input is a named, documented (or clearly-inferred) column | N/A — this is achievable by design discipline, not blocked by data gaps |
| Technical implementation (5) | n/a (judged on our code, not the data) | — | — |
| UI/UX (5) | nodes.csv (lat/lon) for real map rendering | Real Hyderabad-area coordinates available | — |
| Innovation (5) | network.csv graph structure | Grid topology genuinely supports a lightweight propagation method as a real (not cosmetic) innovation | A GNN would not be justified innovation given the topology (§9) — reviewers evaluating "technical novelty that improves decision quality" would likely see an unjustified GNN as the opposite of the intended innovation |

No scores are assigned here, per instruction — only what evidence exists and what remains unverifiable from this package alone.

## 23. Recommended Data Flow — NO CODE

1. Join `traffic_*` + `context_*` on timestamp into one per-segment time-indexed panel (both share the identical 5-minute grid).
2. Attach static `network.csv` attributes (road_class, capacity, free_flow_speed, structural_bottleneck, importance) to each segment via `segment_id`.
3. For any point in time `t` being evaluated, strictly restrict all history-based feature computation to rows with `timestamp ≤ t` (per the leakage rules in §17).
4. Use `incidents_*`/`roadworks_*` only in their **valid** time windows (`start_time ≤ t < end_time`) as *context* features (e.g., "is a roadwork active on this segment right now"), never as a label leaking into detection at the same instant it's meant to be inferred.
5. Hold `forecast_targets_*` aside, untouched by feature engineering, purely as the label set for the forecaster and its backtests.
6. Build the static graph (nodes, network edges, turn restrictions, signal plans) once, separately from the time-indexed panel, for use by any propagation/simulation step.
7. Keep `od_demand_profiles.csv` and `planning_candidates.csv` as their own static simulation-input tables, joined to the graph only at simulation time, not blended into the per-timestamp panel.
8. Treat `scenario_examples.csv` purely as a worked-example/template for output shape — not as additional training or validation signal, since it duplicates `incidents_train.csv`.

## 24. Recommended AI/ML Architecture — CONCEPT ONLY

- **Detection**: a statistical baseline (expected speed/flow/congestion conditioned on segment, hour-of-day, day-of-week, road_class) with deviation-based scoring, calibrated against the 16 `structural_bottleneck` segments and the labeled incidents — not a from-scratch deep model, given the clean tabular signal and small incident-label count.
- **Forecasting**: a gradient-boosted tree model (one per horizon, or a single multi-output model) over lag/rolling features from the traffic panel plus context — justified by the tabular, moderate-dimensionality nature of the features; an LSTM is not obviously justified by anything in this audit (the data has no long-range sequential dependency demonstrated here beyond what lag features already capture, and 15 training days is a modest sample for a recurrent model to learn from without overfitting) — this should be evaluated against the boosting baseline before being chosen, not assumed superior because it's time-series data.
- **Propagation**: a lightweight, explicit graph-diffusion rule over the grid's immediate neighbors (justified in §9), not a GNN.
- **Recommendation**: a rules-plus-scoring approach over `planning_candidates.csv`, using a BPR-style volume-delay simulation (§13) for the before/after estimate — not a learned recommender, since there are only 90 candidates and no historical "which recommendation worked" label to learn from.
- All of the above is a concept-level direction consistent with the evidence in this report; final architecture choices belong to the design phase, not this audit.

## 25. Recommended MVP Scope — CONCEPT ONLY

Directly supported by the data (§20) and should be the MVP's core: congestion/anomaly detection with confidence, 15–60 minute forecasting backtested against the verified-exact validation labels, a scenario-handling flow modeled on `scenario_examples.csv`'s shape (detect → check direct candidate → fall back to neighbor reasoning → simulate before/after), and a map UI using the real `nodes.csv` coordinates. Concept-only — no implementation decisions are made here.

## 26. Key Technical Decisions We Must Make Later

- How to define the statistical baseline for "normal" traffic (which conditioning variables, what window) — data supports many reasonable choices, none forced by the data itself.
- The exact congestion-propagation weighting scheme (by capacity, by distance, by both) — the graph structure supports propagation, but the weighting is a design choice.
- Whether/how to handle the `event_level` train/validation mismatch (§11) — ignore the field, use only `event_id` presence as a binary flag, or something else.
- How to score "neighbor-based" infrastructure recommendations for the 73% of scenarios without a direct candidate (§15) — what counts as a "neighbor," how far to search.
- What synthetic noise to inject for the robustness harness, and how closely to model it on the manifest's stated categories versus our own judgment.

## 27. Questions/Unknowns Requiring Further Verification

- What do `event_level` integer values 1/2/3 (train) and 2 (validation) actually represent? No documentation defines this.
- What exact formula produces `congestion_index`, `importance`, and `grade_pct`? Not documented; only value ranges/behavior were observed.
- Will the hidden 8-day test file actually contain the noise types the manifest lists, given they're absent from train/validation? Cannot be confirmed without that file.
- Does the hidden `scenario_ground_truth.csv`/`intervention_reference.csv` grade against exact-match candidate selection, simulated impact magnitude, or something else? Not documented.
- Is `sensor_quality` meant to vary in the hidden test, or is it vestigial? Its constancy here is consistent with either.

## 28. Final Dataset Verdict

The dataset is clean, internally consistent, and well-suited to the problem statement's core asks: it strongly supports detection, forecasting (with the critical caveat that forecast targets are verified-exact future values and must be handled purely as labels), graph-aware (but not GNN-scale) propagation, and a defensible simulation-based approach to infrastructure recommendation. Its clearest limitations are small labeled-incident counts (weak support for fine-grained classification), the near-total absence of the manifest's declared noise (robustness must be self-tested), and partial coverage of bottleneck/scenario segments by direct planning candidates (roughly 3/16 and 8/30 respectively) — meaning a credible system must reason about neighboring segments, not just exact matches, to cover most of what the hidden evaluation will likely probe. No implementation was started; this report is the complete basis for designing the MVP next.
