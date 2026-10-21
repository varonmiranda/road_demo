# Frederick Health Hospital Campus Salting Advisor

Replays the **Jan 5–7, 2025 storm** hour by hour across the hospital's **own campus surfaces** —
internal drives, parking lots and aisles, the parking-garage top deck and pedestrian walkways — and
tells the facilities team when to start treating, which surfaces to treat first, and with what.

The demo no longer models public city streets: those are the city's responsibility, not the
hospital's. Everything shown sits inside the campus boundary taken from the OpenStreetMap
`amenity=hospital` polygon.

## What it does
- **Map** every campus surface, labelled by type, coloured by ice risk (Low / Watch / Treat soon /
  Treat now) — or by recommended material, or by surface type.
- **Estimate** each surface's temperature from the forecast or the storm replay (air temperature,
  dew point, precipitation type and amount, wind, cloud cover), accounting for:
  - elevated, exposed surfaces (garage top deck, ramps) that cool faster than at-grade pavement,
  - shaded surfaces next to tall buildings that stay colder,
  - concrete walkways vs asphalt lots.
  These are **estimates, not measurements**; the assumptions are listed in the Details panel.
- **Recommend** per surface: predicted surface temperature, ice-risk level, and an action
  (pre-treat with brine, salt, sand, plow/shovel, or no action) with suggested timing.
- **Prioritise**: 1) ED entrance and ambulance routes, 2) main entrances, ramps and accessible
  paths, 3) staff and visitor sidewalks, 4) parking-garage top decks, 5) parking lots,
  6) loading docks.
- The main card summarises the campus: when to start treatment, which surfaces first, and an
  ordered checklist the facilities team can follow.

Surface types OpenStreetMap does not carry for this hospital (ED entrance & ambulance bay, main
entrance / drop-off, ramps & stairs, loading dock) are listed in the checklist as **not mapped
yet** — the app never falls back to public streets to fill the gap.

## Open the demo
In the Base44 sandbox this runs under docker compose (see `AGENTS.md`); standalone it is still:
```bash
python3 serve.py            # PORT=3000 under compose, 8765 by default
```
- **Storm replay:** `/` (Jan 5–7, 2025 storm)
- **Live forecast:** `/?mode=live` (next 48 h from the National Weather Service)
  - **Refresh forecast** re-pulls NWS data (needs internet and `serve.py`).
  - **Drill −25°F** shifts the *real* forecast 25°F colder to rehearse the plan. The page shows a
    "DRILL — not a real forecast" banner. Use it in October demos; never present it as a forecast.

### How live mode works
- `live_plan.py` asks api.weather.gov for the hospital's 2.5 km forecast grid cell (Frederick Health
  Hospital resolves to LWX/80,93). It reads temperature, dew point, sky cover, precipitation,
  snowfall and ice, hour by hour.
- NWS doesn't forecast sunshine or ground temperature, so both are derived: sunshine from the sun's
  position times forecast cloud cover; ground temperature as the past-72 h mean air temperature at
  Frederick Municipal Airport (KFDK).
- Each surface's temperature comes from `thermal.surface_series`, then the same risk rules run. In a
  dry, mild forecast the plan reads **"No treatment needed"**, which is correct and expected in October.
- Command line: `python3 live_plan.py` (or `--drill 25`). It only needs `requests`.

## Rebuild the data
```bash
python3 build_surfaces.py  # campus boundary + surface inventory -> data/surfaces.json, data/campus.js
python3 build_basemap.py   # offline vector basemap             -> data/basemap.js
python3 simulate.py        # storm replay                       -> data/replay.json / replay.js
python3 live_plan.py       # live snapshot                      -> data/live.js
```
`build_surfaces.py` needs `requests` only (it pulls the campus bbox straight from the OpenStreetMap
API; Overpass is blocked in the sandbox). `build_basemap.py` uses `osmnx` and shares a road-graph
cache — point `RSD_CACHE` at a directory outside the repo to keep that (large) graphml out of git.
`simulate.py` refetches the storm weather from Open-Meteo when `data/weather_2025-01-05_07.json` is
missing. The surface model's coefficients (sun, sky loss, shade, pavement type, elevation) are at the
top of `thermal.py`; the risk bands and recommendation rules are in `plan.py`.

## Earlier work in this repo (DC study, not re-run for Frederick)
The original demo compared a **blanket** salt strategy with an **advisor** strategy across the road
corridors leading to eight DC emergency departments. That study and its satellite evidence still
live in this repo, clearly labelled as the DC study and not re-run for Frederick:
- `regression.html` + `data/regression.js` + `regression.py` — which physical factors predict a cold
  road surface across 4,000 random DC roads, using satellite measurements as targets.
- `index-v1-analytics.html`, `index-v2-decision.html`, `index-v3-console.html` — earlier dashboard
  designs (dark theme, road corridors).
- `build_data.py`, `export_kml.py` — the old road-corridor dataset and Google Earth layer. The main
  app no longer uses `data/segments.json`.

Headlines from that DC study, kept for reference: the advisor used about half the salt
(31–57% depending on the assumed blanket rate) with no loss of safety in the simulation; the
coldest-25% classifier scored AUC 0.71 on unseen neighbourhoods; and roads the engine predicts run
colder kept more snow (Spearman ρ = −0.23), a modest but consistent association. Do not quote these
as Frederick results.

## What to say about it (and what not to)
- **Say:** "In a simulation of the January 2025 storm, the campus plan treated the hospital's own
  surfaces — drives, lots, the garage deck and walkways — starting at the entrances, and kept the
  surface temperatures in view." That is what the demo shows.
- **Don't say** the surface temperatures were measured. They come from a heat-balance estimate built
  from stated assumptions; the Details panel lists them.
- **Don't say** the hospital actually applied a particular tonnage. The plan is a demo and dispatches
  nothing.
- The weather is reanalysis (ERA5) used as a perfect forecast. Check the official storm total before
  quoting it.
