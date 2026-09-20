# FlowSense AI — Rider (User) Portal

A public, map-first experience next to the operator control room. Same brand and the same backend intelligence, different job:

| | Operator portal (`/login` → `/dashboard`) | Rider portal (`/user`) |
|---|---|---|
| Question | What is wrong, what will happen, what should we do? | Where am I going, how is traffic, is there a better way? |
| Access | Login ID + password (session cookie) | Public, no account |
| Shows | Detection, incidents, forecasts, propagation, interventions, simulation, evaluation | Map, search, routes, traffic, alerts, diversion advice, nearby places |
| API | `/api/*` (protected) | `/api/user/*` (public, rate limited, rider language only) |

## Workflow

1. **Open**: press **USER LOGIN** (below *Sign In* on the operator login) or go to `/user`.
2. **Destination**: type a place. Suggestions appear after a short pause (debounced); a newer query cancels the older one.
3. **Start**: *Your location* (browser geolocation, only when chosen), a searched place, or a pin dropped on the map.
4. **Routes**: up to three options with ETA, distance, traffic level, delay and rank. The recommended one is selected and framed; tap another card or route line to switch.
5. **Alerts and diversion**: when the usual route is heavy/severe and a better alternative exists, a *Diversion recommended* card shows both ETAs, the **estimated** saving and a confidence label.
6. **Nearby**: toggle Hospitals, Fuel, Food, Bus, Metro, Parking; tap a marker, then **Get directions**.
7. **Replay**: the play button steps the dataset forward 5 simulated minutes every 6 s.

States implemented: default, searching, results, no results, route loading, route error with retry, alternative route, traffic alert, nearby loading/empty/error, location denied/unavailable/outside service area, API unreachable.

## Data honesty

- **Traffic is simulated.** It is the organizer dataset replayed by FlowSense's models, labelled *Simulated traffic* everywhere. No live traffic provider is connected.
- **The simulation is not real road geometry.** It is a synthetic 12×10 grid placed over Hyderabad. The rider map never draws the grid: congested segments appear only as soft translucent bands, and route lines follow real roads from the routing provider.
- **How a route gets traffic.** Each ~120 m stretch of a real route takes the state of the worst simulated segment within 750 m (else the nearest within 1.5 km, else *no data*, drawn gray). This is an approximation and the UI says so. Computed in `backend/app/user_traffic.py`; nothing is random.
- **Provider ETAs contain no traffic.** FlowSense adds its simulated delay on top.
- Alerts say *Traffic disruption detected* or *Possible incident*. No accident is ever asserted or fabricated.

## Ranking and diversion

```
eta       = provider duration + simulated delay now
worsening = max(0, simulated delay at +30 min - delay now)   # FlowSense forecast
score     = eta + 0.5 * worsening + 0.1 * distance_km        # lower is better
```

- Lowest score = **Recommended route**; others are *Alternative* / *Second alternative*. A transparent heuristic, not a claim of mathematical optimality.
- The provider's first route is the *current route* (the default a rider would take).
- A **diversion** is recommended only when the current route is heavy/severe (or expected to worsen by 3 min or more) **and** the best alternative is at least 2 min faster in expected time. Savings are always worded "estimated".
- No alternative available means no diversion; the panel says so.

## Architecture

```
                 FlowSense AI
                      |
          +-----------+-----------+
          |                       |
   Operator portal          Rider portal (/user)
   (session cookie)         (public)
          |                       |
     /api/* protected        /api/user/*
          +------- FastAPI -------+
                      |
     +----------------+-----------------+
     |                |                 |
 Dataset          Routing            Places / search
 intelligence     OSRM / ORS         Overpass / Nominatim
```

- `app/user_api.py`: public router, validation, per-IP rate limits, server-owned replay clock.
- `app/user_traffic.py`: real route x simulated traffic, ranking, diversion, alerts, congestion bands.
- `app/providers.py`: the only code that talks to third parties (cache, throttle, friendly errors). Swap a provider here.
- Frontend: `pages/UserPortal.tsx`, `components/user/*`, `services/userApi.ts`, `hooks/useGeolocation.ts`.

## Public API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/user/config` | Mode, source label, replay clock, service area, providers, categories. No keys |
| GET | `/api/user/traffic?step=` | Simulated congestion bands (normal segments omitted) |
| GET | `/api/user/alerts?step=` | Area alerts in rider language |
| GET | `/api/user/geocode?q=` | Place search (Nominatim), 2-100 chars, bounded to the service area |
| POST | `/api/user/route-options` | `{origin, destination, step}` returns ranked `routes`, `diversion`, `alerts` |
| POST | `/api/user/nearby` | `{lat, lon, categories, radius_m}` returns `places` |

Locations travel in POST bodies so coordinates stay out of URLs and logs. `step` is 0-23 (default 6 = 13:30); riders cannot request arbitrary timestamps. Errors are `{"detail": "<readable sentence>"}`.

## Third-party services

| Need | Provider | Key | Notes |
|---|---|---|---|
| Basemap | OpenStreetMap tiles | no | Demo use; set `VITE_TILE_URL` for a hosted provider |
| Search | Nominatim | no | Max 1 request/s (enforced), identifying User-Agent required, cached 10 min |
| Routing | OSRM demo (default) | no | Demonstration use, no SLA; 1-2 alternatives depending on the trip |
| Routing | OpenRouteService | `OPENROUTESERVICE_API_KEY` | Auto-selected when the key is set; `alternative_routes` up to 3, routes up to 100 km. Written from the ORS docs and covered by mocked tests only; **not exercised live** (no key) |
| Nearby | Overpass | no | Public servers are rate limited and sometimes time out; two mirrors tried in order (`OVERPASS_URL`), cached 10 min |
| Weather | not used | n/a | The dataset already carries rain and temperature |

Provider calls happen only in the backend. The browser receives just `VITE_API_BASE_URL` and `VITE_TILE_URL`, both public.

## Environment variables (all optional)

`ROUTING_PROVIDER`, `OPENROUTESERVICE_API_KEY`, `OSRM_URL`, `OPENROUTESERVICE_URL`, `GEOCODING_URL`, `OVERPASS_URL`, `NOMINATIM_USER_AGENT`. See `.env.example`. Put them in the backend environment, never in `VITE_*`.

## Security

- Only `/api/user/*` is public; every other `/api/*` route still needs the operator session (tested).
- Rider responses carry no anomaly scores, model data, labels, evaluation, credentials or keys (tested).
- Validation: numeric coordinates inside the service area, query length, category whitelist, radius 200-5000 m, step 0-23. Validation errors never echo submitted values.
- Per-IP limits per minute: traffic 60, alerts 60, config 60, search 40, routes 20, nearby 20 (friendly 429). In-memory per process; use a gateway or Redis for multiple workers.
- Provider text is never rendered as HTML; a website is linked only if it starts with `http(s)://`.
- Device location lives in React state only, is never stored, and is asked for only after the rider taps a location control (or if permission was already granted).

## Known limitations

- No live traffic and no verified incidents: a dataset replay (2026-01-16 13:00-14:55) approximated onto real roads.
- No turn-by-turn navigation: it is a route preview.
- Public OSRM/Nominatim/Overpass servers are for demos; expect occasional slowness (the app shows a message and retry). Use self-hosted or paid providers in production.
- Hyderabad service area only.
- OSRM often returns one route for short trips; the panel then says no alternative was available.
- Place data quality is that of OpenStreetMap; fields are shown only when present.
- Rate limits and caches are in-process.

## Demo

1. Start API and frontend (see README), open `/login`, press **USER LOGIN**.
2. Start **Ameerpet**, destination **Uppal**. At 13:30 the usual route crosses the simulated incident, so *Diversion recommended* appears with an estimated saving.
3. Press play to watch the incident form and clear.
4. Close directions, toggle **Hospitals**, tap a marker, then **Get directions**.

## Traffic intelligence: "Why is traffic heavy?"

When the selected route has heavy or severe traffic, or a rider taps a congested band, an incident marker, or an alert's **Why is traffic heavy?** link, FlowSense explains the traffic. The panel is compact by default (cause, certainty, confidence, three evidence lines, one forecast sentence) and expands to full evidence and a +15/+30/+60 min forecast.

**Source.** `backend/app/user_explain.py` translates the operator engine's existing outputs (`Snapshot.diagnosis`, `evidence`, and the LightGBM speed forecast). No second model and no frontend rules. Organizer incident labels (`incidents_*.csv`) are never read at runtime.

**Fact vs inference** (the badge on the card):

| Certainty | Meaning | Comes from |
|---|---|---|
| Reported | A record exists | Active roadwork on record |
| Likely | Rule-based or high-confidence detection | Detector confidence >= 0.8, recurring bottleneck, spillback |
| Possible | Weak support | Detector confidence < 0.8, weather/event context |
| Unknown | Not supported by the data | Abnormal traffic with no identifiable cause: shown as "Cause unclear" |

FlowSense never says "confirmed accident". A sudden local drop is shown as **Traffic disruption**. A type ("Accident-like incident", "Lane blockage-like incident", ...) is shown only when the type estimate is Medium; on held-out validation the type estimate did not beat chance, so the card says the type is a weak estimate and otherwise shows "Undetermined".

**Evidence** lines appear only when the measured value supports them (speed at least 10% below its baseline, onset step >= 0.1 speed ratio, queue growth >= 5 vehicles, congestion at least 0.15 above baseline, rain >= 0.3). Each item carries metric, observed value, reference, change, segment and timestamp. Anomaly scores and model diagnostics are not in the public response.

**Forecast.** Levels at +15/+30/+60 min from the speed forecaster, with a per-horizon confidence (from the p10-p90 band: High/Medium/Low), a trend (worsening/steady/easing) and a plain reason. "Congestion may spread to nearby roads" appears only when a neighbouring segment is also slowing and traffic is not easing.

**Failure.** If the explanation cannot be produced: "Traffic disruption detected, but the cause could not be determined." The map and routes keep working.

## Leaving the rider portal (Log out)

The rider portal is public: there is no rider account or session. **Log out** (top right of the search card) calls `POST /api/auth/logout`, which clears and *revokes* any FlowSense session cookie in this browser (for example an operator who opened `/user`) and returns to `/login`. From there, Sign In opens the operator portal and User login opens the rider portal. Opening `/user` again after logout works, because it is public by design; operator pages still redirect to `/login`. Revocation is an in-memory list of tokens (per process, cleared on restart): use a shared store for several workers.
