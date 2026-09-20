# FlowSense AI — Model Card

All numbers are from `backend/artifacts/metrics/*.json`. Tags: **[FACT]** measured, **[DESIGN]** our choice, **[INF]** inference.

## 1. Forecasters (12 models)

- **Task:** speed_kmh, flow_vph and congestion_index at +15/30/45/60 min for every segment.
- **Model:** LightGBM regressor per metric × horizon. The target is Δ = value(t+h) − value(t), and the prediction is now + Δ̂, clipped to physical bounds.
- **Parameters:** 300 trees, learning rate 0.05, 63 leaves, min child samples 50, bagging 0.8, feature fraction 0.8, seed 42.
- **Features (60):**
  - lags at 5/15/30/60 min;
  - trailing means over 15/30/60 min;
  - 15-min slopes;
  - value in the same slot yesterday at t+h (which is strictly < t);
  - occupancy, queue, delay and v/c now;
  - mean congestion, speed ratio and flow of up/downstream neighbors at t;
  - time of day (sin/cos, for t and t+h), day of week, weekend, holiday, rain, temperature, event present;
  - static attributes: class, lanes, capacity, free-flow speed, length, grade, signalized, importance.
- **Data:**
  - Fit on train days 1–12 (every 3rd slot, ~500k rows per model; labels never cross 12 Jan 23:55).
  - Residual bands from days 13–15.
  - Evaluated on validation days (16–19 Jan) against `forecast_targets_validation.csv`.
- **Uncertainty:** empirical P10/P90 of calibration residuals, split by state (normal vs congested). Validation coverage is 70–79% against a nominal 80%, so the band is slightly narrow at long horizons. It is not a calibrated probabilistic forecast.
- **Results [FACT] (validation MAE, model vs persistence):**

| | +15 | +30 | +45 | +60 |
|---|---|---|---|---|
| speed (km/h) | 0.504 vs 0.543 | 0.612 vs 0.631 | 0.635 vs 0.723 | 0.666 vs 0.818 |
| flow (veh/h) | 151 vs 207 | 153 vs 215 | 154 vs 227 | 155 vs 242 |
| congestion | 0.0116 vs 0.0124 | 0.0136 vs 0.0144 | 0.0144 vs 0.0164 | 0.0155 vs 0.0186 |

- **Limitations:**
  - Only 2.4% of validation rows are congested, so the averages are dominated by free flow. The congested-slot MAE is reported separately in EVALUATION_REPORT.
  - With 15 training days the models learn little about rare days.
  - The rain/holiday shift in validation is only partly represented in training.

## 2. Anomaly detector

- **Score** [DESIGN, chosen by train F1 among 3 variants]: `drop_epicentre`. The local drop is (baseline speed ratio − current speed ratio) − the network-wide median drop at t. It is kept only where it is ≥ every 1-hop neighbor's drop.
- **Baseline:** median/MAD per segment × hour × day type (weekday vs weekend/holiday) from traffic_train, with labeled incident and roadwork windows removed.
- **Threshold:** 0.341, speed-ratio units, set by the best slot-level F1 on incidents_train only.
- **States** [DESIGN]: speed/free-flow speed ≥ 0.85 NORMAL, ≥ 0.70 MODERATE, ≥ 0.50 HEAVY, else CRITICAL.
- **Confidence:** logistic distance from the threshold × data quality. This is a heuristic, not a probability.
- **Results [FACT]:**

| Split | Precision | Recall | F1 | Incidents found | Median delay |
|---|---|---|---|---|---|
| Train (in-sample) | 0.72 | 0.94 | 0.81 | 49/49 | 3 min |
| Validation | 0.56 | 0.96 | 0.71 | 11/11 | 2 min |

- **Caveat [INF]:** about 1,000 unlabeled incident-shaped drops exist in training, so precision against the labels is a lower bound.

## 3. Diagnosis and incident type

- **Cause:** transparent rules, in this order:
  1. roadwork active on the segment;
  2. abrupt onset (a step ≥ 0.10 in one 5-min reading) → transient incident;
  3. flagged but gradual → unknown;
  4. neighbor drop larger than own drop → propagated congestion;
  5. congested, independently detected bottleneck, peak hour → recurring bottleneck;
  6. congested with rain, an event or a network-wide shift → weather/event effect;
  7. otherwise normal variation.
- **Type likelihood:** nearest centroid on (mean speed-ratio drop, mean flow change) from the 49 training incidents.
- **Type results [FACT]:** 9% accuracy on validation (82% returned UNKNOWN), and 12% in-sample, against 20% uniform chance. **Type is not identifiable from these signals.** Severity sets the speed drop (≈0.58 / 0.42 / 0.28 speed ratio). The UI shows the type as low-confidence and separate from "detected incident". demand_surge and road_closure are absent from validation.

## 4. Recurring-bottleneck detector (independent)

- **Score:** p99(flow/capacity at peak hours) ÷ p99(off-peak), from traffic_train with incident and roadwork windows removed. Flagged at robust z ≤ −2.5, a threshold fixed before comparing with the flag.
- **Inputs never used:** `structural_bottleneck`, and `peak_capacity_factor`, which is 0.72 exactly on the 16 flagged segments [FACT].
- **Benchmark vs the organizer flag [FACT]:** 2 detected, both correct. Precision 1.00, recall 0.125, F1 0.22. Ranking AUC 0.968, and 13/16 flagged segments rank in the top 16. The threshold is conservative, and the sensitivity table is in EVALUATION_REPORT (label-informed, not used for selection).

## 5. Propagation

- **Model:** predicted drop = source drop × decay[direction][hop] × (0.75 + 0.5·min(v/c, 1)). ETA = lag × hop.
- **Calibrated on training incidents [FACT]:** decay 0.48 at hop 1 in both directions and ≈0.001 at hops 2–3, so spillback here is one hop deep. Lag is 5 min, one reading.
- **Validation (indirect) [FACT]:** hit rate 91%, recall 53%, false propagation 9%, against a 19% control base rate.

## 6. Network simulation

- Static user equilibrium: BPR (α 0.15, β 4) + Webster uniform delay from `signal_plans`. Frank-Wolfe with bisection line search, ≤ 80 iterations.
- Runs on the line graph with turn restrictions as forbidden transitions. `time_window` restrictions apply at peak hours [DESIGN; times are absent from the data].
- Demand is the OD base demand × a scale matching observed veh·km at t.
- **Pivot point:** scenario flow = observed + (assigned scenario − assigned reference). The raw assignment correlates only 0.10–0.18 with observed link flows [FACT].
- Candidates are modeled as +capacity_delta_vph on the target [DESIGN]. Incidents are a capacity multiplier sized by the observed state (CRITICAL 50%, HEAVY 33%, MODERATE 20%) [DESIGN].
- **Checks [FACT]:**
  - All assigned routes are legal.
  - A simulated capacity loss raises network delay for 11/11 validation incidents.
  - Network-level deltas from a single segment are small (≈0.01–0.7%) and usually within the solver tolerance, which is flagged in the output.
- **No intervention ground truth exists**, so every impact is a *simulated estimate*.

## 7. Recommendation ranking

- **Score** [DESIGN, weights in `config.py`] = 2.0 × network delay reduction % + 0.1 × local delay reduction % − 0.1 × worsened segments % − 1.0 × cost_index/18 − feasibility penalty (high 0, medium 0.5, low 1.5).
- There is no learned recommender, because no "which intervention worked" labels exist.
- A candidate is headlined only if it reduces both network and local delay. Otherwise the advisory is an "Operational-only response".
