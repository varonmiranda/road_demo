"""Step 1b: add the hospital campus itself (its own drives and parking aisles) to the
road-segment set, and derive the campus boundary the page clips the predictions to.

Run AFTER build_data.py:

    python3 build_campus.py     # needs network (api.openstreetmap.org), no osmnx

Sources
  - OpenStreetMap API /api/0.6/map for the campus bbox (Overpass is not required): the
    hospital grounds polygon, the adjacent parking lots, and every service road /
    parking aisle inside them.
  - Campus boundary = convex hull of the hospital grounds + the parking lots within
    160 m of it.

Writes
  - data/segments.json  (existing corridor segments kept, campus segments appended)
  - data/campus.js      window.CAMPUS = {boundary: [...], lots: [...]}
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
HOSP = (39.42296, -77.41485)
LOT_M = 160           # parking lots this close to the grounds belong to the campus
TRAUMA_W = 0.7        # "Emergency dept." weight, as in build_data.py
CORRIDOR_M = 1500

M_PER_DEG_LAT = 111320.0


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


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        st = []
        for p in seq:
            while len(st) >= 2 and ((st[-1][0] - st[-2][0]) * (p[1] - st[-2][1])
                                    - (st[-1][1] - st[-2][1]) * (p[0] - st[-2][0])) <= 0:
                st.pop()
            st.append(p)
        return st

    return half(pts)[:-1] + half(pts[::-1])[:-1]


def point_in_ring(lat, lon, ring):
    inside = False
    for i in range(len(ring)):
        y1, x1 = ring[i]
        y2, x2 = ring[(i + 1) % len(ring)]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def main():
    root = fetch_osm()
    nodes = {n.get("id"): (float(n.get("lat")), float(n.get("lon"))) for n in root.findall("node")}

    def tags(e):
        return {c.get("k"): c.get("v") for c in e.findall("tag")}

    def coords(w):
        return [nodes[n.get("ref")] for n in w.findall("nd") if n.get("ref") in nodes]

    ways = root.findall("way")
    grounds = next(coords(w) for w in ways if tags(w).get("amenity") == "hospital")
    lots = [coords(w) for w in ways if tags(w).get("amenity") == "parking"
            and coords(w) and min(dist_m(p, HOSP) for p in coords(w)) < LOT_M]
    boundary = convex_hull(grounds + [p for lot in lots for p in lot])

    # campus ways: the site's own drives, parking aisles and service roads
    existing = json.load(open(os.path.join(DATA, "segments.json")))
    base = [s for s in existing["segments"] if not s.get("campus")]
    next_id = max((s["id"] for s in base), default=-1) + 1
    campus = []
    for w in ways:
        tg = tags(w)
        if tg.get("highway") != "service":
            continue
        c = coords(w)
        if len(c) < 2:
            continue
        mid = c[len(c) // 2]
        if not point_in_ring(mid[0], mid[1], boundary):
            continue
        length = sum(dist_m(c[i], c[i + 1]) for i in range(len(c) - 1))
        if length < 8:
            continue
        svc = tg.get("service", "")
        lanes = 1 if svc in ("parking_aisle", "driveway") else 2
        dm = round(min(dist_m(p, HOSP) for p in c))
        hscore = TRAUMA_W * (1 - 0.5 * dm / CORRIDOR_M)
        campus.append({
            "id": next_id, "name": tg.get("name") or "(unnamed)",
            "cls": "Parking aisle" if svc == "parking_aisle" else "Campus drive",
            "hosp": "fh", "dist_m": dm, "len_m": round(length), "lanes": lanes,
            "lane_mi": round(length / 1609.34 * lanes, 4),
            "bridge": False, "tunnel": False, "grade": 0.0, "tier": 1,
            "priority": round(hscore * 1.3, 3),
            "coords": [[round(p[0], 5), round(p[1], 5)] for p in c],
            "campus": True,
        })
        next_id += 1

    existing["segments"] = base + campus
    json.dump(existing, open(os.path.join(DATA, "segments.json"), "w"), separators=(",", ":"))
    blob = json.dumps({"boundary": [[round(p[0], 5), round(p[1], 5)] for p in boundary],
                       "lots": [[[round(p[0], 5), round(p[1], 5)] for p in lot] for lot in lots]},
                      separators=(",", ":"))
    open(os.path.join(DATA, "campus.js"), "w").write("window.CAMPUS=" + blob + ";")
    print(f"campus: {len(campus)} segments ({sum(s['lane_mi'] for s in campus):.2f} lane-mi), "
          f"boundary {len(boundary)} pts, {len(lots)} parking lots")


if __name__ == "__main__":
    main()
