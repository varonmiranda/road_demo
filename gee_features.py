"""Step 1b: pull Google Earth Engine variables for every road segment.

Run once after `earthengine authenticate`:
    EE_PROJECT=<your-cloud-project-id> python3 gee_features.py

Output: data/gee_features.json  {segment_id: {variable: value}}

Variables (all sampled over a 15 m buffer around each road segment)
  elev_m          USGS 3DEP 10 m DEM - mean elevation                     -> cold-air / lapse rate
  grade_pct       3DEP elevation difference between the two segment ends -> steep-hill priority
  northness       mean cos(terrain aspect), +1 = north-facing slope      -> less winter sun
  sun_lit_frac    share of 5 winter sun positions (Jan 6, 9:00-15:30) NOT in terrain shadow
  canopy_pct      NLCD 2023 tree canopy cover (%)                        -> shade + traps night heat
  impervious_pct  NLCD 2021 impervious surface (%)                       -> urban heat island
  built_h_m       JRC GHSL 2018 average building height (m, 100 m grid)  -> urban-canyon shade
  water_dist_m    distance to water (JRC Global Surface Water occurrence > 50%) -> frost / fog
  lst_night_anom_f MODIS night land-surface temp, winters 2022-25, minus DC median (F) -> measured cold spots
  lst_day_anom_f  Landsat 8/9 surface temp, clear winter days 2022-25, minus DC median (F)
  snow_persist    Sentinel-2 snow share in clear scenes Jan 7-20, 2025 (after this storm) -> VALIDATION only
"""
import json
import os

import ee

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data")
BUFFER_M = 15
CHUNK = 300

# Sun positions over DC on Jan 6 (azimuth deg, elevation deg): 9:00, 10:30, 12:15, 14:00, 15:30
SUN = [(133, 13), (150, 23), (180, 28), (210, 23), (228, 12)]
WINTERS = [("2022-12-01", "2023-03-01"), ("2023-12-01", "2024-03-01"), ("2024-12-01", "2025-03-01")]


def winter(col):
    return ee.ImageCollection(col).filter(ee.Filter.Or(*[ee.Filter.date(a, b) for a, b in WINTERS]))


def build_stack(region):
    dem = ee.Image("USGS/3DEP/10m").select("elevation")
    aspect = ee.Terrain.aspect(dem)
    northness = aspect.multiply(3.14159265 / 180).cos().rename("northness")
    lit = ee.Image.constant(0)
    for az, el in SUN:
        lit = lit.add(ee.Terrain.hillShadow(dem, az, 90 - el, 200, True))
    sun_lit = lit.divide(len(SUN)).rename("sun_lit_frac")

    canopy = (ee.ImageCollection("USGS/NLCD_RELEASES/2023_REL/TCC/v2023-5")
              .filterDate("2023-01-01", "2024-01-01").filterBounds(region).mosaic()
              .select("NLCD_Percent_Tree_Canopy_Cover").rename("canopy_pct"))
    imperv = (ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD").filterBounds(region).mosaic()
              .select("impervious").rename("impervious_pct"))
    built_h = (ee.ImageCollection("JRC/GHSL/P2023A/GHS_BUILT_H").filterBounds(region).mosaic()
               .select("built_height").rename("built_h_m"))

    water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(50).unmask(0)
    water_dist = (water.fastDistanceTransform(512).sqrt()
                  .multiply(ee.Image.pixelArea().sqrt()).rename("water_dist_m"))

    # MODIS night LST (Kelvin * 0.02) -> F anomaly vs DC median
    def k_to_f(img):
        return img.subtract(273.15).multiply(9 / 5).add(32)
    modis = k_to_f(winter("MODIS/061/MOD11A1").filterBounds(region)
                   .select("LST_Night_1km").median().multiply(0.02))
    # Landsat 8/9 surface temp, cloud/shadow/snow masked
    def ls_prep(img):
        qa = img.select("QA_PIXEL")
        clear = qa.bitwiseAnd(0b111000).eq(0)       # bits 3 cloud, 4 shadow, 5 snow
        return img.select("ST_B10").multiply(0.00341802).add(149.0).updateMask(clear)
    landsat = k_to_f(winter("LANDSAT/LC09/C02/T1_L2").merge(winter("LANDSAT/LC08/C02/T1_L2"))
                     .filterBounds(region).map(ls_prep).median())

    def anomaly(img, name, scale):
        med = img.reduceRegion(ee.Reducer.median(), region, scale, maxPixels=1e9).values().get(0)
        return img.subtract(ee.Number(med)).rename(name)

    # Sentinel-2 snow after the Jan 6, 2025 storm (validation only)
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
          .filterDate("2025-01-07", "2025-01-21").filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30)))

    def snow(img):
        ndsi = img.normalizedDifference(["B3", "B11"])
        scl = img.select("SCL")
        valid = scl.neq(8).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(3))   # drop cloud/cirrus/shadow
        return ndsi.gt(0.4).And(img.select("B8").gt(1100)).updateMask(valid).rename("snow_persist")
    snow_persist = s2.map(snow).mean()     # masked where no clear view -> null for that segment

    stack = (dem.rename("elev_m").addBands(northness).addBands(sun_lit).addBands(canopy)
             .addBands(imperv).addBands(built_h).addBands(water_dist)
             .addBands(anomaly(modis, "lst_night_anom_f", 1000))
             .addBands(anomaly(landsat, "lst_day_anom_f", 30))
             .addBands(snow_persist))
    return stack, dem, s2.size()


def main():
    project = os.environ.get("EE_PROJECT")
    ee.Initialize(project=project) if project else ee.Initialize()

    segs = json.load(open(os.path.join(DATA, "segments.json")))["segments"]
    lats = [c[0] for s in segs for c in s["coords"]]
    lons = [c[1] for s in segs for c in s["coords"]]
    region = ee.Geometry.Rectangle([min(lons) - .02, min(lats) - .02, max(lons) + .02, max(lats) + .02])
    stack, dem, n_s2 = build_stack(region)
    print("Sentinel-2 clear scenes after the storm:", n_s2.getInfo())

    out = {}
    for k in range(0, len(segs), CHUNK):
        part = segs[k:k + CHUNK]
        lines = ee.FeatureCollection([
            ee.Feature(ee.Geometry.LineString([[lon, lat] for lat, lon in s["coords"]]).buffer(BUFFER_M),
                       {"sid": s["id"]}) for s in part])
        ends = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([s["coords"][j][1], s["coords"][j][0]]), {"sid": s["id"], "end": j})
            for s in part for j in (0, -1)])
        mean = stack.reduceRegions(lines, ee.Reducer.mean(), scale=10, tileScale=4).getInfo()
        z = dem.reduceRegions(ends, ee.Reducer.first(), scale=10).getInfo()
        for f in mean["features"]:
            out[f["properties"]["sid"]] = {kk: (round(v, 3) if isinstance(v, float) else v)
                                           for kk, v in f["properties"].items() if kk != "sid"}
        zz = {}
        for f in z["features"]:
            zz.setdefault(f["properties"]["sid"], []).append(f["properties"].get("first"))
        for s in part:
            a = zz.get(s["id"], [])
            if len(a) == 2 and None not in a and s["len_m"] > 30:
                out[s["id"]]["grade_pct"] = round(min(15, abs(a[0] - a[1]) / s["len_m"] * 100), 1)
            else:
                out[s["id"]]["grade_pct"] = 0.0
        print(f"  segments {k}-{k + len(part) - 1} done")

    json.dump(out, open(os.path.join(DATA, "gee_features.json"), "w"), separators=(",", ":"))
    print("wrote data/gee_features.json for", len(out), "segments")


if __name__ == "__main__":
    main()
