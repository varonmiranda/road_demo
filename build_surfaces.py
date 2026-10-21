"""Step 1: build the hospital-campus surface inventory from OpenStreetMap.

The demo used to salt public streets around the hospital. It now works the hospital's own
grounds: the internal drives, parking lots and aisles, the parking-garage top deck, and the
pedestrian walkways, all inside the campus boundary taken from the `amenity=hospital` polygon.

Run:

    python3 build_surfaces.py     # needs network (api.openstreetmap.org), no osmnx

Sources
  - OpenStreetMap API /api/0.6/map for the campus bbox (Overpass is blocked in the sandbox;
    api.openstreetmap.org works).
  - Campus boundary   = the `amenity=hospital` way polygon.
  - Internal drives   = highway=service (service=driveway/alley/unset) inside the boundary
  - Parking aisles    = highway=service + service=parking_aisle inside the boundary
  - Parking garages   = amenity=parking + building=* + parking=multi-storey  -> "top deck"
  - Walkways          = highway=footway/path/pedestrian/steps inside the boundary

Surface types OpenStreetMap does not carry for this hospital (ED entrance & ambulance bay,
main entrance / drop-off loop, loading dock) are recorded as `osm: false` placeholders so the
page can say "not mapped yet" instead of falling back to public streets.

Writes
  - data/surfaces.json  the static surface inventory (geometry + static attributes)
  - data/campus.js      window.CAMPUS = {boundary, lots}  (boundary used by the page)
"""
import json
import math
import os
import xml.etree.ElementTree as ET

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.environ.get("RSD_CACHE", os.path.join(HERE, "cache"))
os.makedirs(CACHE, exist_ok=True)

# campus bbox (minlon,minlat,maxlon,maxlat) around Frederick Health Hospital
BBOX = (-77.4185, 39.4200, -77.4110, 39.4270)
HOSP = (39.42296, -77.41485)          # Frederick Health Hospital
M_PER_DEG_LAT = 111320.0

# surface-type metadata: label, priority (1 = treat first), default pavement, elevated?
KINDS = {
    "ed_entrance":  {"label": "ED entrance & ambulance bay", "priority": 1, "surface": "asphalt",  "elevated": False},
    "main_entrance":{"label": "Main entrance / drop-off",    "priority": 2, "surface": "asphalt",  "elevated": False},
    "ramp":         {"label": "Ramp / stairs",               "priority": 2, "surface": "concrete", "elevated": True},
    "driveway":     {"label": "Internal driveway",           "priority": 2, "surface": "asphalt",  "elevated": False},
    "sidewalk":     {"label": "Sidewalk / walkway",          "priority": 3, "surface": "concrete", "elevated": False},
    "garage_deck":  {"label": "Parking garage top deck",     "priority": 4, "surface": "asphalt",  "elevated": True},
    "parking_lot":  {"label": "Parking lot / aisle",         "priority": 5, "surface": "asphalt",  "elevated": False},
    "loading_dock": {"label": "Loading dock",                "priority": 6, "surface": "concrete", "elevated": False},
}

def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * M_PER_DEG_LAT,
                      (a[1] - b[1]) * M_PER_DEG_LAT * math.cos(math.radians(HOSP[0])))


def fetch_osm():
    path = os.path.join(CACHE, "campus.osm")
    if not os.path.exists(path):
        r = requests.get("https://api.openstreetmap.org/api/0.6/map",
                         params={"bbox": ",".join(str(v) for v in BBOX)},
                         headers={"User-Agent": "hospital-salting-advisor/1.0 (demo)"}, timeout=120)
        r.raise_for_status()
        open(path, "wb").write(r.content)
    return ET.parse(path).getroot()


def point_in_ring(lat, lon, ring):
    inside = False
    for i in range(len(ring)):
        y1, x1 = ring[i]
        y2, x2 = ring[(i + 1) % len(ring)]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def poly_centroid(ring):
    return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))


def line_len_m(coords):
    return sum(dist_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def poly_area_m2(ring):
    """Approximate planar area (shoelace) in m^2 -- fine at campus scale."""
    lat0 = sum(p[0] for p in ring) / len(ring)
    mx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    pts = [(p[1] * mx, p[0] * M_PER_DEG_LAT) for p in ring]
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def main():
    root = fetch_osm()
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}

    def tags(e):
        return {c.get("k"): c.get("v") for c in e.findall("tag")}

    def coords(w):
        return [nodes[n.get("ref")] for n in w.findall("nd") if n.get("ref") in nodes]

    ways = root.findall("way")
    boundary = next(coords(w) for w in ways if tags(w).get("amenity") == "hospital")

    # tall buildings near the campus (for the "shaded" assumption); heights from OSM only
    tall = []
    for w in ways:
        t = tags(w)
        if not t.get("building"):
            continue
        c = coords(w)
        if not c:
            continue
        lv = t.get("building:levels")
        h = t.get("height")
        h_m = float(h.split()[0]) if h and h.split() and h.split()[0].replace(".", "").isdigit() \
            else (float(lv) * 3.2 if lv and lv.replace(".", "").isdigit() else 0)
        if h_m >= 10 and any(point_in_ring(p[0], p[1], boundary) or
                             min(dist_m(p, q) for q in boundary) < 40 for p in c):
            tall.append((w.get("id"), c))

    def shaded(ring_or_line, self_id=None):
        return any(min(dist_m(p, q) for q in b) < 30
                   for wid, b in tall if wid != self_id for p in ring_or_line)

    surfaces = []
    sid = 0

    def add(kind, geom, coords_, name, surface_tag, src=None):
        nonlocal sid
        meta = KINDS[kind]
        rec = {
            "id": sid, "kind": kind, "label": meta["label"], "name": name or "",
            "geom": geom, "coords": [[round(p[0], 5), round(p[1], 5)] for p in coords_],
            "surface": surface_tag or meta["surface"],
            "elevated": meta["elevated"], "shaded": shaded(coords_, src),
            "priority": meta["priority"], "osm": True,
        }
        if geom == "polygon":
            rec["area_m2"] = round(poly_area_m2(coords_))
            rec["len_m"] = 0
        else:
            rec["len_m"] = round(line_len_m(coords_))
            rec["area_m2"] = 0
        surfaces.append(rec)
        sid += 1

    for w in ways:
        t = tags(w)
        c = coords(w)
        if len(c) < 2:
            continue
        mid = c[len(c) // 2]
        if not point_in_ring(mid[0], mid[1], boundary):
            continue
        hw = t.get("highway")
        svc = t.get("service", "")
        name = t.get("name")
        surf = t.get("surface")
        if hw == "service" and svc == "parking_aisle":
            add("parking_lot", "line", c, name, surf, w.get("id"))
        elif hw == "service":
            add("driveway", "line", c, name, surf, w.get("id"))
        elif hw == "steps":
            add("ramp", "line", c, name, surf, w.get("id"))
        elif hw in ("footway", "path", "pedestrian"):
            add("sidewalk", "line", c, name, surf, w.get("id"))
        elif t.get("amenity") == "parking" and t.get("building") and t.get("parking") == "multi-storey":
            if len(c) >= 3:
                add("garage_deck", "polygon", c, name, surf, w.get("id"))

    # surface types OpenStreetMap does not carry here -> placeholders ("not mapped yet")
    mapped = {s["kind"] for s in surfaces}
    for kind in [k for k in KINDS if k not in mapped]:
        meta = KINDS[kind]
        surfaces.append({
            "id": sid, "kind": kind, "label": meta["label"], "name": "",
            "geom": None, "coords": None, "surface": meta["surface"],
            "elevated": meta["elevated"], "shaded": False,
            "priority": meta["priority"], "osm": False, "len_m": 0, "area_m2": 0,
        })
        sid += 1

    surfaces.sort(key=lambda s: (s["priority"], s["kind"], s["id"]))
    lots = [[[round(p[0], 5), round(p[1], 5)] for p in coords(w)]
            for w in ways if tags(w).get("amenity") == "parking" and tags(w).get("parking") == "surface"
            and coords(w) and min(dist_m(p, HOSP) for p in coords(w)) < 200]

    json.dump({"hospital": {"id": "fh", "name": "Frederick Health Hospital",
                            "lat": HOSP[0], "lon": HOSP[1]},
               "boundary": [[round(p[0], 5), round(p[1], 5)] for p in boundary],
               "kinds": KINDS, "surfaces": surfaces},
              open(os.path.join(DATA, "surfaces.json"), "w"), separators=(",", ":"))
    blob = json.dumps({"boundary": [[round(p[0], 5), round(p[1], 5)] for p in boundary],
                       "lots": lots}, separators=(",", ":"))
    open(os.path.join(DATA, "campus.js"), "w").write("window.CAMPUS=" + blob + ";")

    by_kind = {}
    for s in surfaces:
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + 1
    print("surfaces:", len(surfaces), by_kind)
    print("unmapped (no OSM geometry):", [s["kind"] for s in surfaces if not s["osm"]])


if __name__ == "__main__":
    main()
