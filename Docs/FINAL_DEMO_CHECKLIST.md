# FlowSense AI — 4-minute demo

## Before the demo (once)
- [ ] `cd backend && python -m uvicorn app.api:app --port 8000` and wait for startup. The demo snapshot and its recommendation warm up in the background, which takes about 20 s.
- [ ] `cd frontend && npm run dev`, then open http://localhost:5173 and sign in with **tgpolice / tgpolice**.
- [ ] Optional pre-warm for step 6 (after signing in): open http://localhost:5173/interventions?t=2026-01-16%2016:45:00&seg=R0384 once and wait for the table.
- [ ] Browser at ≥ 1440 px wide.

## Flow (every number comes live from the dataset and models)

1. **Dashboard, 0:00–0:40.** The clock sits at **Fri 16 Jan 13:30**, a held-out validation day. The map is the real city with only the incident corridor emphasized; click **R0360** in the events list and the camera flies to street level.
   - Say: "Real basemap, synthetic simulation network: the organizer grid placed on Hyderabad coordinates, labelled so."
   - Point to one red segment, **R0360**, with 13 heavy segments around it.
2. **What happened and why, 0:40–1:20.** Click R0360.
   - Anomaly is DETECTED, confidence HIGH (0.94).
   - Diagnosis: *Transient incident*, "speed ratio fell 0.58 in one 5-min step", with the network-wide shift removed.
   - Open **"Why is this segment critical?"**: speed 12.6 against a normal of ~29.9 km/h, plus threshold, rain, roadwork and data quality.
   - Point out that the likely *type* is "undetermined, LOW". We show that honestly: type is not identifiable from traffic in this data.
3. **What next, 1:20–1:50.** Click **+30** above the map: neighbors recede and the source eases to amber (the model's forecast, labelled *FORECAST +30 min*). Then point to the forecast in the bottom panel: speed now 12.6, then 18.8 at +15 and back to 30.0 by +45 (P10–P90 band shown).
   - Optional: Forecast page, then **Evaluation / backtest** toggle. The striped banner marks recorded future values, which are never used as input.
4. **Where it spreads, 1:50–2:15.** Propagation page, +15 m: six 1-hop neighbors at HIGH risk, upstream and downstream (dashed), ETA 5 min.
   - Say: "Calibrated from training incidents; validated indirectly at 91% hit rate against a 19% control."
5. **What can we do, 2:15–2:45.** Interventions page.
   - There is no direct candidate for R0360, so the search goes to neighbors within 2 hops.
   - Show the feasibility ticks, cost, capacity delta and score.
   - Show the legal diversion R0320 → R0314 → R0315, with turns verified.
   - Show the honest headline: confidence LOW, network change within solver tolerance.
6. **What if we do it, 2:45–3:30.** Change the clock to **16 Jan 16:45** and select **R0384**. It is CRITICAL, and a direct candidate PLAN0383 exists.
   - Point out that the direct candidate ranks *below* neighbor candidates, because the simulation shows it pulls flow in and worsens local delay.
   - Click **SIMULATE** on a candidate. The Simulation page shows baseline vs counterfactual maps side by side, the KPI table (absolute and %), segments improved/worsened and side effects, all labelled *SIMULATION ESTIMATE*.
7. **Why trust it, 3:30–4:00.** Evaluation page: forecasts beat persistence on all 12 targets, 11/11 validation incidents detected (median 2 min), the bottleneck benchmark, and synthetic robustness (combined noise keeps F1 0.87 against 0.01 without sanitizing).
   - Close with: no API keys, no labels at runtime, every number reproducible via `backend/scripts`.

## If something fails
- Blank data with "Backend unreachable" means the API isn't running (see step 0).
- "Model artifacts unavailable" means `python scripts/train_models.py` needs to be run.
- If the basemap is missing, the overlay still works and the map shows a notice.
