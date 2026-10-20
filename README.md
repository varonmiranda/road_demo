# DC Hospital Access Salting Advisor: storm replay demo (Round 1)

Replays the **Jan 5–7, 2025 DC snowstorm** hour by hour across 1,755 road segments
(265 lane-miles) that give access to 8 DC emergency departments. It compares two strategies
using the same trucks and the same plow passes:

| | Blanket (typical practice) | Advisor + Earth Engine microclimate |
|---|---|---|
| Salt used | 244.9 tons | **106.0 tons (−57%)** |
| Icy lane-mile-hours, tier-1 roads | 0 | 0 |
| Icy lane-mile-hours, all corridor roads | 0 | 0 |
| CaCl₂ (cold-weather material) | 0 | 1.2 t, coldest bridges only |

**Quote a range, not 57%.** Here is how much of the saving rests on assumptions:

| Engine version | Advisor tons | Saving vs 200 lb blanket |
|---|---|---|
| Two road types only (bridge / not), no Earth Engine | 125.8 | 49% |
| Earth Engine, but no heat-island or satellite terms | 117.7 | 52% |
| Full Earth Engine engine | 106.0 | 57% |

The blanket rate is also assumed, and the saving moves with it: 150 lb/lane-mi gives 43%,
200 gives 57%, and 250 gives 65% (full engine). A defensible headline is **"roughly half the salt (49–57%), with
no loss of safety in the simulation."**

**Satellite check (Sentinel-2, 5 clear scenes Jan 7–20, 2025):** roads the engine predicts run
colder kept more snow. Spearman ρ = −0.23 across 1,755 roads, −0.24 excluding bridges, and the
same direction in 6 of 7 hospital areas (Georgetown was the exception). That's modest but consistent. Roads with
more pavement and taller buildings kept *less* snow, which independently supports the
urban-heat-island term.

## Regression evidence page (`regression.html`)
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
```bash
cd road-salt-demo && python3 serve.py
```
- **Storm replay:** http://localhost:8765 (Jan 5–7, 2025 storm, both strategies compared)
- **Live forecast:** http://localhost:8765/?mode=live (next 48 h from the National Weather Service)
  - **Refresh forecast** re-pulls NWS data (needs internet and `serve.py`; about 7 s).
  - **Drill −25°F** shifts the *real* forecast 25°F colder to rehearse the plan. The page shows a
    "DRILL — not a real forecast" banner. Use it in October demos; never present it as a forecast.
- `data/hospital_salting_plan.kml` opens directly in Google Earth.

### How live mode works
- `live_plan.py` asks api.weather.gov for each hospital's own 2.5 km forecast grid cell (6 distinct
  cells across the 8 hospitals). It reads temperature, dew point, sky cover, precipitation, snowfall
  and ice, hour by hour.
- NWS doesn't forecast sunshine or ground temperature, so both are derived: sunshine from the sun's
  position times forecast cloud cover; ground temperature as the past-72 h mean air temperature at
  Reagan National (KDCA).
- Each road's pavement temperature comes from the same Earth Engine engine (`thermal.py`), then
  the same advisor rules run. A new **frost / black-ice rule** brines tier-1/2 roads when dry pavement at or
  below 32°F is about to meet its dew point. Without that rule a quiet clear night would never trigger
  treatment.
- In a dry, mild forecast the plan reads **"No treatment needed"**, which is correct and expected in October.
- Command line: `python3 live_plan.py` (or `--drill 25`). It only needs `requests`, no Earth Engine.

## Rebuild the data (only if you change assumptions)
```bash
pip install osmnx scikit-learn requests pandas earthengine-api   # then: earthengine authenticate
python3 build_data.py      # corridors, bridges, grades  -> data/segments.json
EE_PROJECT=green-infrapro python3 gee_features.py   # Earth Engine variables -> data/gee_features.json
python3 simulate.py        # storm replay, both strategies -> data/replay.json / replay.js
python3 export_kml.py      # Google Earth layer -> data/hospital_salting_plan.kml
```
Salt assumptions (rates, bond threshold, route cycle, blanket rate) are constants at the top of
`simulate.py`. Microclimate coefficients (sun, heat island, elevation, satellite weights) are at
the top of `thermal.py`.

## What to say about it (and what not to)
- **Say:** "In a simulation of a real DC storm, rules that use pavement temperature kept every
  hospital approach ice-free with about half the salt." That is consistent with field results in
  the research (Indiana 41%, Hyde Park ~50%).
- **Say:** "On the morning of Jan 7 the air was 19°F but bridge decks were modeled at 15°F,
  where rock salt barely works. The advisor switched only those bridges to a CaCl₂ blend."
- **Don't say** the pavement temperatures were measured. They come from a heat-balance model.
  Round 2 replaces it with ML trained on road weather station (RWIS) data.
- **Don't say** DC actually used 245 tons. The blanket strategy is a modeled baseline. Ask DPW
  for their real application records and use those for the Round 2 impact simulation.
- The weather is reanalysis (ERA5) used as a perfect forecast. The grid cell shows 5.9 in of
  snow, while DC's official total was higher; check the official number before quoting it.

## Round 2 upgrades
1. ML pavement-temperature model (RWIS training data via MADIS), plus forecast error.
2. Fit the `thermal.py` coefficients to measured road temperatures (RWIS) rather than assuming them.
   Swap tree canopy to the newer USFS TCC 2025 asset (the NLCD 2023 one now shows a deprecation warning).
3. Truck routing with OR-Tools (depots, capacity, refills) in place of a fixed 3 h cycle.
4. A live mode that pulls the NWS/HRRR forecast and issues tomorrow's plan automatically.
5. An LLM-written briefing for DPW/DDOT, generated from the plan table. The rules set the rates; the LLM only explains them.
