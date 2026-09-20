"""Road network graph.

Two views:
- node graph (NetworkX DiGraph): nodes = intersections, edges = segments. Used for GeoJSON and connectivity.
- line graph: nodes = segments, edge a->b when a.target == b.source and the turn (a -> b) is legal.
  Turn restrictions are transition constraints, so they live on line-graph edges — a restricted turn
  never removes a whole segment.
"""
from functools import lru_cache

import networkx as nx
import numpy as np
import pandas as pd

from . import config as C
from .data import network, table


@lru_cache(maxsize=1)
def node_graph() -> nx.DiGraph:
    g = nx.DiGraph()
    for r in table("nodes").itertuples():
        g.add_node(r.node_id, lat=r.lat, lon=r.lon, x=r.x, y=r.y)
    for r in network().itertuples():
        g.add_edge(r.source_node, r.target_node, segment_id=r.segment_id)
    return g


@lru_cache(maxsize=1)
def seg_index() -> dict:
    return {s: i for i, s in enumerate(network().segment_id)}


@lru_cache(maxsize=1)
def reverse_of() -> dict:
    net = network()
    key = {(r.source_node, r.target_node): r.segment_id for r in net.itertuples()}
    return {r.segment_id: key.get((r.target_node, r.source_node)) for r in net.itertuples()}


def is_restricted(frm: str, to: str, hour: int | None) -> bool:
    """[DESIGN] `time_window` rows carry no times in the dataset [UNK]; enforced during peak hours only.
    hour=None means 'always enforce' (conservative)."""
    for r in _restrictions().get((frm, to), []):
        if r in ("no_turn", "no_left"):
            return True
        if r == "time_window" and (hour is None or hour in C.PEAK_HOURS):
            return True
    return False


@lru_cache(maxsize=1)
def _restrictions() -> dict:
    out = {}
    for r in table("turn_restrictions").itertuples():
        out.setdefault((r.from_segment, r.to_segment), []).append(r.restriction)
    return out


@lru_cache(maxsize=None)
def turn_pairs(hour_bucket: str) -> list:
    """All legal (from_idx, to_idx) segment transitions. hour_bucket: 'peak' | 'offpeak'."""
    hour = C.PEAK_HOURS[0] if hour_bucket == "peak" else 3
    net = network()
    idx = seg_index()
    by_src = net.groupby("source_node").segment_id.apply(list).to_dict()
    pairs = []
    for r in net.itertuples():
        for b in by_src.get(r.target_node, []):
            if not is_restricted(r.segment_id, b, hour):
                pairs.append((idx[r.segment_id], idx[b]))
    return pairs


@lru_cache(maxsize=1)
def neighbors() -> dict:
    """segment -> {'up': [...], 'down': [...]} excluding the reverse (U-turn) twin."""
    net = network()
    rev = reverse_of()
    by_tgt = net.groupby("target_node").segment_id.apply(list).to_dict()
    by_src = net.groupby("source_node").segment_id.apply(list).to_dict()
    out = {}
    for r in net.itertuples():
        out[r.segment_id] = {
            "up": [u for u in by_tgt.get(r.source_node, []) if u != rev[r.segment_id]],
            "down": [d for d in by_src.get(r.target_node, []) if d != rev[r.segment_id]],
        }
    return out


@lru_cache(maxsize=1)
def neighbor_matrices():
    """Row-normalised (S x S) upstream / downstream adjacency for vectorised neighbor features."""
    idx = seg_index()
    S = len(idx)
    up, dn = np.zeros((S, S), "float32"), np.zeros((S, S), "float32")
    for s, nb in neighbors().items():
        for u in nb["up"]:
            up[idx[s], idx[u]] = 1
        for d in nb["down"]:
            dn[idx[s], idx[d]] = 1
    up /= np.maximum(up.sum(1, keepdims=True), 1)
    dn /= np.maximum(dn.sum(1, keepdims=True), 1)
    return up, dn


def hops_from(segment: str, max_hops: int, direction: str = "both") -> dict:
    """BFS over segment adjacency. Returns {segment: (hop, direction)}."""
    nb = neighbors()
    seen = {segment: (0, "source")}
    frontier = [(segment, d) for d in (("up", "down") if direction == "both" else (direction,))]
    for hop in range(1, max_hops + 1):
        nxt = []
        for s, d in frontier:
            for n in nb[s][d]:
                if n not in seen:
                    seen[n] = (hop, "upstream" if d == "up" else "downstream")
                    nxt.append((n, d))
        frontier = nxt
    return seen


def legal_detour(blocked: str, hour: int | None = None, weights: dict | None = None):
    """Shortest legal path from the blocked segment's source node to its target node that avoids it.
    Search runs on the line graph, so every consecutive transition is checked against turn restrictions.
    Returns list of segment ids or None."""
    net = network().set_index("segment_id")
    src, dst = net.at[blocked, "source_node"], net.at[blocked, "target_node"]
    g = nx.DiGraph()
    w = weights or (net["length_km"] / net["free_flow_speed_kmh"]).to_dict()
    starts = [s for s in net.index[net.source_node == src] if s != blocked]
    by_src = net.reset_index().groupby("source_node").segment_id.apply(list).to_dict()
    for a in net.index:
        if a == blocked:
            continue
        for b in by_src.get(net.at[a, "target_node"], []):
            if b != blocked and not is_restricted(a, b, hour):
                g.add_edge(a, b, weight=w[b])
    g.add_node("SRC"), g.add_node("DST")
    for s in starts:
        g.add_edge("SRC", s, weight=w[s])
    for s in net.index[net.target_node == dst]:
        if s != blocked:
            g.add_edge(s, "DST", weight=0.0)
    try:
        path = nx.shortest_path(g, "SRC", "DST", weight="weight")[1:-1]
    except nx.NetworkXNoPath:
        return None
    # a detour that simply U-turns straight back is not a detour
    return path if path else None


def path_is_legal(path: list, hour: int | None = None) -> bool:
    net = network().set_index("segment_id")
    for a, b in zip(path, path[1:]):
        if net.at[a, "target_node"] != net.at[b, "source_node"] or is_restricted(a, b, hour):
            return False
    return True


@lru_cache(maxsize=1)
def geojson() -> dict:
    """Segments as LineStrings, offset sideways so the two directions of a road don't overlap on the map."""
    nodes = table("nodes").set_index("node_id")
    feats = []
    for r in network().itertuples():
        a, b = nodes.loc[r.source_node], nodes.loc[r.target_node]
        dx, dy = b.lon - a.lon, b.lat - a.lat
        n = np.hypot(dx, dy) or 1
        ox, oy = -dy / n * 0.0012, dx / n * 0.0012  # right-hand offset (~130 m)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[a.lon + ox, a.lat + oy], [b.lon + ox, b.lat + oy]]},
            "properties": {"segment_id": r.segment_id, "road_class": r.road_class, "lanes": int(r.lanes),
                           "source_node": r.source_node, "target_node": r.target_node},
        })
    return {"type": "FeatureCollection", "features": feats}


def summary() -> dict:
    g = node_graph()
    deg = pd.Series(dict(g.out_degree())).value_counts().sort_index()
    return {"nodes": g.number_of_nodes(), "segments": g.number_of_edges(),
            "strongly_connected": nx.is_strongly_connected(g),
            "out_degree_distribution": {int(k): int(v) for k, v in deg.items()},
            "turn_restrictions": int(len(table("turn_restrictions")))}
