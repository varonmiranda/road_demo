"""Storm mode: replay the recorded Jan 2025 Frederick, MD snowstorm hour by hour over the
hospital campus surfaces, and write data/replay.json / data/replay.js for the demo page.

Weather: Open-Meteo historical (ERA5 reanalysis) used as a stand-in for a perfect forecast.
Surface temperature: the campus surface engine (see thermal.surface_series), an estimate built
from stated assumptions -- not a measured value.

Run AFTER build_surfaces.py:

    python3 simulate.py
"""
import json
import os
from datetime import datetime

import requests

import plan

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

START = datetime(2025, 1, 5, 8)
END = datetime(2025, 1, 7, 18)
LAT, LON = 39.42296, -77.41485   # Frederick Health Hospital


def load_weather():
    path = os.path.join(DATA, "weather_2025-01-05_07.json")
    if not os.path.exists(path):
        r = requests.get("https://archive-api.open-meteo.com/v1/archive", params={
            "latitude": LAT, "longitude": LON, "start_date": "2025-01-05", "end_date": "2025-01-07",
            "hourly": "temperature_2m,dew_point_2m,precipitation,rain,snowfall,cloud_cover,"
                      "shortwave_radiation,wind_speed_10m,soil_temperature_0_to_7cm",
            "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
            "wind_speed_unit": "mph", "timezone": "America/New_York"}, timeout=60)
        r.raise_for_status()
        json.dump(r.json(), open(path, "w"))
    h = json.load(open(path))["hourly"]
    rows = []
    for i, t in enumerate(h["time"]):
        dt = datetime.fromisoformat(t)
        if START <= dt <= END:
            rows.append({
                "t": t, "air": h["temperature_2m"][i], "dew": h["dew_point_2m"][i],
                "precip": h["precipitation"][i], "rain": h["rain"][i],
                "snow": h["snowfall"][i], "cloud": h["cloud_cover"][i],
                "sw": h["shortwave_radiation"][i], "wind": h["wind_speed_10m"][i],
                "pop": 0, "soil": h["soil_temperature_0_to_7cm"][i]})
    return rows


def storm_bounds(wx):
    """First and last hour with precipitation, or (None, None) when the window is dry."""
    idx = [i for i, w in enumerate(wx) if w["snow"] > 0 or w["precip"] > 0]
    return (idx[0], idx[-1]) if idx else (None, None)


def main():
    base = json.load(open(os.path.join(DATA, "surfaces.json")))
    wx = load_weather()
    onset, end = storm_bounds(wx)
    blob = plan.build("replay", base["hospital"], base["boundary"], base["surfaces"], wx,
                      extra={"onset": onset, "end": end,
                             "source": "Replay of recorded weather from the Jan 5–7, 2025 "
                                       "Frederick, MD snowstorm."})
    out = json.dumps(blob, separators=(",", ":"))
    open(os.path.join(DATA, "replay.json"), "w").write(out)
    open(os.path.join(DATA, "replay.js"), "w").write("window.REPLAY=" + out + ";")
    n = sum(1 for s in blob["surfaces"] if s.get("osm"))
    print(f"replay: {n} campus surfaces over {len(wx)} hours; storm {onset}–{end}")


if __name__ == "__main__":
    main()
