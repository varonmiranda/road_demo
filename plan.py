"""Shared campus planner: turn a weather series + the surface inventory into the data the
dashboard renders -- per-surface hourly temperature, ice risk and a recommendation.

Both modes use this:
  - simulate.py   replays the recorded Jan 2025 storm weather (storm mode)
  - live_plan.py  uses the NWS forecast (live mode)

Risk levels (colour the map and the legend)
  0 Low        surface stays above freezing
  1 Watch      dry but the dew point reaches the surface -> frost possible
  2 Treat soon wet and near freezing, or snow on the way
  3 Treat now  wet/icy at or below freezing

Recommendations: none | watch | brine | salt | sand | plow
"""
import thermal

RISK_LABELS = ["Low", "Watch", "Treat soon", "Treat now"]
RISK_SHORT = ["low", "watch", "soon", "now"]
REC_LABELS = {
    "none": "No action", "watch": "Monitor", "brine": "Pre-treat with brine",
    "salt": "Salt", "sand": "Sand", "plow": "Plow / shovel",
}
WET_HOURS = 8          # surface counts as wet for this many hours after precipitation


def wet_flags(wx):
    last, out = -99, []
    for t, w in enumerate(wx):
        if w["precip"] > 0:
            last = t
        out.append(t - last < WET_HOURS)
    return out


def risk_at(T, wet, dew):
    if T > 34:
        return 0
    if not wet:
        return 1 if dew >= T - 1 else 0     # dry: black ice if the dew point meets the surface
    return 3 if T < 30 else 2


def recommend(risk, T, snow, priority):
    """(code, text) for one surface at one hour."""
    if risk == 0:
        return "none", "No action"
    if risk == 1:
        return "watch", "Monitor — frost possible"
    if risk == 3:
        if snow > 0.05:
            return "plow", "Plow / shovel, then salt"
        if T < 22:
            return "sand", "Sand now (salt too cold to work)"
        return "salt", "Salt now"
    # risk == 2
    if snow > 0.05:
        return "plow", "Plow / shovel before it refreezes"
    return "brine", "Pre-treat with brine before onset"


ASSUMPTIONS = [
    "Surface temperature is an estimate, not a measurement. It starts from the air temperature "
    "and nudges toward an equilibrium each hour.",
    "Elevated, exposed surfaces (parking-garage top deck, ramps) cool faster and lose more heat "
    "to the clear night sky than ground-level pavement.",
    "Concrete walkways absorb less sun than asphalt and shed heat faster (lower mass).",
    "Shaded surfaces (next to a tall building) stay colder by day and are marked from OpenStreetMap "
    "building heights where available.",
    "Ice risk = surface temperature, whether the surface is wet (precipitation within 8 hours) and "
    "the dew point. Recommendations follow the risk band, highest-priority surfaces first.",
]


def build(mode, hospital, boundary, surfaces, weather, extra=None):
    """Return the dashboard blob for one mode."""
    wet = wet_flags(weather)
    out_surfaces = []
    for s in surfaces:
        if not s.get("osm"):
            out_surfaces.append({**s, "temp": [], "risk": [], "rec": []})
            continue
        temp = thermal.surface_series(weather, s)
        risk, rec = [], []
        for t, w in enumerate(weather):
            r = risk_at(temp[t], wet[t], w["dew"])
            code, _ = recommend(r, temp[t], w["snow"], s["priority"])
            risk.append(r)
            rec.append(code)
        out_surfaces.append({**s, "temp": temp, "risk": risk, "rec": rec})

    blob = {
        "mode": mode,
        "hospital": hospital,
        "boundary": boundary,
        "hours": [w["t"] for w in weather],
        "weather": weather,
        "surfaces": out_surfaces,
        "risk_labels": RISK_LABELS,
        "rec_labels": REC_LABELS,
        "assumptions": ASSUMPTIONS,
    }
    if extra:
        blob.update(extra)
    return blob
