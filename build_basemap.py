"""Build data/basemap.js: an offline vector basemap for DC (boundary, water, every drivable street),
so the demo map needs no tile server (works offline and inside a published artifact)."""
import json
import os

import osmnx as ox
ox.settings.overpass_url = os.environ.get("OVERPASS_URL", ox.settings.overpass_url)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("RSD_CACHE", os.path.join(HERE, "data"))
TOL = 0.00008          # ~8 m simplification


def rings(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.area < 2e-7:
            continue
        out.append([[round(y, 5), round(x, 5)] for x, y in p.simplify(TOL).exterior.coords])
    return out


def main():
    G = ox.load_graphml(os.path.join(CACHE, "dc_drive.graphml"))
    U = ox.convert.to_undirected(G)
    major = {"motorway", "trunk", "primary", "secondary"}
    roads = {"major": [], "minor": []}
    for u, v, d in U.edges(data=True):
        hw = d.get("highway"); hw = hw[0] if isinstance(hw, list) else hw
        g = d.get("geometry")
        pts = [(U.nodes[u]["x"], U.nodes[u]["y"]), (U.nodes[v]["x"], U.nodes[v]["y"])] if g is None else list(g.simplify(TOL).coords)
        roads["major" if str(hw).replace("_link", "") in major else "minor"].append([[round(y, 4), round(x, 4)] for x, y in pts])

    boundary = rings(ox.geocode_to_gdf("Washington, DC, USA").geometry.iloc[0])
    water = []
    for tags in ({"natural": "water"}, {"water": "river"}):
        try:
            gdf = ox.features_from_place("Washington, DC, USA", tags)
            for geom in gdf.geometry:
                water += rings(geom)
        except Exception as e:
            print("water fetch skipped:", tags, e)
    out = {"boundary": boundary, "water": water, "major": roads["major"], "minor": roads["minor"]}
    blob = json.dumps(out, separators=(",", ":"))
    open(os.path.join(HERE, "data", "basemap.js"), "w").write("window.BASEMAP=" + blob + ";")
    print(f"basemap: {len(water)} water polygons, {len(roads['major'])} major + {len(roads['minor'])} minor streets, {len(blob)/1e6:.2f} MB")


if __name__ == "__main__":
    main()
