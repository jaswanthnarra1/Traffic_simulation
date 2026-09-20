"""Paths and every tunable constant in one place. Tags: [DESIGN] = our choice, [INF] = inference."""
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent

# Load the repo-root .env (stdlib only). Real environment variables win; blank values are ignored.
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _k, _sep, _v = _line.partition("=")
        if _sep and not _k.strip().startswith("#") and _v.strip():
            os.environ.setdefault(_k.strip(), _v.strip())


def _path(var: str, default: Path) -> Path:
    """Relative paths in .env are relative to the repo root."""
    v = os.getenv(var)
    return (ROOT / v if not Path(v).is_absolute() else Path(v)) if v else default


DATA_DIR = _path("DATA_DIR", BACKEND / "data")
RAW = DATA_DIR / "raw"
DB_PATH = _path("NEURAX_DB_PATH", DATA_DIR / "db" / "neurax.duckdb")
MODEL_DIR = _path("MODEL_DIR", BACKEND / "artifacts")
MODELS = MODEL_DIR / "models"
METRICS = MODEL_DIR / "metrics"
META = MODEL_DIR / "metadata"
FEATURE_CACHE = DATA_DIR / "features"
for _p in (MODELS, METRICS, META):
    _p.mkdir(parents=True, exist_ok=True)

MODEL_VERSION = "v1"
STEP_MIN = 5
SLOTS_PER_DAY = 288
TRAIN_START, TRAIN_END = "2026-01-01 00:00:00", "2026-01-15 23:55:00"
VAL_START, VAL_END = "2026-01-16 00:00:00", "2026-01-19 23:55:00"
# Organizer hidden test input (unlabelled, deliberately noisy), present only after scripts/import_test_data.py.
# Analysis-only: nothing is ever fitted on it — full_panel() stops at VAL_END.
TEST_START, TEST_END = "2026-01-20 00:00:00", "2026-01-27 23:55:00"
FIT_END = "2026-01-12 23:55:00"          # forecast models fit on days 1-12 ...
CALIB_START = "2026-01-13 00:00:00"      # ... residual bands from days 13-15 [DESIGN]
DEMO_TIME = os.getenv("DEMO_TIME", "2026-01-16 13:30:00")  # validation period (held out) [DESIGN]

HORIZONS = {15: 3, 30: 6, 45: 9, 60: 12}  # minutes -> 5-min steps
METRICS_FC = {"speed": "speed_kmh", "flow": "flow_vph", "congestion": "congestion_index"}

# [FACT] hourly profile: congestion elevated 07-09 and 16-19 (RESEARCH / hourly query)
PEAK_HOURS = (7, 8, 9, 16, 17, 18, 19)

# Traffic state thresholds on speed / free-flow speed [DESIGN]
STATE_THRESHOLDS = [("NORMAL", 0.85), ("MODERATE", 0.70), ("HEAVY", 0.50), ("CRITICAL", 0.0)]

# Anomaly detector [DESIGN] — floors stop tiny night-time MADs from exploding z-scores
SR_MAD_FLOOR = 0.02
FLOW_MAD_FLOOR = 0.05
ANOMALY_WEIGHTS = {"speed": 0.8, "flow": 0.2}

# Diagnosis [DESIGN]
RAIN_EFFECT = 0.3            # [FACT] rain is 0 or >=0.3 in this dataset
NETWORK_SHIFT_EFFECT = 0.03  # network-wide median speed-ratio drop that counts as a context effect
TYPE_MIN_LIKELIHOOD = 0.5

# Bottleneck detector [DESIGN] — robust z of peak/off-peak throughput amplification
BOTTLENECK_Z = -2.5

# Propagation [DESIGN defaults; decay/lag overwritten by calibration from train incidents]
PROP_MAX_HOPS = 3
PROP_RISK_LEVELS = [("HIGH", 0.5), ("MEDIUM", 0.25), ("LOW", 0.1)]

# Simulation [DESIGN]
BPR_ALPHA, BPR_BETA = 0.15, 4.0
MSA_ITERS = 30
MAX_CAPACITY_REDUCTION = 0.9  # a closure keeps 10% so the network stays valid; diversion covers the rest
SEVERITY_CAPACITY_REDUCTION = {"CRITICAL": 0.5, "HEAVY": 0.33, "MODERATE": 0.2, "NORMAL": 0.0}

# Recommendation ranking [DESIGN]. score = sum(weight x component) - feasibility penalty.
# Components: network delay reduction (% of total veh-h), local delay reduction (% on segment + 1-hop
# neighbors), worsened segments (% of network), cost_index normalised to 0-1 by the max in the file.
# Network-wide % changes from one segment are small (~0.1-1%), local ones larger: weights put both
# on a comparable scale.
SCORE_WEIGHTS = {
    "network_delay_reduction_pct": 2.0,
    "target_delay_reduction_pct": 0.1,
    "worsened_links_pct": 0.1,
    "cost_index": 1.0,
}
FEASIBILITY_PENALTY = {"high": 0.0, "medium": 0.5, "low": 1.5}
MAX_CANDIDATE_HOPS = 2
MAX_CANDIDATES_SIMULATED = 6

CORS_ALLOWED_ORIGINS = [o for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o]

# ---- public user portal ---------------------------------------------------------------------
# The rider portal replays the FlowSense dataset as *simulated* traffic. The server owns the clock:
# users pick a replay step, never an arbitrary timestamp (bounds snapshot computation for public callers).
USER_REPLAY_BACK_MIN = 30      # replay starts 30 min before DEMO_TIME so an incident can be watched forming and clearing
USER_REPLAY_STEPS = 24         # 24 x 5 min = 2 hours
USER_DEFAULT_STEP = 6          # = DEMO_TIME
# Requests outside this box are rejected (the traffic simulation only covers the Hyderabad area; also limits proxy abuse)
SERVICE_AREA = {"south": 17.10, "west": 78.10, "north": 17.70, "east": 78.80}

GEOCODING_PROVIDER = "nominatim"   # only adapter implemented; providers.py is the seam to swap it
GEOCODING_URL = (os.getenv("GEOCODING_URL") or "https://nominatim.openstreetmap.org").rstrip("/")
NOMINATIM_USER_AGENT = os.getenv("NOMINATIM_USER_AGENT") or "FlowSenseAI/1.0 (hackathon demo)"   # Nominatim policy requires an identifying UA
OPENROUTESERVICE_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY", "")
OPENROUTESERVICE_URL = (os.getenv("OPENROUTESERVICE_URL") or "https://api.openrouteservice.org").rstrip("/")
OSRM_URL = (os.getenv("OSRM_URL") or "https://router.project-osrm.org").rstrip("/")
_ROUTING = (os.getenv("ROUTING_PROVIDER") or "auto").lower()
# auto = OpenRouteService when a key is configured, otherwise the keyless OSRM demo server
ROUTING_PROVIDER = "openrouteservice" if (_ROUTING == "openrouteservice" or (_ROUTING == "auto" and OPENROUTESERVICE_API_KEY)) else "osrm"
OVERPASS_URLS = [u.strip() for u in (os.getenv("OVERPASS_URL") or
                 "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter").split(",") if u.strip()]
