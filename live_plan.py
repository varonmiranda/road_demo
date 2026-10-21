"""Live mode: pull the current National Weather Service forecast and build the next-48-hour
salting plan for every hospital access road.

    python3 live_plan.py              (or click "Refresh forecast" when running serve.py)
    python3 live_plan.py --drill 25   DRILL: the real forecast shifted 25 F colder, for rehearsals.
                                      Clearly labelled on the page; never present it as a forecast.

Output: data/live.js  (window.LIVE = {...}, same shape as the storm replay)

Sources (no API key; NWS asks for a descriptive User-Agent)
  - api.weather.gov/points -> each hospital's own 2.5 km NWS forecast grid cell (Sterling, VA office)
  - gridpoint forecast: temperature, dew point, sky cover, liquid precipitation, snowfall, ice
  - KFDK observations (Frederick Municipal Airport), past 72 h: mean air temperature as the ground-temperature input

NWS does not forecast sunshine or ground temperature, which the pavement engine needs:
  - sunshine: clear-sky solar radiation from the sun's position (Haurwitz), reduced by forecast
    sky cover (Kasten-Czeplak)
  - ground temperature: average air temperature over the past 72 h at Frederick Municipal (KFDK)
"""
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

import simulate as sm
import thermal

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TZ = ZoneInfo("America/New_York")
HOURS = 48
UA = {"User-Agent": "dc-hospital-salting-advisor (American University student project)",
      "Accept": "application/geo+json"}
SITE = (39.42296, -77.41485)   # Frederick Health Hospital


def get(url, tries=4):
    for k in range(tries):
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (k + 1))        # NWS gridpoints occasionally return 500/503; retry
    r.raise_for_status()


def duration_hours(iso):
    """ISO-8601 duration like PT1H, PT6H, P1DT3H -> hours."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", iso)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + (1 if mi else 0)


def hourly(prop, hours_utc, amount=False):
    """Expand an NWS time-series property onto an hourly UTC grid.
    amount=True spreads accumulations (mm over the window) evenly per hour."""
    out = {h: None for h in hours_utc}
    for v in (prop or {}).get("values", []):
        start, dur = v["validTime"].split("/")
        t0 = datetime.fromisoformat(start).astimezone(timezone.utc)
        n = max(1, duration_hours(dur))
        val = v["value"]
        for k in range(n):
            h = t0 + timedelta(hours=k)
            if h in out and val is not None:
                out[h] = val / n if amount else val
    return [out[h] for h in hours_utc]


def fill(xs, default):
    last = next((x for x in xs if x is not None), default)
    res = []
    for x in xs:
        last = x if x is not None else last
        res.append(last)
    return res


def solar_ghi(dt_utc, sky_pct, lat=SITE[0], lon=SITE[1]):
    """Global horizontal irradiance (W/m2): Haurwitz clear-sky x Kasten-Czeplak cloud factor."""
    doy = dt_utc.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(math.radians(360 / 365 * (doy - 81)))
    b = math.radians(360 / 365 * (doy - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)       # minutes
    solar_time = dt_utc.hour + 0.5 + (lon / 15) + eot / 60                      # mid-hour
    ha = math.radians(15 * (solar_time - 12))
    el = math.asin(math.sin(math.radians(lat)) * math.sin(decl) +
                   math.cos(math.radians(lat)) * math.cos(decl) * math.cos(ha))
    if el <= 0:
        return 0.0
    clear = 1098 * math.sin(el) * math.exp(-0.057 / math.sin(el))
    return round(clear * (1 - 0.75 * (sky_pct / 100) ** 3.4), 1)


def ground_temp_f():
    """Mean KFDK air temperature over the past 72 h (F) -- stand-in for shallow ground temperature."""
    start = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    obs = get(f"https://api.weather.gov/stations/KFDK/observations?start={start}")
    temps = [f["properties"]["temperature"]["value"] for f in obs["features"]
             if f["properties"].get("temperature", {}).get("value") is not None]
    if not temps:
        return None, 0
    c = sum(temps) / len(temps)
    return round(c * 9 / 5 + 32, 1), len(temps)


def grid_weather(grid_url, hours_utc, soil_f):
    p = get(grid_url)["properties"]
    c2f = lambda c: None if c is None else c * 9 / 5 + 32
    air = fill([c2f(x) for x in hourly(p.get("temperature"), hours_utc)], 50)
    dew = fill([c2f(x) for x in hourly(p.get("dewpoint"), hours_utc)], 40)
    sky = fill(hourly(p.get("skyCover"), hours_utc), 50)
    qpf = [(x or 0) / 25.4 for x in hourly(p.get("quantitativePrecipitation"), hours_utc, amount=True)]
    snow = [(x or 0) / 25.4 for x in hourly(p.get("snowfallAmount"), hours_utc, amount=True)]
    ice = [(x or 0) / 25.4 for x in hourly(p.get("iceAccumulation"), hours_utc, amount=True)]
    wind = fill([None if x is None else x / 1.609 for x in hourly(p.get("windSpeed"), hours_utc)], 5)
    pop = fill(hourly(p.get("probabilityOfPrecipitation"), hours_utc), 0)
    rows = []
    for k, h in enumerate(hours_utc):
        liquid = max(qpf[k], snow[k] / 10)            # ~10:1 snow-to-liquid if QPF is missing
        rows.append({
            "t": h.astimezone(TZ).strftime("%Y-%m-%dT%H:%M"),
            "air": round(air[k], 1), "dew": round(min(dew[k], air[k]), 1),
            "precip": round(liquid, 3), "rain": round(max(0.0, qpf[k] - snow[k] / 10), 3),
            "snow": round(snow[k], 3), "ice": round(ice[k], 3), "cloud": round(sky[k]),
            "sw": solar_ghi(h, sky[k]), "wind": round(wind[k], 1), "pop": pop[k], "soil": soil_f})
    return rows, p.get("updateTime")


def main(drill=0.0):
    rep = json.load(open(os.path.join(DATA, "replay.json")))
    segs, hosp = rep["segments"], rep["hospitals"]

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours_utc = [now + timedelta(hours=k) for k in range(HOURS)]

    soil, n_obs = ground_temp_f()
    soil_src = f"KFDK mean air temperature, past 72 h ({n_obs} observations)"

    grids, by_hosp, issued = {}, {}, set()
    for h in hosp:
        pt = get(f"https://api.weather.gov/points/{h['lat']:.4f},{h['lon']:.4f}")["properties"]
        key = f"{pt['gridId']}/{pt['gridX']},{pt['gridY']}"
        if key not in grids:
            if soil is None:
                soil = 45.0
                soil_src = "fallback 45 F (no KFDK observations returned)"
            grids[key], upd = grid_weather(pt["forecastGridData"], hours_utc, soil - drill)
            for w in grids[key]:
                w["air"] = round(w["air"] - drill, 1)
                w["dew"] = round(w["dew"] - drill, 1)
            issued.add(upd)
        by_hosp[h["id"]] = key
        h["grid"] = key

    # citywide series = mean across hospital grid cells (drives storm timing and the chart)
    keys = list(grids)
    city = []
    for k in range(HOURS):
        rows = [grids[g][k] for g in keys]
        m = {f: round(sum(r[f] for r in rows) / len(rows), 3) for f in
             ("air", "dew", "precip", "rain", "snow", "ice", "cloud", "sw", "wind", "pop", "soil")}
        m["precip"] = max(r["precip"] for r in rows)   # any cell wet -> storm clock starts
        m["snow"] = max(r["snow"] for r in rows)
        city.append({"t": rows[0]["t"], **m})

    seg_wx = [grids[by_hosp[s["hosp"]]] for s in segs]
    pav = [thermal.pavement_series(seg_wx[i], s, s.get("gee")) for i, s in enumerate(segs)]
    med = [sorted(p[t] for p in pav)[len(pav) // 2] for t in range(HOURS)]
    onset, end = sm.storm_bounds(city)

    out = {k: rep[k] for k in ("hospitals", "segments", "route_cycle_h", "validation", "gee", "t_night", "t_day")}
    out.update({"hours": [w["t"] for w in city], "weather": city, "onset": onset, "end": end,
                "pavement": {"grade": thermal.pavement_series(city, {"bridge": False}, None),
                             "bridge": thermal.pavement_series(city, {"bridge": True}, None)},
                "pavement_median": med, "seg_pavement": [[round(v * 10) for v in p] for p in pav],
                "strategies": {}, "sensitivity": [],
                "grid_weather": grids})        # each hospital's own NWS cell (hospitals[].grid points here)
    actions, materials = [], []

    def code(lst, v):
        if v not in lst:
            lst.append(v)
        return lst.index(v)

    for strat in ("blanket", "advisor"):
        risk, events = sm.simulate(segs, city, pav, strat, seg_wx=seg_wx)
        out["strategies"][strat] = {
            "risk": ["".join(map(str, r)) for r in risk],
            "events": [[t, i, code(actions, a), code(materials, m), round(r), round(tf)] for (t, i, a, m, r, tf) in events],
            "totals": sm.totals(segs, city, risk, events)}
    out["actions"], out["materials"] = actions, materials

    coldest = min(range(len(segs)), key=lambda i: min(pav[i]))
    tmin = min(pav[coldest])
    adv = out["strategies"]["advisor"]["totals"]
    out["live"] = {
        "fetched": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M"),
        "issued": sorted(x for x in issued if x)[-1] if any(issued) else None,
        "office": "NWS Baltimore/Washington (Sterling, VA)", "grids": keys, "soil_f": soil, "soil_source": soil_src,
        "min_pavement_f": tmin, "min_pavement_road": segs[coldest]["name"],
        "min_pavement_hosp": next(h["name"] for h in hosp if h["id"] == segs[coldest]["hosp"]),
        "min_pavement_time": city[min(range(HOURS), key=lambda t: pav[coldest][t])]["t"],
        "min_air_f": min(w["air"] for w in city), "total_snow_in": round(sum(w["snow"] for w in city), 2),
        "total_precip_in": round(sum(w["precip"] for w in city), 2),
        "freeze_hours": sum(1 for t in range(HOURS) if med[t] <= 32),
        "drill_f": drill,
    }
    blob = json.dumps(out, separators=(",", ":"))
    open(os.path.join(DATA, "live.js"), "w").write("window.LIVE=" + blob + ";")
    summary = (("DRILL -%g F | " % drill if drill else "") + f"NWS forecast {out['live']['issued']} | {len(keys)} grid cells | next {HOURS} h: "
               f"air low {out['live']['min_air_f']:.0f}F, coldest road {tmin:.0f}F ({segs[coldest]['name']}), "
               f"snow {out['live']['total_snow_in']} in | advisor salt {adv['salt_tons']} t, "
               f"{adv['applications']} applications")
    print(summary)
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", type=float, default=0.0, help="shift the forecast this many F colder (rehearsal only)")
    main(ap.parse_args().drill)
