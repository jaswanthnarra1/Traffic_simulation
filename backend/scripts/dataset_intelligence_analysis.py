"""
Audit-only analysis for the Dataset Intelligence Report. Read-only against
data/raw (via DuckDB's CSV scanner) and data/db/neurax.duckdb. Writes nothing
except a JSON dump of computed facts to data/metadata/dataset_intelligence.json
for the report to quote verbatim. No model training, no app code, no dataset
mutation.
"""
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
META = ROOT / "data" / "metadata"


def c(name):
    return f"read_csv_auto('{(RAW / name).as_posix()}', SAMPLE_SIZE=-1)"


def numeric_stats(con, file, col):
    q = f'''
        SELECT min("{col}"), max("{col}"), avg("{col}"), median("{col}"),
               stddev_pop("{col}"), count(DISTINCT "{col}"), count(*) FILTER (WHERE "{col}" IS NULL)
        FROM {c(file)}
    '''
    lo, hi, mean, med, sd, uniq, nulls = con.execute(q).fetchone()
    return {"min": lo, "max": hi, "mean": mean, "median": med, "stddev": sd, "distinct": uniq, "nulls": nulls}


def main():
    con = duckdb.connect()
    out = {}

    # ---------------- Full column profile per file (numeric + categorical) ----------------
    profile = {}
    for path in sorted(RAW.glob("*.csv")):
        schema = con.execute(f"DESCRIBE SELECT * FROM {c(path.name)}").fetchall()
        total = con.execute(f"SELECT count(*) FROM {c(path.name)}").fetchone()[0]
        cols = {}
        for name, dtype in [(r[0], r[1]) for r in schema]:
            nulls = con.execute(f'SELECT count(*) FROM {c(path.name)} WHERE "{name}" IS NULL').fetchone()[0]
            uniq = con.execute(f'SELECT count(DISTINCT "{name}") FROM {c(path.name)}').fetchone()[0]
            entry = {"type": dtype, "nulls": nulls, "null_pct": round(100 * nulls / total, 4) if total else 0,
                      "distinct": uniq}
            if dtype in ("BIGINT", "DOUBLE", "INTEGER", "HUGEINT", "DECIMAL"):
                s = numeric_stats(con, path.name, name)
                entry.update({k: s[k] for k in ("min", "max", "mean", "median", "stddev")})
            elif uniq <= 15:
                vals = con.execute(f'SELECT DISTINCT "{name}" FROM {c(path.name)} ORDER BY 1').fetchall()
                entry["values"] = [str(v[0]) for v in vals]
            cols[name] = entry
        profile[path.name] = {"rows": total, "columns": cols}
    out["column_profile"] = profile

    # ---------------- Forecast target verification ----------------
    # target_speed_15m(t, seg) should equal traffic.speed_kmh(t+15min, seg) IF targets are literal future draws.
    fv = {}
    for split, target_file, traffic_file in [
        ("train", "forecast_targets_train.csv", "traffic_train.csv"),
        ("validation", "forecast_targets_validation.csv", "traffic_validation.csv"),
    ]:
        split_res = {}
        for horizon in (15, 30, 45, 60):
            q = f'''
                SELECT count(*),
                       count(*) FILTER (WHERE abs(f.target_speed_{horizon}m - t.speed_kmh) < 0.001),
                       avg(abs(f.target_speed_{horizon}m - t.speed_kmh)),
                       max(abs(f.target_speed_{horizon}m - t.speed_kmh))
                FROM {c(target_file)} f
                JOIN {c(traffic_file)} t
                  ON t.segment_id = f.segment_id
                 AND t.timestamp = f.timestamp + INTERVAL '{horizon} minutes'
            '''
            n, exact, mad, maxad = con.execute(q).fetchone()
            split_res[f"+{horizon}m"] = {
                "joined_rows": n, "exact_match_count": exact,
                "mean_abs_diff": mad, "max_abs_diff": maxad,
            }
        # how many forecast rows had NO matching future traffic row at all (grid alignment)
        total_target_rows = con.execute(f"SELECT count(*) FROM {c(target_file)}").fetchone()[0]
        split_res["total_forecast_rows"] = total_target_rows
        fv[split] = split_res
    out["forecast_target_verification"] = fv

    # ---------------- Timestamp / resolution audit ----------------
    ts_audit = {}
    for f in ["traffic_train.csv", "traffic_validation.csv", "context_train.csv", "context_validation.csv"]:
        lo, hi, n = con.execute(f"SELECT min(timestamp), max(timestamp), count(DISTINCT timestamp) FROM {c(f)}").fetchone()
        # interval mode: compute distinct diffs
        diffs = con.execute(f'''
            WITH t AS (SELECT DISTINCT timestamp FROM {c(f)} ORDER BY timestamp),
                 d AS (SELECT date_diff('second', lag(timestamp) OVER (ORDER BY timestamp), timestamp) AS gap FROM t)
            SELECT gap, count(*) FROM d WHERE gap IS NOT NULL GROUP BY gap ORDER BY count(*) DESC LIMIT 5
        ''').fetchall()
        ts_audit[f] = {"min": str(lo), "max": str(hi), "distinct_timestamps": n, "top_gaps_seconds": diffs}
    # forecast anchor gaps
    for f in ["forecast_targets_train.csv", "forecast_targets_validation.csv"]:
        diffs = con.execute(f'''
            WITH t AS (SELECT DISTINCT timestamp FROM {c(f)} ORDER BY timestamp),
                 d AS (SELECT date_diff('second', lag(timestamp) OVER (ORDER BY timestamp), timestamp) AS gap FROM t)
            SELECT gap, count(*) FROM d WHERE gap IS NOT NULL GROUP BY gap ORDER BY count(*) DESC LIMIT 5
        ''').fetchall()
        n = con.execute(f"SELECT count(DISTINCT timestamp) FROM {c(f)}").fetchone()[0]
        ts_audit[f] = {"distinct_timestamps": n, "top_gaps_seconds": diffs}
    # does every segment appear at every traffic timestamp?
    for f in ["traffic_train.csv", "traffic_validation.csv"]:
        q = f'''
            SELECT count(*) FROM (
                SELECT timestamp, count(DISTINCT segment_id) AS n FROM {c(f)} GROUP BY timestamp
            ) WHERE n <> (SELECT count(DISTINCT segment_id) FROM {c(f)})
        '''
        bad = con.execute(q).fetchone()[0]
        ts_audit[f + "_segment_completeness_violations"] = bad
    out["timestamp_audit"] = ts_audit

    # ---------------- Incident audit ----------------
    inc = {}
    for split, f in [("train", "incidents_train.csv"), ("validation", "incidents_validation.csv")]:
        n = con.execute(f"SELECT count(*) FROM {c(f)}").fetchone()[0]
        types = con.execute(f'SELECT incident_type, count(*) FROM {c(f)} GROUP BY 1 ORDER BY 2 DESC').fetchall()
        sev = con.execute(f'SELECT severity, count(*) FROM {c(f)} GROUP BY 1 ORDER BY 1').fetchall()
        dur = con.execute(f'''SELECT avg(date_diff('minute', start_time, end_time)),
                                      min(date_diff('minute', start_time, end_time)),
                                      max(date_diff('minute', start_time, end_time))
                               FROM {c(f)}''').fetchone()
        segs = con.execute(f'SELECT count(DISTINCT segment_id) FROM {c(f)}').fetchone()[0]
        inc[split] = {"n": n, "types": types, "severity_dist": sev,
                      "duration_min_avg_min_max": dur, "distinct_segments": segs}
    # overlap train/validation segments
    overlap = con.execute(f'''
        SELECT count(*) FROM (
            SELECT segment_id FROM {c('incidents_train.csv')}
            INTERSECT
            SELECT segment_id FROM {c('incidents_validation.csv')}
        )
    ''').fetchone()[0]
    inc["train_validation_segment_overlap_count"] = overlap
    # validation incident types not seen in train
    unseen_types = con.execute(f'''
        SELECT DISTINCT incident_type FROM {c('incidents_validation.csv')}
        WHERE incident_type NOT IN (SELECT DISTINCT incident_type FROM {c('incidents_train.csv')})
    ''').fetchall()
    inc["validation_incident_types_unseen_in_train"] = [r[0] for r in unseen_types]
    # incidents overlapping roadworks (same segment, overlapping time) - train
    inc_rw_overlap = con.execute(f'''
        SELECT count(*) FROM {c('incidents_train.csv')} i
        JOIN {c('roadworks_train.csv')} r ON i.segment_id = r.segment_id
        WHERE i.start_time < r.end_time AND r.start_time < i.end_time
    ''').fetchone()[0]
    inc["incidents_overlapping_roadworks_train"] = inc_rw_overlap
    out["incident_audit"] = inc

    # ---------------- Scenario audit ----------------
    scen = {}
    scen["n"] = con.execute(f"SELECT count(*) FROM {c('scenario_examples.csv')}").fetchone()[0]
    scen["types"] = con.execute(f"SELECT scenario_type, count(*) FROM {c('scenario_examples.csv')} GROUP BY 1").fetchall()
    scen["incident_types"] = con.execute(f"SELECT incident_type, count(*) FROM {c('scenario_examples.csv')} GROUP BY 1 ORDER BY 2 DESC").fetchall()
    scen["severity_dist"] = con.execute(f"SELECT severity, count(*) FROM {c('scenario_examples.csv')} GROUP BY 1 ORDER BY 1").fetchall()
    scen["duration_avg_min"] = con.execute(f"SELECT avg(date_diff('minute', start_time, end_time)) FROM {c('scenario_examples.csv')}").fetchone()[0]
    # does scenario target_segment+time exactly match a row in incidents_train?
    exact_match = con.execute(f'''
        SELECT count(*) FROM {c('scenario_examples.csv')} s
        JOIN {c('incidents_train.csv')} i
          ON s.target_segment = i.segment_id AND s.start_time = i.start_time AND s.end_time = i.end_time
             AND s.incident_type = i.incident_type
    ''').fetchone()[0]
    scen["exact_match_to_incidents_train"] = exact_match
    scen["total_scenarios"] = scen["n"]
    # scenario target_segment structural_bottleneck flag
    bott_overlap = con.execute(f'''
        SELECT count(*) FROM {c('scenario_examples.csv')} s
        JOIN {c('network.csv')} n ON s.target_segment = n.segment_id
        WHERE n.structural_bottleneck = 1
    ''').fetchone()[0]
    scen["scenarios_on_structural_bottleneck_segment"] = bott_overlap
    # direct planning candidate available for target_segment?
    direct_candidate = con.execute(f'''
        SELECT count(DISTINCT s.scenario_id) FROM {c('scenario_examples.csv')} s
        JOIN {c('planning_candidates.csv')} p ON s.target_segment = p.target_segment
    ''').fetchone()[0]
    scen["scenarios_with_direct_planning_candidate"] = direct_candidate
    out["scenario_audit"] = scen

    # ---------------- Network graph audit ----------------
    net = {}
    net["segments"] = con.execute(f"SELECT count(*) FROM {c('network.csv')}").fetchone()[0]
    net["nodes"] = con.execute(f"SELECT count(*) FROM {c('nodes.csv')}").fetchone()[0]
    net["self_loops"] = con.execute(f"SELECT count(*) FROM {c('network.csv')} WHERE source_node = target_node").fetchone()[0]
    net["duplicate_directed_edges"] = con.execute(f'''
        SELECT count(*) - count(DISTINCT (source_node, target_node)) FROM {c('network.csv')}
    ''').fetchone()[0]
    # reciprocal pairs (A->B and B->A both exist)
    net["reciprocal_edge_pairs"] = con.execute(f'''
        SELECT count(*) FROM {c('network.csv')} a
        JOIN {c('network.csv')} b ON a.source_node = b.target_node AND a.target_node = b.source_node
    ''').fetchone()[0] // 2
    net["road_class_dist"] = con.execute(f"SELECT road_class, count(*) FROM {c('network.csv')} GROUP BY 1").fetchall()
    net["lanes_dist"] = con.execute(f"SELECT lanes, count(*) FROM {c('network.csv')} GROUP BY 1 ORDER BY 1").fetchall()
    for col in ["free_flow_speed_kmh", "capacity_vph", "length_km", "grade_pct", "importance", "peak_capacity_factor"]:
        net[col] = numeric_stats(con, "network.csv", col)
    # degree distribution
    net["out_degree_dist"] = con.execute(f'''
        SELECT out_deg, count(*) FROM (
            SELECT source_node, count(*) AS out_deg FROM {c('network.csv')} GROUP BY 1
        ) GROUP BY 1 ORDER BY 1
    ''').fetchall()
    net["in_degree_dist"] = con.execute(f'''
        SELECT in_deg, count(*) FROM (
            SELECT target_node, count(*) AS in_deg FROM {c('network.csv')} GROUP BY 1
        ) GROUP BY 1 ORDER BY 1
    ''').fetchall()
    # nodes with zero degree (isolated)
    net["isolated_nodes"] = con.execute(f'''
        SELECT count(*) FROM {c('nodes.csv')} n
        WHERE n.node_id NOT IN (SELECT source_node FROM {c('network.csv')})
          AND n.node_id NOT IN (SELECT target_node FROM {c('network.csv')})
    ''').fetchone()[0]
    out["network_audit"] = net

    # ---------------- Structural bottleneck audit ----------------
    bott = {}
    bott["count"] = con.execute(f"SELECT count(*) FROM {c('network.csv')} WHERE structural_bottleneck = 1").fetchone()[0]
    bott["road_class_dist"] = con.execute(f"SELECT road_class, count(*) FROM {c('network.csv')} WHERE structural_bottleneck=1 GROUP BY 1").fetchall()
    bott["capacity_stats"] = con.execute(f'''
        SELECT avg(capacity_vph), min(capacity_vph), max(capacity_vph) FROM {c('network.csv')} WHERE structural_bottleneck=1
    ''').fetchone()
    bott["importance_stats"] = con.execute(f'''
        SELECT avg(importance), min(importance), max(importance) FROM {c('network.csv')} WHERE structural_bottleneck=1
    ''').fetchone()
    bott["non_bottleneck_importance_avg"] = con.execute(f'''
        SELECT avg(importance) FROM {c('network.csv')} WHERE structural_bottleneck=0
    ''').fetchone()[0]
    # avg congestion_index for bottleneck vs non-bottleneck segments (train)
    bott["avg_congestion_index_bottleneck_train"] = con.execute(f'''
        SELECT avg(t.congestion_index) FROM {c('traffic_train.csv')} t
        JOIN {c('network.csv')} n ON t.segment_id = n.segment_id
        WHERE n.structural_bottleneck = 1
    ''').fetchone()[0]
    bott["avg_congestion_index_nonbottleneck_train"] = con.execute(f'''
        SELECT avg(t.congestion_index) FROM {c('traffic_train.csv')} t
        JOIN {c('network.csv')} n ON t.segment_id = n.segment_id
        WHERE n.structural_bottleneck = 0
    ''').fetchone()[0]
    # planning candidates targeting bottleneck segments directly
    bott["bottleneck_segments_with_direct_candidate"] = con.execute(f'''
        SELECT count(DISTINCT n.segment_id) FROM {c('network.csv')} n
        JOIN {c('planning_candidates.csv')} p ON n.segment_id = p.target_segment
        WHERE n.structural_bottleneck = 1
    ''').fetchone()[0]
    out["bottleneck_audit"] = bott

    # ---------------- Signal plan audit ----------------
    sig = {}
    sig["n_signal_plans"] = con.execute(f"SELECT count(*) FROM {c('signal_plans.csv')}").fetchone()[0]
    sig["n_signalized_segments"] = con.execute(f"SELECT count(*) FROM {c('network.csv')} WHERE signal_id IS NOT NULL").fetchone()[0]
    sig["n_distinct_signal_ids_in_network"] = con.execute(f"SELECT count(DISTINCT signal_id) FROM {c('network.csv')} WHERE signal_id IS NOT NULL").fetchone()[0]
    sig["signal_plans_unused_by_network"] = con.execute(f'''
        SELECT count(*) FROM {c('signal_plans.csv')} sp
        WHERE sp.signal_id NOT IN (SELECT signal_id FROM {c('network.csv')} WHERE signal_id IS NOT NULL)
    ''').fetchone()[0]
    for col in ["cycle_s", "green_ratio", "offset_s"]:
        sig[col] = numeric_stats(con, "signal_plans.csv", col)
    out["signal_audit"] = sig

    # ---------------- Turn restriction audit ----------------
    tr = {}
    tr["n"] = con.execute(f"SELECT count(*) FROM {c('turn_restrictions.csv')}").fetchone()[0]
    tr["types"] = con.execute(f"SELECT restriction, count(*) FROM {c('turn_restrictions.csv')} GROUP BY 1").fetchall()
    tr["from_segment_orphans"] = con.execute(f'''
        SELECT count(*) FROM {c('turn_restrictions.csv')} WHERE from_segment NOT IN (SELECT segment_id FROM {c('network.csv')})
    ''').fetchone()[0]
    tr["to_segment_orphans"] = con.execute(f'''
        SELECT count(*) FROM {c('turn_restrictions.csv')} WHERE to_segment NOT IN (SELECT segment_id FROM {c('network.csv')})
    ''').fetchone()[0]
    out["turn_restriction_audit"] = tr

    # ---------------- OD demand audit ----------------
    od = {}
    od["n_pairs"] = con.execute(f"SELECT count(*) FROM {c('od_demand_profiles.csv')}").fetchone()[0]
    od["distinct_origins"] = con.execute(f"SELECT count(DISTINCT origin_node) FROM {c('od_demand_profiles.csv')}").fetchone()[0]
    od["distinct_destinations"] = con.execute(f"SELECT count(DISTINCT destination_node) FROM {c('od_demand_profiles.csv')}").fetchone()[0]
    od["total_demand_vph"] = con.execute(f"SELECT sum(base_demand_vph) FROM {c('od_demand_profiles.csv')}").fetchone()[0]
    od["purpose_dist"] = con.execute(f"SELECT purpose, count(*), sum(base_demand_vph) FROM {c('od_demand_profiles.csv')} GROUP BY 1 ORDER BY 2 DESC").fetchall()
    od["demand_stats"] = numeric_stats(con, "od_demand_profiles.csv", "base_demand_vph")
    od["self_pairs (origin=destination)"] = con.execute(f"SELECT count(*) FROM {c('od_demand_profiles.csv')} WHERE origin_node = destination_node").fetchone()[0]
    out["od_audit"] = od

    # ---------------- Planning candidate audit ----------------
    pc = {}
    pc["n"] = con.execute(f"SELECT count(*) FROM {c('planning_candidates.csv')}").fetchone()[0]
    pc["intervention_type_dist"] = con.execute(f"SELECT intervention_type, count(*) FROM {c('planning_candidates.csv')} GROUP BY 1 ORDER BY 2 DESC").fetchall()
    pc["feasibility_band_dist"] = con.execute(f"SELECT feasibility_band, count(*) FROM {c('planning_candidates.csv')} GROUP BY 1").fetchall()
    pc["capacity_delta_stats"] = numeric_stats(con, "planning_candidates.csv", "capacity_delta_vph")
    pc["cost_index_stats"] = numeric_stats(con, "planning_candidates.csv", "cost_index")
    pc["distinct_target_segments"] = con.execute(f"SELECT count(DISTINCT target_segment) FROM {c('planning_candidates.csv')}").fetchone()[0]
    pc["segments_with_multiple_candidates"] = con.execute(f'''
        SELECT count(*) FROM (SELECT target_segment FROM {c('planning_candidates.csv')} GROUP BY 1 HAVING count(*) > 1)
    ''').fetchone()[0]
    out["planning_candidate_audit"] = pc

    # ---------------- Context / roadwork audit ----------------
    ctx = {}
    for split, f in [("train", "context_train.csv"), ("validation", "context_validation.csv")]:
        ctx[split] = {
            "temperature": numeric_stats(con, f, "temperature_c"),
            "rain_intensity": numeric_stats(con, f, "rain_intensity"),
            "holiday_flag_dist": con.execute(f"SELECT holiday_flag, count(*) FROM {c(f)} GROUP BY 1").fetchall(),
            "event_level_dist": con.execute(f"SELECT event_level, count(*) FROM {c(f)} GROUP BY 1 ORDER BY 1").fetchall(),
            "distinct_event_ids": con.execute(f"SELECT count(DISTINCT event_id) FROM {c(f)} WHERE event_id IS NOT NULL").fetchone()[0],
        }
    rw = {}
    for split, f in [("train", "roadworks_train.csv"), ("validation", "roadworks_validation.csv")]:
        rw[split] = {
            "n": con.execute(f"SELECT count(*) FROM {c(f)}").fetchone()[0],
            "types": con.execute(f"SELECT work_type, count(*) FROM {c(f)} GROUP BY 1").fetchall(),
            "closure_fraction_stats": numeric_stats(con, f, "closure_fraction"),
            "duration_min_avg": con.execute(f"SELECT avg(date_diff('minute', start_time, end_time)) FROM {c(f)}").fetchone()[0],
        }
    out["context_audit"] = ctx
    out["roadwork_audit"] = rw

    META.mkdir(parents=True, exist_ok=True)
    (META / "dataset_intelligence.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"Wrote {META / 'dataset_intelligence.json'}")
    print(json.dumps(out, indent=2, default=str)[:2000] + "\n... (truncated stdout; full output in file)")


if __name__ == "__main__":
    main()
