"""Runtime orchestration: everything the system knows at simulation time t, computed from data <= t only."""
from functools import lru_cache

import numpy as np
import pandas as pd

from . import config as C, data, detection as D, forecasting, propagation
from .detection import evidence

HISTORY_SLOTS = 300  # >= 1 day + 1 hour: enough for the 'same slot yesterday' and 60-min features


@lru_cache(maxsize=1)
def _panel():
    # runtime_panel, not full_panel: analysis may run over the hidden test input, fitting never does.
    return data.runtime_panel()


def window(t) -> data.Panel:
    """Panel slice ending exactly at t. Nothing after t is ever included (RULE 2)."""
    t = data.floor_time(t)
    start = max(pd.Timestamp(C.TRAIN_START), t - pd.Timedelta(minutes=C.STEP_MIN * (HISTORY_SLOTS - 1)))
    return data.slice_panel(_panel(), start, t)


class Snapshot:
    def __init__(self, t):
        self.t = data.floor_time(t)
        p = self.p = window(self.t)
        self.ti = len(p.times) - 1
        self.meta = D.detector_meta()
        self.thr = self.meta["threshold"]
        self.c = D.components(p, D.load_baseline())
        self.score = D.score_variant(self.c, self.meta["variant"])
        self.flags = self.score >= self.thr
        self.quality = data.quality_score(p, self.ti)
        self.recurring = D.recurring_set()
        net = data.network()
        self.net = net.set_index("segment_id")
        self.idx = {s: i for i, s in enumerate(p.segs)}
        ti = self.ti
        sr = self.c["sr"][:, ti]
        self.state = D.state_of(sr)
        self.conf = D.confidence(self.score[:, ti], self.thr, self.quality)
        ctx = data.context().reindex([self.t]).iloc[0]
        self.context = {"temperature_c": float(ctx.temperature_c), "rain_intensity": float(ctx.rain_intensity),
                        "event_present": bool(ctx.event_level > 0), "holiday": bool(ctx.holiday_flag),
                        "network_shift": float(self.c["net_shift"][ti])}
        self.forecast = forecasting.predict(p, [ti]) if forecasting.models_available() else None
        v = p.v
        fft = (net.length_km / net.free_flow_speed_kmh * 60).to_numpy()
        self.rows = pd.DataFrame({
            "segment_id": p.segs, "state": self.state,
            "speed_kmh": v["speed_kmh"][:, ti], "flow_vph": v["flow_vph"][:, ti], "occupancy_pct": v["occupancy_pct"][:, ti],
            "travel_time_min": fft / np.where(sr > 0, sr, np.nan), "free_flow_time_min": fft,
            "delay_min": v["delay_min"][:, ti], "queue_veh": v["queue_length_veh"][:, ti],
            "congestion_index": v["congestion_index"][:, ti], "capacity_utilization": self.c["fr"][:, ti],
            "speed_ratio": sr, "anomaly_score": self.score[:, ti], "anomalous": self.flags[:, ti],
            "confidence": self.conf, "data_quality": self.quality,
            "road_class": net.road_class.to_numpy(), "signalized": net.signal_id.notna().to_numpy(),
            "recurring_bottleneck_detected": [s in self.recurring for s in p.segs],
        })

    def row(self, seg) -> dict:
        r = self.rows.iloc[self.idx[seg]].to_dict()
        return {k: (None if isinstance(v, float) and not np.isfinite(v) else (v.item() if hasattr(v, "item") else v))
                for k, v in r.items()}

    def diagnosis(self, seg) -> dict:
        si = self.idx[seg]
        dg = D.diagnose(seg, si, self.ti, self.p, self.c, self.score, self.thr, self.recurring, self.t)
        if self.flags[si, self.ti]:
            rs = self.p.times.get_loc(pd.Timestamp(dg["run_start"]))
            dg["incident_type"] = D.type_likelihood(self.c, si, rs, self.ti, self.p)
            dg["incident_type"]["validation_note"] = ("type likelihood is weak evidence: on held-out validation it did not beat "
                                                      "chance (see Evaluation) — severity, not type, drives the traffic signature")
        else:
            dg["incident_type"] = None
        dg["detected"] = bool(self.flags[si, self.ti])
        dg["confidence"] = round(float(self.conf[si]), 3)
        dg["confidence_label"] = "HIGH" if self.conf[si] >= 0.8 else "MEDIUM" if self.conf[si] >= 0.5 else "LOW"
        return dg

    def evidence(self, seg) -> list:
        si, ti = self.idx[seg], self.ti
        c, n = self.c, self.net.loc[seg]
        ff = float(n.free_flow_speed_kmh)
        prev = max(0, ti - 3)
        q_now, q_prev = self.p.v["queue_length_veh"][si, ti], self.p.v["queue_length_veh"][si, prev]
        rw = data.active_roadworks(self.t)
        rw = rw[rw.segment_id == seg]
        return [
            evidence("speed", self.p.v["speed_kmh"][si, ti], c["base_sr"][si, ti] * ff, unit="km/h",
                     note="baseline = median for this segment, hour and day type (training data)"),
            evidence("local_speed_ratio_drop", c["local_drop"][si, ti], None, self.thr,
                     note="drop vs. baseline after removing the network-wide shift; detector threshold shown"),
            evidence("flow", self.p.v["flow_vph"][si, ti], c["base_fr"][si, ti] * float(n.capacity_vph), unit="veh/h"),
            evidence("congestion_index", self.p.v["congestion_index"][si, ti], 1 - c["base_sr"][si, ti]),
            evidence("queue_change_15min", q_now - q_prev, None, unit="veh"),
            evidence("largest_neighbor_drop", c["neighbor_max_drop"][si, ti], None,
                     note="epicentre rule: a segment is flagged only if its drop >= every 1-hop neighbor's"),
            evidence("network_wide_shift", self.context["network_shift"], None, C.NETWORK_SHIFT_EFFECT, source="traffic (all segments)"),
            evidence("rain_intensity", self.context["rain_intensity"], None, C.RAIN_EFFECT, source="context"),
            evidence("event_present", float(self.context["event_present"]), None, source="context"),
            evidence("roadwork_active", float(len(rw) > 0), None, source="roadworks (start <= t < end)",
                     note=(rw.iloc[0].work_type if len(rw) else None)),
            evidence("data_quality", self.quality[si], None, source="sanitizer",
                     note="share of accepted original readings in the last hour"),
        ]

    def forecast_for(self, seg) -> dict:
        if self.forecast is None:
            return None
        si = self.idx[seg]
        out = {}
        for metric, col in C.METRICS_FC.items():
            pts = [{"horizon_min": 0, "p50": _f(self.p.v[col][si, self.ti]), "p10": _f(self.p.v[col][si, self.ti]),
                    "p90": _f(self.p.v[col][si, self.ti])}]
            for hmin in C.HORIZONS:
                r = self.forecast[(metric, hmin)]
                pts.append({"horizon_min": hmin, "p50": _f(r["p50"][si]), "p10": _f(r["p10"][si]), "p90": _f(r["p90"][si])})
            lo = max(0, self.ti - 24)
            hist = [{"time": str(self.p.times[i]), "value": _f(self.p.v[col][si, i])} for i in range(lo, self.ti + 1)]
            out[metric] = {"history": hist, "forecast": pts}
        return out

    def propagation_for(self, seg, horizon=None) -> list:
        si = self.idx[seg]
        vc = dict(zip(self.p.segs, np.nan_to_num(self.c["fr"][:, self.ti], nan=0.5).tolist()))
        drop = float(max(0.0, np.nan_to_num(self.c["local_drop"][si, self.ti])))
        return propagation.propagate(seg, drop, vc, horizon)

    def events(self, lookback_slots=36) -> list:
        """Detected events (contiguous flagged runs) in the last 3 hours up to t."""
        lo = max(0, self.ti - lookback_slots + 1)
        flags = np.zeros_like(self.flags)
        flags[:, lo:] = self.flags[:, lo:]
        ev = D.events_from_flags(flags, self.score, self.p)
        out = []
        for e in ev.itertuples():
            ongoing = e.en == self.ti
            dg = self.diagnosis(e.segment_id) if ongoing else None
            out.append({"segment_id": e.segment_id, "start": str(e.start), "last_flagged": str(e.end),
                        "duration_min": e.slots * C.STEP_MIN, "ongoing": bool(ongoing), "peak_score": round(e.peak_score, 3),
                        "state": self.state[e.s] if ongoing else None,
                        "cause": dg["cause"] if dg else "RESOLVED",
                        "confidence": dg["confidence"] if dg else None,
                        "confidence_label": dg["confidence_label"] if dg else None,
                        "likely_type": (dg["incident_type"] or {}).get("type") if dg else None})
        return sorted(out, key=lambda r: (not r["ongoing"], -r["peak_score"]))


def _f(x):
    x = float(x)
    return round(x, 4) if np.isfinite(x) else None


@lru_cache(maxsize=24)
def snapshot(t) -> Snapshot:
    return Snapshot(t)


def get(t=None) -> Snapshot:
    return snapshot(data.floor_time(t or C.DEMO_TIME))
