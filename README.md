# Frederick Health Hospital Access Salting Advisor: storm replay demo (Round 1)

Replays the **Jan 5–7, 2025 storm** hour by hour across 185 road segments
(33.9 lane-miles) that give access to Frederick Health Hospital's emergency department. It compares
two strategies using the same trucks and the same plow passes:

| | Blanket (typical practice) | Advisor |
|---|---|---|
| Salt used | 32.1 tons | **17.0 tons (−47%)** |
| Icy lane-mile-hours, tier-1 roads | 0 | 0 |
| Icy lane-mile-hours, all corridor roads | 0 | 0 |
| CaCl₂ (cold-weather material) | 0 | 0 (no blend needed in this run) |

**Quote a range, not 47%.** The blanket rate is assumed, and the saving moves with it:
150 lb/lane-mi gives 31%, 200 gives 47%, and 250 gives 57%. A defensible headline is
**"about half the salt (31–57% depending on the assumed blanket rate), with no loss of safety in
the simulation."**

**This build runs without the Earth Engine microclimate layer.** `data/gee_features.json` was
removed when the demo was re-targeted, because those variables were sampled per *DC* segment id and
would be meaningless on Frederick roads. Pavement temperature therefore comes from the simpler
two-class model in `thermal.py` (bridge / not bridge). For reference, the DC study measured 125.8 t
without Earth Engine, 117.7 t without the heat-island/satellite terms, and 106.0 t with the full
engine — the full microclimate engine found a few percent more saving. Regenerating it needs a
Google Earth Engine project (`EE_PROJECT=<project> python3 gee_features.py`).

**Satellite check (Sentinel-2, 5 clear scenes Jan 7–20, 2025) — DC study, not re-run for
Frederick:** roads the engine predicts run
colder kept more snow. Spearman ρ = −0.23 across 1,755 roads, −0.24 excluding bridges, and the
same direction in 6 of 7 hospital areas (Georgetown was the exception). That's modest but consistent. Roads with
more pavement and taller buildings kept *less* snow, which independently supports the
urban-heat-island term.

## Regression evidence page (`regression.html`) — DC study, not re-run for Frederick
Tests which physical factors predict a cold road surface across **4,000 random DC roads**, using
satellite *measurements* as targets (Landsat winter day temperature, MODIS night temperature,
Sentinel-2 snow after the storm), not the engine's own output. Headlines:
- "Coldest 25% of roads" classifier: **AUC 0.71** on unseen 2 km neighborhoods, well calibrated.
- **10 of 11** engine assumptions are backed by the satellites. Trees and buildings make roads colder by day
  and warmer at night, as `thermal.py` assumes. The one contradiction is bridges at night, most
  likely a 1 km MODIS pixel artifact over rivers.
- The factors explain **12–48%** of road-to-road variation on unseen neighborhoods. Random
  cross-validation overstates this; spatial cross-validation is the honest number.
- This shows association, not proof of cause. Error bars are clustered by 1 km cell.

Rebuild: `EE_PROJECT=green-infrapro python3 gee_citywide_sample.py && python3 regression.py`

## Open the demo
In the Base44 sandbox this runs under docker compose (see `AGENTS.md`); standalone it is still:
```bash
python3 serve.py            # PORT=3000 under compose, 8765 by default
```
- **Storm replay:** `/` (Jan 5–7, 2025 storm, both strategies compared)
- **Live forecast:** `/?mode=live` (next 48 h from the National Weather Service)
  - **Refresh forecast** re-pulls NWS data (needs internet and `serve.py`; about 7 s).
  - **Drill −25°F** shifts the *real* forecast 25°F colder to rehearse the plan. The page shows a
    "DRILL — not a real forecast" banner. Use it in October demos; never present it as a forecast.
- `data/hospital_salting_plan.kml` opens directly in Google Earth.

### How live mode works
- `live_plan.py` asks api.weather.gov for each hospital's own 2.5 km forecast grid cell (Frederick
  Health Hospital resolves to a single cell, LWX/80,93). It reads temperature, dew point, sky cover,
  precipitation, snowfall and ice, hour by hour.
- NWS doesn't forecast sunshine or ground temperature, so both are derived: sunshine from the sun's
  position times forecast cloud cover; ground temperature as the past-72 h mean air temperature at
  Frederick Municipal Airport (KFDK).
- Each road's pavement temperature comes from the same engine (`thermal.py`), then
  the same advisor rules run. A new **frost / black-ice rule** brines tier-1/2 roads when dry pavement at or
  below 32°F is about to meet its dew point. Without that rule a quiet clear night would never trigger
  treatment.
- In a dry, mild forecast the plan reads **"No treatment needed"**, which is correct and expected in October.
- Command line: `python3 live_plan.py` (or `--drill 25`). It only needs `requests`, no Earth Engine.

## Rebuild the data (only if you change assumptions)
```bash
pip install osmnx scikit-learn requests pandas earthengine-api   # then: earthengine authenticate
python3 build_data.py      # corridors, bridges, grades  -> data/segments.json (OSM: Frederick, MD)
python3 build_basemap.py   # offline vector basemap       -> data/basemap.js
EE_PROJECT=<project> python3 gee_features.py   # Earth Engine variables -> data/gee_features.json
python3 simulate.py        # storm replay, both strategies -> data/replay.json / replay.js
python3 export_kml.py      # Google Earth layer -> data/hospital_salting_plan.kml
```
`build_data.py` and `build_basemap.py` share a road-graph cache; point `RSD_CACHE` at a directory
outside the repo to keep that (large) graphml out of git. `simulate.py` refetches the storm weather
from Open-Meteo when `data/weather_2025-01-05_07.json` is missing. The hospital list — currently
just Frederick Health Hospital — is `HOSPITALS` at the top of `build_data.py`.
Salt assumptions (rates, bond threshold, route cycle, blanket rate) are constants at the top of
`simulate.py`. Microclimate coefficients (sun, heat island, elevation, satellite weights) are at
the top of `thermal.py`.

## What to say about it (and what not to)
- **Say:** "In a simulation of the January 2025 storm, rules that use pavement temperature kept the
  hospital approach ice-free with about half the salt." That is consistent with field results in
  the research (Indiana 41%, Hyde Park ~50%).
- **Say:** "In the coldest hour of the replay the air was 20°F but bridge decks were modeled at
  15°F, where rock salt barely works." In this run the advisor hit the target without a CaCl₂
  blend; the DC study switched 1.2 t on its coldest bridges.
- **Don't say** the pavement temperatures were measured. They come from a heat-balance model.
  Round 2 replaces it with ML trained on road weather station (RWIS) data.
- **Don't say** Frederick County actually used 32 tons. The blanket strategy is a modeled baseline.
  Ask the county/state agency for their real application records and use those for the Round 2
  impact simulation.
- The weather is reanalysis (ERA5) used as a perfect forecast. The Frederick grid cell shows 3.8 in
  of snow; check the official total before quoting it.

## Round 2 upgrades
1. ML pavement-temperature model (RWIS training data via MADIS), plus forecast error.
2. Fit the `thermal.py` coefficients to measured road temperatures (RWIS) rather than assuming them.
   Swap tree canopy to the newer USFS TCC 2025 asset (the NLCD 2023 one now shows a deprecation warning).
3. Truck routing with OR-Tools (depots, capacity, refills) in place of a fixed 3 h cycle.
4. A live mode that pulls the NWS/HRRR forecast and issues tomorrow's plan automatically.
5. An LLM-written briefing for the county/state agency, generated from the plan table. The rules set the rates; the LLM only explains them.
