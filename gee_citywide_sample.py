"""Step 4a: sample road segments across ALL of DC (not just hospital corridors) and pull the same
Earth Engine variables, so regressions are fitted on a citywide, spatially spread sample.

    EE_PROJECT=green-infrapro python3 gee_citywide_sample.py

Output: data/citywide_sample.json  (list of {lat, lon, bridge, road class, <gee variables>})
"""
import json
import os
import random

import ee
import osmnx as ox

from gee_features import build_stack

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data")
CACHE = os.environ.get("RSD_CACHE", DATA)
N = 4000
SEED = 7
BUFFER_M = 15
CHUNK = 400


def first(v):
    return v[0] if isinstance(v, list) else v


def main():
    project = os.environ.get("EE_PROJECT")
    ee.Initialize(project=project) if project else ee.Initialize()

    G = ox.load_graphml(os.path.join(CACHE, "dc_drive.graphml"))
    U = ox.convert.to_undirected(G)
    edges = []
    for u, v, k, d in U.edges(keys=True, data=True):
        if d.get("length", 0) < 40 or first(d.get("tunnel")) not in (None, "no"):
            continue
        if "geometry" in d:
            coords = [(round(y, 6), round(x, 6)) for x, y in d["geometry"].coords]
        else:
            coords = [(round(U.nodes[n]["y"], 6), round(U.nodes[n]["x"], 6)) for n in (u, v)]
        b = first(d.get("bridge"))
        edges.append({"coords": coords, "len_m": round(d["length"]),
                      "bridge": isinstance(b, str) and b != "no",
                      "hw": str(first(d.get("highway", "residential"))).replace("_link", "")})
    random.seed(SEED)
    sample = random.sample(edges, min(N, len(edges)))
    print(f"{len(edges)} drivable edges in DC, sampling {len(sample)}")

    lats = [c[0] for e in edges for c in e["coords"]]
    lons = [c[1] for e in edges for c in e["coords"]]
    region = ee.Geometry.Rectangle([min(lons), min(lats), max(lons), max(lats)])
    stack, dem, n_s2 = build_stack(region)
    print("Sentinel-2 clear scenes after the storm:", n_s2.getInfo())

    rows = []
    for k in range(0, len(sample), CHUNK):
        part = sample[k:k + CHUNK]
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.LineString([[lo, la] for la, lo in e["coords"]]).buffer(BUFFER_M), {"i": k + j})
            for j, e in enumerate(part)])
        ends = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([e["coords"][end][1], e["coords"][end][0]]), {"i": k + j, "end": end})
            for j, e in enumerate(part) for end in (0, -1)])
        res = stack.reduceRegions(fc, ee.Reducer.mean(), scale=10, tileScale=4).getInfo()
        z = dem.reduceRegions(ends, ee.Reducer.first(), scale=10).getInfo()
        zz = {}
        for f in z["features"]:
            zz.setdefault(f["properties"]["i"], []).append(f["properties"].get("first"))
        for f in res["features"]:
            p = f["properties"]
            i = p.pop("i")
            e = sample[i]
            mid = e["coords"][len(e["coords"]) // 2]
            a = zz.get(i, [])
            p["grade_pct"] = round(min(15, abs(a[0] - a[1]) / e["len_m"] * 100), 2) if len(a) == 2 and None not in a else None
            rows.append({"lat": mid[0], "lon": mid[1], "bridge": e["bridge"], "hw": e["hw"], "len_m": e["len_m"],
                         **{kk: (round(v, 4) if isinstance(v, float) else v) for kk, v in p.items()}})
        print(f"  {k + len(part)}/{len(sample)}")

    json.dump(rows, open(os.path.join(DATA, "citywide_sample.json"), "w"), separators=(",", ":"))
    print("wrote data/citywide_sample.json:", len(rows), "roads")


if __name__ == "__main__":
    main()
