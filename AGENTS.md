# Working notes for this repo (Base44 sandbox)

## What this project is
A static demo (no backend app, no database) served by the project's own stdlib dev server,
`serve.py`. It replays the Jan 2025 DC snowstorm across 1,755 road segments and serves two modes:
the storm replay (`/`) and a live NWS-forecast mode (`/?mode=live` or `/index.html#live`).

## Layout
- Pages and Python live at the repo root: `index.html` (main), `regression.html`,
  `index-v1-analytics.html`, `index-v2-decision.html`, `index-v3-console.html`, `serve.py`,
  `live_plan.py`, `simulate.py`, `thermal.py`, and the rebuild scripts.
- `data/` holds the generated assets the pages load (`basemap.js`, `replay.js`, `live.js`,
  `regression.js`, `segments.json`, `gee_features.json`, `weather_2025-01-05_07.json`,
  `hospital_salting_plan.kml`), and `vendor/` holds `leaflet.css`.
- `index.html` (the main dashboard) is **light-themed**: its `:root` block carries the light
  palette with `color-scheme: light`, and the map colors come from the `--bm-*` tokens (plus
  `--bm-major` for arterial roads), so the whole page — card, legend, Details panel, Leaflet
  controls — follows those tokens. The alternate designs (`index-v1/v2/v3-*.html`) and
  `regression.html` still use the original dark theme.
- The pages hard-code those relative paths (`data/…`, `vendor/leaflet.css`) and every Python
  script points at `data/`. The original upload had these assets flattened into the repo root,
  which left the pages unable to load their data — they were moved back into `data/`/`vendor/`
  with no code changes.

## Run it here
```bash
docker compose -f docker-compose.base44.yml up -d --build   # http://localhost:3000
docker compose -f docker-compose.base44.yml logs -f web
```
- `serve.py` reads `PORT` (compose sets 3000) and prints its own URLs; it binds 0.0.0.0.
- `Dockerfile.base44` installs dependencies only; the source is bind-mounted, so page/JS/data
  edits need **no rebuild**.
- **No live reloader**: `serve.py` is a plain `http.server`. HTML/JS/data edits are served fresh
  on the next request (`Cache-Control: no-store`), but after editing `serve.py`, `live_plan.py`,
  `simulate.py` or `thermal.py` restart the service:
  `docker compose -f docker-compose.base44.yml restart web`.

## First boot / data that is not in the repo
- `data/replay.json` is **not** committed, but `live_plan.py` needs it (live mode reads the storm
  replay to know the segments). The compose command generates it once with `python3 simulate.py`,
  which is deterministic and needs no network (it uses the committed `data/segments.json` and
  `data/weather_2025-01-05_07.json`). Afterwards the file sits in the bind-mounted repo.
- `data/replay.js` and `data/live.js` are generated artifacts that happen to be committed as
  snapshots. `simulate.py` rewrites `replay.js`; `live_plan.py` (or the page's **Refresh
  forecast** button, `POST /api/refresh?drill=0`) rewrites `live.js`. Re-generating them is normal
  and leaves the tree dirty — restore with `git checkout -- data/live.js data/replay.js` if a
  clean tree matters.

## Credentials
None. Live mode calls `api.weather.gov`, which needs no API key (only internet access and a
descriptive User-Agent, which `live_plan.py` sets). The Google Earth Engine rebuild scripts
(`gee_features.py`, `gee_citywide_sample.py`) need `EE_PROJECT` + `earthengine authenticate`, but
they are only for regenerating the committed inputs and are not needed to run the demo.
`/run/base44/app.env` is therefore not referenced by compose.

## Sandbox / preview notes
- No host or origin allowlists in the code, so no sandbox-only config overrides were needed and
  nothing is gated on `BASE44_PREVIEW_MODE`.
- The page treats any hostname that is not `localhost`/`127.0.0.1` as "static" (`index.html`, the
  `STATIC` constant): it labels live mode "Snapshot of the …" and never renders the **Refresh
  forecast** / **Drill −25 °F** controls. Sandbox previews come through the proxy host, so those
  two controls are absent in the preview. The endpoint itself works regardless — verify with
  `curl -s -X POST 'http://localhost:3000/api/refresh?drill=0'` (it rewrites `data/live.js`; run
  `git checkout -- data/live.js` afterwards to keep the tree clean).
- Leaflet and the Google fonts load from CDNs, so the browser needs internet access.
- Rebuild-only deps (`osmnx`, `scikit-learn`, `statsmodels`, `earthengine-api`) are deliberately
  not installed in the image — `requirements.txt` covers only what the server needs.

## How to verify
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/            # 200
curl -s http://localhost:3000/ | grep -c 'Hospital Salting Advisor'        # 1
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/data/replay.js   # 200
docker compose -f docker-compose.base44.yml ps                             # web = healthy
```
Live mode (needs internet, ~15 s):
`curl -s -X POST 'http://localhost:3000/api/refresh?drill=0'` → `{"ok": true, "summary": "NWS …"}`.
