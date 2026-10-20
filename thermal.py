"""Segment-level pavement temperature engine (the "microclimate" layer).

Every road segment gets its own hourly pavement temperature from the shared weather plus its
Google Earth Engine variables (gee_features.py). Each term is a transparent, documented
assumption, not a fitted model -- Round 2 fits these coefficients to RWIS observations.

  T_eq = air
       + SUN    0.022 F per W/m2 x sun exposure      (terrain shadow x leaf-off canopy x urban canyon)
       + GROUND 0.35 x (soil - air)                  (0 on bridges: no ground under the deck)
       - SKY    night radiative loss x sky view      (trees/buildings block the cold sky)
       + UHI    1.5 F x impervious share             (stored heat in dense pavement/buildings)
       - LAPSE  0.012 F per m above 20 m             (~3.6 F per 1000 ft)
       + SAT    0.25 x MODIS night anomaly (night) / 0.15 x Landsat day anomaly (day)
                                                     (measured, persistent warm/cold spots)
  T_pav(t) = T_pav(t-1) + lag x (T_eq - T_pav(t-1))  (bridges respond faster: thin deck)
"""

SUN_K, GROUND_K, UHI_K, LAPSE_K = 0.022, 0.35, 1.5, 0.012
SAT_NIGHT_K, SAT_DAY_K = 0.25, 0.15
FROST_WATER_M = 150


def exposure(f):
    """Fraction of winter sun reaching the road and fraction of sky it 'sees' at night."""
    if not f:
        return 1.0, 1.0
    canopy = (f.get("canopy_pct") or 0) / 100
    built = f.get("built_h_m") or 0
    lit = f.get("sun_lit_frac", 1.0)
    lit = 1.0 if lit is None else lit
    north = max(0.0, f.get("northness") or 0) * min(1.0, (f.get("grade_pct") or 0) / 8)
    sun = lit * (1 - 0.5 * canopy) * (1 - min(0.6, built / 25)) * (1 - 0.3 * north)  # leaf-off trees pass ~50%
    svf = (1 - 0.4 * canopy) * (1 - min(0.5, built / 40))
    return sun, svf


def terms(w, seg, f):
    """Equilibrium temperature terms (F) for one hour. Returns dict of named contributions."""
    bridge = seg["bridge"]
    sun_x, svf = exposure(f)
    night = w["sw"] < 20
    t = {"air": w["air"]}
    t["sun"] = SUN_K * w["sw"] * sun_x
    t["ground"] = 0.0 if bridge else GROUND_K * (w["soil"] - w["air"])
    sky = (1 - w["cloud"] / 100) * (5.0 if bridge else 3.0) if night else 0.0
    t["sky"] = -sky * svf
    if f:
        t["uhi"] = UHI_K * (f.get("impervious_pct") or 0) / 100
        t["lapse"] = -LAPSE_K * ((f.get("elev_m") or 20) - 20)
        a = f.get("lst_night_anom_f") if night else f.get("lst_day_anom_f")
        t["sat"] = (SAT_NIGHT_K if night else SAT_DAY_K) * (a or 0)
    return t


def pavement_series(wx, seg, f):
    out, tp = [], None
    lag = 0.7 if seg["bridge"] else 0.4
    for w in wx:
        eq = sum(terms(w, seg, f).values())
        if w["snow"] > 0:
            eq = min(eq, 32.0)           # falling snow pins the surface near freezing
        tp = eq if tp is None else tp + lag * (eq - tp)
        out.append(round(tp, 1))
    return out


def explain(wx, seg, f, t):
    """'Why is this road warm/cold at hour t' vs an open, sunny, at-grade road with no
    Earth Engine adjustments -- the old two-class model."""
    w = wx[t]
    mine = terms(w, seg, f)
    ref = terms(w, {"bridge": False}, None)
    parts = {
        "Bridge deck (no ground heat)": (mine["ground"] - ref["ground"]) + (mine["sky"] - ref["sky"] if seg["bridge"] else 0),
        "Shade (terrain, trees, buildings)": mine["sun"] - ref["sun"],
        "Sheltered from night sky": 0 if seg["bridge"] else mine["sky"] - ref["sky"],
        "Urban heat island": mine.get("uhi", 0),
        "Elevation": mine.get("lapse", 0),
        "Satellite-measured warm/cold spot": mine.get("sat", 0),
    }
    return {k: round(v, 1) for k, v in parts.items() if abs(v) >= 0.05}


def frost_prone(f):
    return bool(f) and (f.get("water_dist_m") or 1e9) < FROST_WATER_M


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** .5
    vy = sum((b - my) ** 2 for b in ry) ** .5
    return cov / (vx * vy) if vx and vy else 0.0
