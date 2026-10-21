"""Step 1: build the hospital access-corridor road segments for Frederick, MD.

Output: data/segments.json  (one record per road segment with static risk features)

Sources
  - Road network, bridges, lanes: OpenStreetMap via OSMnx
  - Elevation (for grade): Open-Meteo elevation API (Copernicus 90 m DEM)
    -> Round 2: swap for USGS 3DEP via Google Earth Engine for 10 m grade + shade
"""
import json
import math
import os
import time

import networkx as nx
import osmnx as ox
import requests

OUT = os.path.join(os.path.dirname(__file__), "data")
CACHE = os.environ.get("RSD_CACHE", OUT)
os.makedirs(OUT, exist_ok=True)

CORRIDOR_M = 1500   # network distance from the ER for arterial approaches
APPROACH_M = 350    # every street (incl. local) this close is an ER approach
DOOR_M = 150        # last block to the ER entrance is always tier 1

# Frederick Health Hospital, 400 W 7th St, Frederick, MD (coordinates from OpenStreetMap/Nominatim).
# Trauma level -- VERIFY before citing.
HOSPITALS = [
    {"id": "fh", "name": "Frederick Health Hospital", "lat": 39.42296, "lon": -77.41485, "trauma": "Emergency dept."},
]
TRAUMA_WEIGHT = {"Level I": 1.0, "Level II": 0.85, "Emergency": 0.7}

ARTERIAL = {"motorway", "trunk", "primary", "secondary", "tertiary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
DEFAULT_LANES = {"motorway": 6, "trunk": 4, "primary": 4, "secondary": 3, "tertiary": 2}
CLASS_LABEL = {"motorway": "Freeway", "trunk": "Major arterial", "primary": "Major arterial",
               "secondary": "Minor arterial", "tertiary": "Collector"}


def first(v):
    return v[0] if isinstance(v, list) else v


def hospital_weight(h):
    return next(w for k, w in TRAUMA_WEIGHT.items() if h["trauma"].startswith(k))


def fetch_elevations(points):
    cpath = os.path.join(CACHE, "elev_cache.json")
    cache = json.load(open(cpath)) if os.path.exists(cpath) else {}
    keys = [f"{p[0]:.5f},{p[1]:.5f}" for p in points]
    todo = [k for k in dict.fromkeys(keys) if k not in cache]
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        for attempt in range(8):
            r = requests.get("https://api.open-meteo.com/v1/elevation", params={
                "latitude": ",".join(k.split(",")[0] for k in chunk),
                "longitude": ",".join(k.split(",")[1] for k in chunk)}, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(10 * (attempt + 1))
        r.raise_for_status()
        cache.update(zip(chunk, r.json()["elevation"]))
        json.dump(cache, open(cpath, "w"))
        time.sleep(1.5)
    return [cache[k] for k in keys]


def main():
    gpath = os.path.join(CACHE, "frederick_drive.graphml")
    if os.path.exists(gpath):
        G = ox.load_graphml(gpath)
    else:
        G = ox.graph_from_place("Frederick, Maryland, USA", network_type="drive", simplify=True)
        ox.save_graphml(G, gpath)
    U = ox.convert.to_undirected(G)

    # 1. corridor membership: network distance from each ER
    seg = {}  # (u,v,k) -> record
    for h in HOSPITALS:
        src = ox.distance.nearest_nodes(U, h["lon"], h["lat"])
        dist = nx.single_source_dijkstra_path_length(U, src, cutoff=CORRIDOR_M, weight="length")
        w = hospital_weight(h)
        for u, v, k, d in U.edges(keys=True, data=True):
            if u not in dist or v not in dist:
                continue
            hw = first(d.get("highway", "residential"))
            dm = min(dist[u], dist[v])
            if not (hw in ARTERIAL or dm <= APPROACH_M):
                continue
            key = (min(u, v), max(u, v), k)
            # proximity weight: 1.0 at the door, 0.5 at the corridor edge
            score = w * (1 - 0.5 * dm / CORRIDOR_M)
            if key not in seg or score > seg[key]["hscore"]:
                seg[key] = {"u": u, "v": v, "d": d, "hw": hw, "hosp": h["id"],
                            "dist_m": round(dm), "hscore": score}

    # 2. elevation for grade
    nodes = sorted({n for r in seg.values() for n in (r["u"], r["v"])})
    elev = dict(zip(nodes, fetch_elevations([(U.nodes[n]["y"], U.nodes[n]["x"]) for n in nodes])))

    # 3. static features + priority tier
    records = []
    for i, r in enumerate(seg.values()):
        d, hw = r["d"], r["hw"]
        length = d["length"]
        base = hw.replace("_link", "")
        try:
            lanes = int(str(first(d.get("lanes"))).split(";")[0])
        except (TypeError, ValueError):
            lanes = DEFAULT_LANES.get(base, 2)
        if d.get("oneway") is False or str(d.get("oneway")) == "False":
            lanes = max(lanes, 2)
        lanes = min(max(lanes, 1), 8)
        bridge = first(d.get("bridge")) not in (None, "no") and not (isinstance(first(d.get("bridge")), float))
        tunnel = first(d.get("tunnel")) not in (None, "no") and not (isinstance(first(d.get("tunnel")), float))
        grade = abs(elev[r["u"]] - elev[r["v"]]) / max(length, 1) * 100
        grade = min(round(grade, 1), 12.0) if length > 100 else 0.0  # short edges: DEM too coarse
        steep = grade >= 6

        if "geometry" in d:
            coords = [(round(y, 5), round(x, 5)) for x, y in d["geometry"].coords]
        else:
            coords = [(round(U.nodes[n]["y"], 5), round(U.nodes[n]["x"], 5)) for n in (r["u"], r["v"])]

        # tier 1 = treat first / never skip; tier 2 = arterial approach; tier 3 = local street
        if bridge or steep or r["dist_m"] <= DOOR_M:
            tier = 1
        elif base in ARTERIAL or hw in ARTERIAL:
            tier = 2
        else:
            tier = 3
        priority = r["hscore"] * (1.4 if bridge else 1) * (1.2 if steep else 1) * {1: 1.3, 2: 1.0, 3: 0.6}[tier]

        name = first(d.get("name")) if isinstance(first(d.get("name")), str) else "(unnamed)"
        records.append({
            "id": i, "name": name, "cls": CLASS_LABEL.get(base, "Local street"), "hosp": r["hosp"],
            "dist_m": r["dist_m"], "len_m": round(length), "lanes": lanes,
            "lane_mi": round(length / 1609.34 * lanes, 4),
            "bridge": bool(bridge), "tunnel": bool(tunnel), "grade": grade,
            "tier": tier, "priority": round(priority, 3), "coords": coords,
        })

    with open(os.path.join(OUT, "segments.json"), "w") as f:
        json.dump({"hospitals": HOSPITALS, "segments": records}, f, separators=(",", ":"))
    lm = sum(r["lane_mi"] for r in records)
    print(f"{len(records)} segments, {lm:.1f} lane-miles, "
          f"{sum(r['bridge'] for r in records)} bridge, {sum(r['grade'] >= 6 for r in records)} steep, "
          f"tiers {[sum(r['tier'] == t for r in records) for t in (1, 2, 3)]}")


if __name__ == "__main__":
    main()
