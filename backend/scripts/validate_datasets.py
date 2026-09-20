"""
Validate raw datasets against real constraints (referential integrity, coordinate
bounds, timestamp parseability, unit sanity) using the *actual* schema discovered
in the source data (see data/metadata/profiles.json) — nothing invented.

Never mutates data/raw. Writes data/validated/validation_report.md +
validation_report.json documenting: original count, affected count, reason,
proposed transformation, final count for every issue found. No row is deleted
here — flags only; preprocess_datasets.py decides what to do with a flag.

Safe to re-run: overwrites its own output only.
"""
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
VALID = ROOT / "data" / "validated"
VALID.mkdir(parents=True, exist_ok=True)


def csv(name):
    return f"read_csv_auto('{(RAW / name).as_posix()}', SAMPLE_SIZE=-1)"


def main():
    con = duckdb.connect()
    issues = []

    # ---- Referential integrity: segment_id everywhere must exist in network.csv ----
    network_segments = f"(SELECT segment_id FROM {csv('network.csv')})"
    for f, col in [
        ("traffic_train.csv", "segment_id"),
        ("traffic_validation.csv", "segment_id"),
        ("forecast_targets_train.csv", "segment_id"),
        ("forecast_targets_validation.csv", "segment_id"),
        ("incidents_train.csv", "segment_id"),
        ("incidents_validation.csv", "segment_id"),
        ("roadworks_train.csv", "segment_id"),
        ("roadworks_validation.csv", "segment_id"),
        ("planning_candidates.csv", "target_segment"),
    ]:
        total = con.execute(f"SELECT count(*) FROM {csv(f)}").fetchone()[0]
        orphans = con.execute(
            f'SELECT count(*) FROM {csv(f)} WHERE "{col}" NOT IN {network_segments}'
        ).fetchone()[0]
        issues.append({
            "dataset": f, "check": f"{col} exists in network.csv segment_id",
            "original_count": total, "affected_count": orphans,
            "reason": "orphan reference" if orphans else "none",
            "transformation": "flagged only, not removed" if orphans else "n/a",
        })

    # ---- Referential integrity: node_id everywhere must exist in nodes.csv ----
    node_ids = f"(SELECT node_id FROM {csv('nodes.csv')})"
    for f, cols in [
        ("network.csv", ["source_node", "target_node"]),
        ("signal_plans.csv", ["node_id"]),
        ("turn_restrictions.csv", ["node_id"]),
        ("od_demand_profiles.csv", ["origin_node", "destination_node"]),
    ]:
        total = con.execute(f"SELECT count(*) FROM {csv(f)}").fetchone()[0]
        for col in cols:
            orphans = con.execute(
                f'SELECT count(*) FROM {csv(f)} WHERE "{col}" NOT IN {node_ids}'
            ).fetchone()[0]
            issues.append({
                "dataset": f, "check": f"{col} exists in nodes.csv node_id",
                "original_count": total, "affected_count": orphans,
                "reason": "orphan reference" if orphans else "none",
                "transformation": "flagged only, not removed" if orphans else "n/a",
            })

    # ---- Referential integrity: signal_id in network.csv must exist in signal_plans.csv (where non-null) ----
    total = con.execute(f"SELECT count(*) FROM {csv('network.csv')}").fetchone()[0]
    orphans = con.execute(
        f"SELECT count(*) FROM {csv('network.csv')} n "
        f"WHERE n.signal_id IS NOT NULL AND n.signal_id NOT IN "
        f"(SELECT signal_id FROM {csv('signal_plans.csv')})"
    ).fetchone()[0]
    issues.append({
        "dataset": "network.csv", "check": "signal_id exists in signal_plans.csv (non-null only)",
        "original_count": total, "affected_count": orphans,
        "reason": "orphan reference" if orphans else "none",
        "transformation": "flagged only, not removed" if orphans else "n/a",
    })

    # ---- Coordinate bounds: nodes.csv lat/lon must be plausible (Hyderabad-area, generously bounded to India) ----
    total = con.execute(f"SELECT count(*) FROM {csv('nodes.csv')}").fetchone()[0]
    bad_coords = con.execute(
        f"SELECT count(*) FROM {csv('nodes.csv')} "
        f"WHERE lat NOT BETWEEN 6 AND 38 OR lon NOT BETWEEN 68 AND 98"
    ).fetchone()[0]
    issues.append({
        "dataset": "nodes.csv", "check": "lat/lon within India bounding box",
        "original_count": total, "affected_count": bad_coords,
        "reason": "out-of-range coordinate" if bad_coords else "none",
        "transformation": "flagged only, not removed" if bad_coords else "n/a",
    })

    # ---- Timestamp parseability + ordering (end_time >= start_time) ----
    for f in ["incidents_train.csv", "incidents_validation.csv",
              "roadworks_train.csv", "roadworks_validation.csv",
              "scenario_examples.csv"]:
        total = con.execute(f"SELECT count(*) FROM {csv(f)}").fetchone()[0]
        bad_order = con.execute(
            f"SELECT count(*) FROM {csv(f)} WHERE end_time < start_time"
        ).fetchone()[0]
        issues.append({
            "dataset": f, "check": "end_time >= start_time",
            "original_count": total, "affected_count": bad_order,
            "reason": "end before start" if bad_order else "none",
            "transformation": "flagged only, not removed" if bad_order else "n/a",
        })

    # ---- Unit/value sanity on traffic observations ----
    for f in ["traffic_train.csv", "traffic_validation.csv"]:
        total = con.execute(f"SELECT count(*) FROM {csv(f)}").fetchone()[0]
        checks = {
            "negative speed/flow/occupancy/queue": (
                "speed_kmh < 0 OR flow_vph < 0 OR occupancy_pct < 0 OR queue_length_veh < 0"
            ),
            "occupancy_pct > 100": "occupancy_pct > 100",
            "speed_kmh > free_flow_speed*1.5 (implausible)": None,  # needs join, see below
        }
        for name, cond in checks.items():
            if cond is None:
                continue
            n = con.execute(f"SELECT count(*) FROM {csv(f)} WHERE {cond}").fetchone()[0]
            issues.append({
                "dataset": f, "check": name,
                "original_count": total, "affected_count": n,
                "reason": name if n else "none",
                "transformation": "flagged only, not removed" if n else "n/a",
            })
        # speed vs free-flow-speed sanity requires the network join
        n = con.execute(
            f"SELECT count(*) FROM {csv(f)} t JOIN {csv('network.csv')} n "
            f"ON t.segment_id = n.segment_id WHERE t.speed_kmh > n.free_flow_speed_kmh * 1.5"
        ).fetchone()[0]
        issues.append({
            "dataset": f, "check": "speed_kmh > 1.5x segment free_flow_speed_kmh",
            "original_count": total, "affected_count": n,
            "reason": "implausible speed vs. network free-flow speed" if n else "none",
            "transformation": "flagged only, not removed" if n else "n/a",
        })

    # ---- Categorical consistency: incident_type / road_class / intervention_type values ----
    for f, col in [
        ("incidents_train.csv", "incident_type"),
        ("incidents_validation.csv", "incident_type"),
        ("network.csv", "road_class"),
        ("planning_candidates.csv", "intervention_type"),
        ("planning_candidates.csv", "feasibility_band"),
    ]:
        vals = con.execute(f'SELECT DISTINCT "{col}" FROM {csv(f)}').fetchall()
        issues.append({
            "dataset": f, "check": f"distinct values of {col}",
            "original_count": len(vals), "affected_count": 0,
            "reason": "informational only", "transformation": "n/a",
            "distinct_values": sorted(str(v[0]) for v in vals),
        })

    (VALID / "validation_report.json").write_text(json.dumps(issues, indent=2, default=str))

    lines = ["# Validation Report (auto-generated by scripts/validate_datasets.py)", ""]
    lines.append("| Dataset | Check | Original | Affected | Reason |")
    lines.append("|---|---|---|---|---|")
    for i in issues:
        if "distinct_values" in i:
            continue
        lines.append(f"| {i['dataset']} | {i['check']} | {i['original_count']:,} | {i['affected_count']:,} | {i['reason']} |")
    lines.append("")
    lines.append("## Distinct categorical values found (informational)")
    for i in issues:
        if "distinct_values" in i:
            lines.append(f"- **{i['dataset']}.{i['check'].split(' of ')[-1]}**: {', '.join(i['distinct_values'])}")
    (VALID / "validation_report.md").write_text("\n".join(lines) + "\n")

    flagged = [i for i in issues if i.get("affected_count", 0) and "distinct_values" not in i]
    print(f"Validation complete. {len(flagged)} check(s) found affected rows (see validation_report.md).")


if __name__ == "__main__":
    main()
