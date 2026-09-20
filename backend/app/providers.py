"""Third-party adapters behind the public user portal: geocoding, routing, nearby places.

The browser never talks to these services: it calls FlowSense, FlowSense calls the provider. That keeps API keys
server-side, lets every provider be swapped in one place, and lets us cache, throttle and validate.

Providers (all OpenStreetMap-based):
- geocoding: Nominatim (public instance: max 1 request/second, identifying User-Agent required; set GEOCODING_URL to a
  self-hosted instance for real traffic)
- routing:   OpenRouteService when OPENROUTESERVICE_API_KEY is set (alternative_routes: target_count <= 3, routes <= 100 km),
             otherwise the keyless OSRM demo server (demonstration use only, no SLA)
- places:    Overpass API (public instances are rate limited and sometimes slow; a mirror is tried on failure)
"""
import math
import threading
import time
from collections import OrderedDict

import httpx

from . import config as C

# ponytail: in-process TTL cache and 1 req/s throttle — enough for one server process. Behind several workers, use Redis.


class ProviderError(Exception):
    """A provider problem, already phrased for a rider. `status` is the HTTP status FlowSense answers with."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.message, self.status = message, status


def client() -> httpx.Client:
    """Factory seam: tests replace this with a client on httpx.MockTransport."""
    return httpx.Client(timeout=httpx.Timeout(25.0, connect=8.0), follow_redirects=True)


class _TTL:
    def __init__(self, ttl: float, size: int = 256):
        self.ttl, self.size, self._d, self._lock = ttl, size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] > time.monotonic():
                self._d.move_to_end(key)
                return hit[1]
            self._d.pop(key, None)
        return None

    def put(self, key, value):
        with self._lock:
            self._d[key] = (time.monotonic() + self.ttl, value)
            while len(self._d) > self.size:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()


geocode_cache, route_cache, places_cache = _TTL(600), _TTL(120), _TTL(600)
MIN_GEOCODE_INTERVAL = 1.05   # seconds between outgoing Nominatim calls (their usage policy)
_geo_lock, _geo_last = threading.Lock(), [0.0]


def _guard(action: str, verb: str = "is"):
    """Turn transport failures into rider-friendly errors."""
    class _Ctx:
        def __enter__(self): return self

        def __exit__(self, typ, exc, tb):
            if isinstance(exc, ProviderError):
                return False
            if isinstance(exc, httpx.TimeoutException):
                raise ProviderError(f"{action} {verb} taking too long. Please try again.", 504) from exc
            if isinstance(exc, httpx.HTTPError):
                raise ProviderError(f"{action} {verb} temporarily unavailable. Please try again shortly.", 503) from exc
            return False
    return _Ctx()


# ------------------------------------------------------------------ geocoding
def geocode(q: str, limit: int = 6) -> list[dict]:
    key = (q.strip().lower(), limit)
    if (hit := geocode_cache.get(key)) is not None:
        return hit
    a = C.SERVICE_AREA
    params = {"q": q, "format": "jsonv2", "limit": limit, "addressdetails": 1, "accept-language": "en", "countrycodes": "in",
              "viewbox": f"{a['west']},{a['north']},{a['east']},{a['south']}", "bounded": 1}
    with _guard("Search"):
        with _geo_lock:                                         # Nominatim policy: at most one request per second
            wait = MIN_GEOCODE_INTERVAL - (time.monotonic() - _geo_last[0])
            if wait > 0:
                time.sleep(wait)
            _geo_last[0] = time.monotonic()
        with client() as c:
            r = c.get(f"{C.GEOCODING_URL}/search", params=params, headers={"User-Agent": C.NOMINATIM_USER_AGENT})
        if r.status_code == 429:
            raise ProviderError("Search is busy right now. Please try again in a moment.", 503)
        if r.status_code >= 400:
            raise ProviderError("Search is temporarily unavailable. Please try again shortly.", 503)
        out = [p for p in (_place_from_nominatim(x) for x in r.json()) if p]
    geocode_cache.put(key, out)
    return out


def _place_from_nominatim(x: dict) -> dict | None:
    try:
        lat, lon = float(x["lat"]), float(x["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    parts = [p.strip() for p in (x.get("display_name") or "").split(",") if p.strip()]
    if not parts:
        return None
    title = (x.get("name") or parts[0]).strip()
    rest = [p for p in parts if p != title][:3]
    return {"id": f"{x.get('osm_type', 'x')[:1]}{x.get('osm_id', '')}", "label": title, "sublabel": ", ".join(rest) or None,
            "lat": lat, "lon": lon, "kind": x.get("type") or x.get("category")}


# ------------------------------------------------------------------ routing
def route_options(origin: tuple[float, float], dest: tuple[float, float]) -> list[dict]:
    """Route candidates from the configured provider, each {distance_km, duration_min, coords: [[lon, lat], ...]}.
    Duration is the provider's road-speed estimate WITHOUT traffic; FlowSense adds simulated delay on top."""
    key = (C.ROUTING_PROVIDER, round(origin[0], 4), round(origin[1], 4), round(dest[0], 4), round(dest[1], 4))
    if (hit := route_cache.get(key)) is not None:
        return hit
    with _guard("Routing"):
        routes = _ors(origin, dest) if C.ROUTING_PROVIDER == "openrouteservice" else _osrm(origin, dest)
    if not routes:
        raise ProviderError("No route could be found between these places.", 404)
    route_cache.put(key, routes)
    return routes


def _osrm(o, d) -> list[dict]:
    url = f"{C.OSRM_URL}/route/v1/driving/{o[1]:.6f},{o[0]:.6f};{d[1]:.6f},{d[0]:.6f}"
    with client() as c:
        r = c.get(url, params={"alternatives": 3, "overview": "full", "geometries": "geojson", "steps": "true"},
                  headers={"User-Agent": C.NOMINATIM_USER_AGENT})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if body.get("code") in ("NoRoute", "NoSegment"):
        raise ProviderError("No route could be found between these places.", 404)
    if r.status_code >= 400 or body.get("code") != "Ok":
        raise ProviderError("Routing is temporarily unavailable. Please try again shortly.", 503)
    return [{"distance_km": x["distance"] / 1000, "duration_min": x["duration"] / 60, "coords": _round(x["geometry"]["coordinates"]),
             **_osrm_detail(x)} for x in body.get("routes", [])]


def _osrm_detail(route: dict) -> dict:
    """Road names (with the metre range each covers) and real junctions (intersections with 3+ approaches) from OSRM steps.
    Empty when the provider gave no steps: callers must treat both as 'unavailable', not zero."""
    roads, junctions, at = [], [], 0.0
    for leg in route.get("legs", []):
        for st in leg.get("steps", []):
            d = float(st.get("distance", 0.0))
            if st.get("name"):
                roads.append({"name": st["name"], "start_m": at, "end_m": at + d})
            for it in st.get("intersections", []):
                if len(it.get("bearings", [])) >= 3 and it.get("location"):
                    junctions.append([round(it["location"][1], 5), round(it["location"][0], 5)])
            at += d
    return {"roads": roads, "junctions": junctions}


def _ors(o, d) -> list[dict]:
    body = {"coordinates": [[o[1], o[0]], [d[1], d[0]]], "instructions": False,
            "alternative_routes": {"target_count": 3, "weight_factor": 1.6, "share_factor": 0.6}}   # documented ORS keys; max 3, routes <= 100 km
    with client() as c:
        r = c.post(f"{C.OPENROUTESERVICE_URL}/v2/directions/driving-car/geojson", json=body,
                   headers={"Authorization": C.OPENROUTESERVICE_API_KEY, "Content-Type": "application/json"})
    if r.status_code in (401, 403):
        raise ProviderError("Routing is not configured correctly on this server.", 503)
    if r.status_code == 429:
        raise ProviderError("Routing is busy right now. Please try again in a moment.", 503)
    if r.status_code == 404 or (r.status_code == 400 and "routable" in r.text.lower()):
        raise ProviderError("No route could be found between these places.", 404)
    if r.status_code >= 400:
        raise ProviderError("Routing is temporarily unavailable. Please try again shortly.", 503)
    return [{"distance_km": f["properties"]["summary"]["distance"] / 1000, "duration_min": f["properties"]["summary"]["duration"] / 60,
             "coords": _round(f["geometry"]["coordinates"])} for f in r.json().get("features", [])]


def _round(coords):
    return [[round(x, 5), round(y, 5)] for x, y, *_ in coords]


# ------------------------------------------------------------------ nearby places (Overpass)
# category id -> (rider label, Overpass tag filters). Each filter is one OSM tag combination.
CATEGORIES = {
    "hospital": ("Hospitals", ['["amenity"="hospital"]']),
    "fuel": ("Fuel stations", ['["amenity"="fuel"]']),
    "food": ("Restaurants", ['["amenity"~"^(restaurant|fast_food)$"]']),
    "bus": ("Bus stops", ['["highway"="bus_stop"]']),
    "metro": ("Metro stations", ['["railway"="station"]["station"="subway"]', '["public_transport"="station"]["subway"="yes"]']),
    "parking": ("Parking", ['["amenity"="parking"]']),
}
PER_CATEGORY = 12


def _overpass_query(lat: float, lon: float, radius: int, cats: list[str]) -> str:
    around = f"(around:{radius},{lat:.6f},{lon:.6f})"
    body = "".join(f"nwr{around}{flt};" for c in cats for flt in CATEGORIES[c][1])
    return f"[out:json][timeout:12];({body});out center qt 600;"


def nearby(lat: float, lon: float, cats: list[str], radius: int) -> list[dict]:
    key = (round(lat, 3), round(lon, 3), radius, tuple(sorted(cats)))
    if (hit := places_cache.get(key)) is not None:
        return hit
    query = _overpass_query(lat, lon, radius, cats)
    last: ProviderError | None = None
    for url in C.OVERPASS_URLS:                                # first mirror that answers wins
        try:
            with _guard("Nearby places", "are"):
                with client() as c:      # short per-mirror timeout: a slow public Overpass must leave time to try the next mirror
                    r = c.post(url, data={"data": query}, headers={"User-Agent": C.NOMINATIM_USER_AGENT}, timeout=httpx.Timeout(14.0, connect=5.0))
                if r.status_code == 429 or r.status_code >= 500:
                    raise ProviderError("Nearby places are busy right now. Please try again in a moment.", 503)
                if r.status_code >= 400:
                    raise ProviderError("Nearby places are unavailable right now.", 503)
                elements = r.json().get("elements", [])
            break
        except ProviderError as e:
            last = e
    else:
        raise last or ProviderError("Nearby places are unavailable right now.", 503)
    out = _select(elements, lat, lon, cats)
    places_cache.put(key, out)
    return out


def _category_of(tags: dict) -> str | None:
    a, h, rw = tags.get("amenity"), tags.get("highway"), tags.get("railway")
    if a == "hospital": return "hospital"
    if a == "fuel": return "fuel"
    if a in ("restaurant", "fast_food"): return "food"
    if h == "bus_stop": return "bus"
    if rw == "station" and tags.get("station") == "subway" or tags.get("public_transport") == "station" and tags.get("subway") == "yes": return "metro"
    if a == "parking": return "parking"
    return None


def _select(elements: list[dict], lat: float, lon: float, cats: list[str]) -> list[dict]:
    """Normalise Overpass elements; keep the nearest few per category. Missing fields stay None (never invented)."""
    by_cat: dict[str, list[dict]] = {c: [] for c in cats}
    seen = set()
    for e in elements:
        tags = e.get("tags") or {}
        cat = _category_of(tags)
        plat, plon = e.get("lat"), e.get("lon")
        if plat is None:
            c = e.get("center") or {}
            plat, plon = c.get("lat"), c.get("lon")
        if cat not in by_cat or plat is None or plon is None or (e.get("type"), e.get("id")) in seen:
            continue
        seen.add((e.get("type"), e.get("id")))
        street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
        addr = ", ".join(x for x in (street, tags.get("addr:suburb"), tags.get("addr:city")) if x) or None
        by_cat[cat].append({
            "id": f"{e.get('type', 'x')[:1]}{e.get('id')}", "category": cat, "category_label": CATEGORIES[cat][0][:-1] if CATEGORIES[cat][0].endswith("s") else CATEGORIES[cat][0],
            "name": tags.get("name") or None, "lat": float(plat), "lon": float(plon), "address": addr,
            "distance_m": round(haversine_m(lat, lon, float(plat), float(plon))),
            "opening_hours": tags.get("opening_hours") or None, "phone": tags.get("phone") or tags.get("contact:phone") or None,
            "website": tags.get("website") or tags.get("contact:website") or None,
        })
    out = []
    for c in cats:
        out += sorted(by_cat[c], key=lambda p: p["distance_m"])[:PER_CATEGORY]
    return sorted(out, key=lambda p: p["distance_m"])


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))
