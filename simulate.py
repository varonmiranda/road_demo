"""Step 2: replay the Jan 6, 2025 Frederick, MD snowstorm hour by hour, segment by segment,
under two strategies, and write data/replay.json for the demo page.

  BLANKET  - typical fixed practice: brine the day before, dry rock salt at a fixed
             rate on every plow pass, one post-storm pass. Same everywhere.
  ADVISOR  - our rules engine: pavement-temperature bands, anti-ice close to onset,
             top up to a target (counts residual salt), material switch below ~20F,
             priority ordering (bridges / grades / ER doors first), refreeze patrol.

Both strategies get the SAME trucks and route cycle (3 h). Only the decisions differ.

Weather: Open-Meteo historical (ERA5 reanalysis) used as a stand-in for a perfect
forecast. Pavement temperature: per-segment heat-balance engine (see thermal.py),
NOT a measured value -- Round 2 replaces it with an ML model trained on RWIS data.
All coefficients below are model assumptions to be stated as such.
"""
import json
import math
import os
from datetime import datetime, timedelta

import requests

import thermal

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data")

START = datetime(2025, 1, 5, 8)
END = datetime(2025, 1, 7, 18)
LAT, LON = 39.42296, -77.41485   # Frederick Health Hospital

ROUTE_CYCLE_H = 3          # every truck route takes 3 h to complete -> offsets 0..2
TREATED_LB = 30            # effective lb/lane-mi needed to keep a thin water film from bonding
BLANKET_RATE = 200         # fixed dry-salt rate per pass (lb/lane-mi); sensitivity run at 150/250
BRINE_LB_PER_GAL = 2.33    # 23.3% NaCl brine
LOSS = {"dry": 0.30, "prewet": 0.12, "brine": 0.05, "blend": 0.12}  # scatter/bounce at application


# ---------------------------------------------------------------- weather
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
                # Open-Meteo snowfall is in cm even with inch precip unit? -> it is inches here
                "snow": h["snowfall"][i], "cloud": h["cloud_cover"][i],
                "sw": h["shortwave_radiation"][i], "wind": h["wind_speed_10m"][i],
                "soil": h["soil_temperature_0_to_7cm"][i]})
    return rows


# ---------------------------------------------------------------- salt physics (simplified)
def eff_nacl(T):
    """Fraction of rock salt's melting power available at pavement temp T (F).
    Shape follows Minnesota PCA / research table: strong 25-32, falls fast below 20, ~nil <10."""
    pts = [(10, 0.05), (15, 0.2), (20, 0.45), (25, 0.8), (32, 1.0)]
    return interp(T, pts)


def eff_cacl(T):
    return interp(T, [(-20, 0.3), (0, 0.8), (15, 1.0), (32, 1.0)])


def interp(x, pts):
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def target_lb(T, seg):
    """Advisor target effective chemical (lb/lane-mi) by pavement temp band
    -- midpoints of the Minnesota Snow & Ice Control Handbook ranges for snow."""
    if T >= 25:
        base = 120
    elif T >= 20:
        base = 175
    elif T >= 15:
        base = 240
    else:
        base = 300
    if seg["bridge"] or seg["grade"] >= 6:
        base *= 1.3
    elif seg["tier"] == 3:
        base *= 0.6
    return base


def bond_need(g):
    """Effective chemical needed to stop a bond, rising with snow sitting on the road."""
    return TREATED_LB * (1 + 0.35 * g.snow)


class Seg:
    def __init__(self, s):
        self.s = s
        self.snow = 0.0     # inches on the road
        self.nacl = 0.0     # lb/lane-mi residual
        self.cacl = 0.0
        self.bonded = False
        self.wet_h = 99     # hours since last moisture (start dry)

    def eff(self, T):
        return self.nacl * eff_nacl(T) + self.cacl * eff_cacl(T)


# ---------------------------------------------------------------- strategies
def storm_bounds(wx):
    """First and last hour with precipitation, or (None, None) when the window is dry."""
    idx = [i for i, w in enumerate(wx) if w["snow"] > 0 or w["precip"] > 0]
    return (idx[0], idx[-1]) if idx else (None, None)


def plow_passes(wx, onset, end):
    """Shared plow schedule: a pass starts every route cycle once snow is falling."""
    if onset is None:
        return []
    passes, t = [], onset + 1
    while t <= end:
        passes.append(t)
        t += ROUTE_CYCLE_H
    passes.append(end + 1)  # cleanup pass after the storm
    return passes


def simulate(segs_raw, wx, pav, strategy, blanket_rate=BLANKET_RATE, seg_wx=None):
    """seg_wx: optional per-segment weather (live mode gives each hospital its own NWS grid cell);
    storm timing and plow passes always follow the citywide series `wx`."""
    H = len(wx)
    onset, end = storm_bounds(wx)
    W = (lambda i: seg_wx[i]) if seg_wx else (lambda i: wx)
    segs = [Seg(s) for s in segs_raw]

    # route offsets: same capacity both ways. Blanket = fixed route order (by id);
    # advisor = priority order (highest priority treated in the first hour of a pass).
    if strategy == "advisor":
        order = sorted(range(len(segs)), key=lambda i: -segs_raw[i]["priority"])
    else:
        order = list(range(len(segs)))
    offset = [0] * len(segs)
    for rank, i in enumerate(order):
        offset[i] = rank * ROUTE_CYCLE_H // len(segs)

    # scheduled per-segment actions: hour -> list of (seg_idx, kind)
    sched = [[] for _ in range(H + ROUTE_CYCLE_H + 2)]
    plows = plow_passes(wx, onset, end)
    if onset is None:
        pass                                  # dry window: no pre-treatment or plow passes
    elif strategy == "blanket":
        pre = max(0, onset - 14)              # day-before brine run (or now, if the storm is sooner)
        for i in range(len(segs)):
            sched[pre + offset[i]].append((i, "pretreat"))
            for p in plows:
                sched[p + offset[i]].append((i, "plow_salt"))
    else:
        pre = max(0, onset - 4)               # anti-ice finishes >= 1 h before first flakes
        for i in range(len(segs)):
            sched[pre + offset[i]].append((i, "pretreat"))
            for p in plows:
                sched[p + offset[i]].append((i, "plow_salt"))

    risk = [[0] * H for _ in segs]
    events = []          # (hour, seg, action, material, rate lb/ln-mi, pavement temp the rate was sized for)

    def bridge_or_grade(s):
        return s["bridge"] or s["grade"] >= 6

    for t in range(H):
        for i, g in enumerate(segs):
            s = g.s
            if s["tunnel"]:
                continue
            T = pav[i][t]
            w = W(i)[t]
            # 1. weather acts on the segment
            if w["precip"] > 0:
                g.wet_h = 0
            else:
                g.wet_h += 1
            # traffic carry-off every hour; liquid rain washes brine away. Snow dilution is
            # handled in bond_need(): the more snow sits on the road, the more chemical it takes.
            dil = min(0.6, 0.03 + 3 * w["rain"])
            g.nacl *= 1 - dil
            g.cacl *= 1 - dil
            if T <= 32:
                g.snow += w["snow"]
            else:
                g.snow *= 0.5
                g.bonded = False

        # 2. scheduled actions
        for (i, kind) in sched[t]:
            g, s = segs[i], segs[i].s
            if s["tunnel"]:
                continue
            T = pav[i][t]
            look = [pav[i][k] for k in range(t, min(H, t + 12))]
            if kind == "pretreat":
                if strategy == "blanket":
                    rate = 40 * BRINE_LB_PER_GAL
                    apply(g, rate, "brine"); events.append((t, i, "Anti-ice (brine, day before)", "NaCl brine 40 gal/ln-mi", rate, T))
                else:
                    t_on = min(pav[i][onset:onset + 3])
                    if t_on > 33:
                        events.append((t, i, "Skip anti-ice (pavement forecast above freezing)", "-", 0, t_on))
                        continue
                    gal = 35 if bridge_or_grade(s) else 20 if s["tier"] == 3 else 25
                    rate = gal * BRINE_LB_PER_GAL
                    apply(g, rate, "brine"); events.append((t, i, "Anti-ice before onset", f"NaCl brine {gal} gal/ln-mi", rate, t_on))
            elif kind == "plow_salt":
                g.snow = 0.3 if g.bonded else 0.1
                g.nacl *= 0.75
                g.cacl *= 0.75
                if strategy == "blanket":
                    rate = blanket_rate
                    apply(g, rate, "dry"); events.append((t, i, "Plow + salt (fixed rate)", "Dry rock salt", rate, T))
                else:
                    tmin = min(look)
                    need = target_lb(tmin, s)
                    blend = tmin < 20
                    eff_new = (0.8 * eff_nacl(tmin) + 0.2 * eff_cacl(tmin)) if blend else eff_nacl(tmin)
                    have = g.eff(tmin)
                    if have >= need:
                        events.append((t, i, "Plow only (residual salt sufficient)", "-", 0, tmin))
                        continue
                    loss = LOSS["blend" if blend else "prewet"]
                    rate = min(400, (need - have) / max(eff_new, 0.05) / (1 - loss))
                    rate = round(rate / 10) * 10
                    if rate < 30:
                        events.append((t, i, "Plow only (residual salt sufficient)", "-", 0, tmin))
                        continue
                    if blend:
                        apply(g, rate, "blend")
                        events.append((t, i, "Plow + cold-weather blend", "Salt prewet w/ CaCl2 (80/20)", rate, tmin))
                    else:
                        apply(g, rate, "prewet")
                        events.append((t, i, "Plow + salt", "Prewetted rock salt", rate, tmin))
                if g.eff(T) >= TREATED_LB * 1.5:
                    g.bonded = False

        # 3a. advisor frost / black-ice watch: dry pavement at or below freezing with the dew point
        #     reaching the surface temperature in the next 3 h -> light brine on tier 1-2 now
        if strategy == "advisor":
            for i, g in enumerate(segs):
                s = g.s
                if s["tunnel"] or s["tier"] == 3 or g.snow > 0.05 or g.wet_h < 8:
                    continue
                ahead = range(t, min(H, t + 4))
                frost = any(pav[i][k] <= 32 and W(i)[k]["dew"] >= pav[i][k] - 1 for k in ahead)
                if frost and g.eff(pav[i][t]) < TREATED_LB:
                    gal = 25 if s["bridge"] or s["grade"] >= 6 else 20
                    rate = gal * BRINE_LB_PER_GAL
                    apply(g, rate, "brine")
                    events.append((t, i, "Frost / black-ice anti-ice", f"NaCl brine {gal} gal/ln-mi", rate, min(pav[i][k] for k in ahead)))

        # 3b. advisor refreeze patrol: after the storm (or in a dry window), spot-treat segments heading to ice
        if strategy == "advisor" and (end is None or t > end + 1):
            for i, g in enumerate(segs):
                s = g.s
                if s["tunnel"] or s["tier"] == 3:
                    continue
                T = pav[i][t]
                wet = g.snow > 0.05 or g.wet_h < 8
                if wet and T <= 32 and g.eff(T) < TREATED_LB * 1.1:
                    look = [pav[i][k] for k in range(t, min(H, t + 12))]
                    tmin = min(look)
                    blend = tmin < 20
                    eff_new = (0.8 * eff_nacl(tmin) + 0.2 * eff_cacl(tmin)) if blend else eff_nacl(tmin)
                    rate = round(min(300, (target_lb(tmin, s) * 0.6 - g.eff(tmin)) / max(eff_new, .05) / 0.88) / 10) * 10
                    if rate >= 30:
                        apply(g, rate, "blend" if blend else "prewet")
                        events.append((t, i, "Refreeze patrol spot-treat",
                                       "Salt prewet w/ CaCl2 (80/20)" if blend else "Prewetted rock salt", rate, tmin))
                        if g.eff(T) >= TREATED_LB * 1.5:
                            g.bonded = False

        # 4. resulting road state
        for i, g in enumerate(segs):
            s = g.s
            if s["tunnel"]:
                continue
            T = pav[i][t]
            wet = g.snow > 0.05 or g.wet_h < 8
            e = g.eff(T)
            if T > 33 and g.snow < 0.5:
                r = 0                          # above freezing
            elif not wet:
                r = 1 if W(i)[t]["dew"] >= T else 0   # dry; frost possible if dew point >= pavement
            elif g.bonded:
                r = 3
            elif e < bond_need(g):
                g.bonded = True                # untreated wet pavement below freezing -> ice bond
                r = 3
            elif g.snow >= 1.5:
                r = 2                          # treated, but snow building up -> needs plow
            else:
                r = 1                          # wet/slushy, treated
            risk[i][t] = r

    return risk, events


def apply(g, rate, kind):
    kept = rate * (1 - LOSS[kind])
    if kind == "blend":
        g.nacl += 0.8 * kept
        g.cacl += 0.2 * kept
    else:
        g.nacl += kept


# ---------------------------------------------------------------- run + export
def totals(segs, wx, risk, events):
    lbs = sum(e[4] * segs[e[1]]["lane_mi"] for e in events)
    cacl = sum(0.2 * e[4] * segs[e[1]]["lane_mi"] for e in events if "CaCl2" in e[3])
    icy = [sum(segs[i]["lane_mi"] for i in range(len(segs)) if risk[i][t] == 3) for t in range(len(wx))]
    icy_t1 = sum(segs[i]["lane_mi"] for i in range(len(segs)) for t in range(len(wx))
                 if risk[i][t] == 3 and segs[i]["tier"] == 1)
    return {"salt_tons": round(lbs / 2000, 1), "cacl_tons": round(cacl / 2000, 1),
            "icy_lane_mile_hours": round(sum(icy), 1), "icy_lane_mile_hours_tier1": round(icy_t1, 1),
            "applications": sum(1 for e in events if e[4] > 0),
            "skipped": sum(1 for e in events if e[4] == 0)}


def main():
    base = json.load(open(os.path.join(DATA, "segments.json")))
    segs, hosp = base["segments"], base["hospitals"]
    wx = load_weather()
    onset, end = storm_bounds(wx)

    # Google Earth Engine layer (optional: falls back to the two-class model without it)
    gpath = os.path.join(DATA, "gee_features.json")
    gee = {int(k): v for k, v in json.load(open(gpath)).items()} if os.path.exists(gpath) else {}
    print("Earth Engine features:", f"{len(gee)} segments" if gee else "NOT FOUND - using two-class model")
    for s in segs:
        f = gee.get(s["id"])
        if f:
            s["grade"] = f.get("grade_pct", s["grade"])     # 10 m 3DEP replaces the 90 m DEM
            s["frost_prone"] = thermal.frost_prone(f)
        s["gee"] = f

    pav = [thermal.pavement_series(wx, s, s["gee"]) for s in segs]
    # cold index: how much colder than the corridor median this segment runs on freezing hours
    med = [sorted(p[t] for p in pav)[len(pav) // 2] for t in range(len(wx))]
    cold_hours = [t for t in range(len(wx)) if med[t] <= 32]
    t_night = wx.index(next(w for w in wx if w["t"].endswith("01-07T06:00")))
    t_day = wx.index(next(w for w in wx if w["t"].endswith("01-07T13:00")))
    for i, s in enumerate(segs):
        s["cold_idx"] = round(sum(pav[i][t] - med[t] for t in cold_hours) / len(cold_hours), 2)
        steep = s["grade"] >= 6
        if s["bridge"] or steep or s["dist_m"] <= 150:
            s["tier"] = 1
        elif s["cls"] != "Local street":
            s["tier"] = 2
        else:
            s["tier"] = 3
        # re-rank: colder-than-typical segments and frost-prone segments move up the queue
        s["priority"] = round(s["priority"] * (1 + 0.08 * max(0, -s["cold_idx"]))
                              * (1.1 if s.get("frost_prone") else 1), 3)
        s["why_night"] = thermal.explain(wx, s, s["gee"], t_night)
        s["why_day"] = thermal.explain(wx, s, s["gee"], t_day)

    validation = None
    pairs = [(s["cold_idx"], s["gee"]["snow_persist"]) for s in segs
             if s["gee"] and s["gee"].get("snow_persist") is not None and s["gee"]["snow_persist"] >= 0]
    if len(pairs) > 30:
        x, y = zip(*pairs)
        q = sorted(x)[len(x) // 10]
        cold = [b for a, b in pairs if a <= q]
        rest = [b for a, b in pairs if a > q]
        validation = {"n": len(pairs), "spearman": round(thermal.spearman(x, y), 3),
                      "snow_coldest_decile": round(sum(cold) / len(cold), 3),
                      "snow_other": round(sum(rest) / len(rest), 3)}
        print("validation vs Sentinel-2 snow persistence:", validation)

    pav_ref = {"grade": thermal.pavement_series(wx, {"bridge": False}, None),
               "bridge": thermal.pavement_series(wx, {"bridge": True}, None)}
    out = {"hours": [w["t"] for w in wx], "weather": wx, "pavement": pav_ref, "pavement_median": med,
           "seg_pavement": [[round(v * 10) for v in p] for p in pav], "validation": validation,
           "gee": bool(gee), "t_night": t_night, "t_day": t_day,
           "onset": onset, "end": end, "route_cycle_h": ROUTE_CYCLE_H, "hospitals": hosp,
           "segments": [{k: s[k] for k in ("id", "name", "cls", "hosp", "dist_m", "lanes", "lane_mi",
                                           "bridge", "tunnel", "grade", "tier", "priority", "coords",
                                           "cold_idx", "why_night", "why_day")}
                        | {"frost_prone": s.get("frost_prone", False),
                           "gee": {k: v for k, v in (s["gee"] or {}).items()}}
                        for s in segs],
           "strategies": {}}

    actions, materials = [], []

    def code(lst, v):
        if v not in lst:
            lst.append(v)
        return lst.index(v)

    for strat in ("blanket", "advisor"):
        risk, events = simulate(segs, wx, pav, strat)
        out["strategies"][strat] = {
            "risk": ["".join(map(str, r)) for r in risk],
            "events": [[t, i, code(actions, a), code(materials, m), round(rate), round(tf)] for (t, i, a, m, rate, tf) in events],
            "totals": totals(segs, wx, risk, events)}
        print(strat, out["strategies"][strat]["totals"])
    out["actions"], out["materials"] = actions, materials

    # sensitivity of the headline number to the assumed blanket rate
    adv = out["strategies"]["advisor"]["totals"]["salt_tons"]
    out["sensitivity"] = []
    for rate in (150, 200, 250):
        r, ev = simulate(segs, wx, pav, "blanket", blanket_rate=rate)
        tot = totals(segs, wx, r, ev)
        out["sensitivity"].append({"rate": rate, "blanket_tons": tot["salt_tons"],
                                   "saving_pct": round(100 * (1 - adv / tot["salt_tons"]))})
    print("sensitivity", out["sensitivity"])

    blob = json.dumps(out, separators=(",", ":"))
    open(os.path.join(DATA, "replay.json"), "w").write(blob)
    open(os.path.join(DATA, "replay.js"), "w").write("window.REPLAY=" + blob + ";")  # works from file://
    cold = sorted(segs, key=lambda s: s["cold_idx"])[:5]
    print("coldest segments:", [(s["name"], s["cold_idx"]) for s in cold])


if __name__ == "__main__":
    main()
