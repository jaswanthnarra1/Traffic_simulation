"""Build network artifacts: GeoJSON overlay + graph summary (connectivity, degrees, legal turns)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config as C, graph  # noqa: E402

(C.META / "network.geojson").write_text(json.dumps(graph.geojson()))
s = graph.summary()
s["legal_turns_peak"] = len(graph.turn_pairs("peak"))
s["legal_turns_offpeak"] = len(graph.turn_pairs("offpeak"))
(C.META / "network_summary.json").write_text(json.dumps(s, indent=2))
print(json.dumps(s, indent=2))
