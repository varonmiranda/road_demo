"""Step 3: export the corridors + storm plan to KML for Google Earth (Pro or web).

Output: data/hospital_salting_plan.kml
  - ER placemarks
  - one folder per priority tier; each segment's balloon shows the advisor vs blanket
    storm totals and the pavement temperatures it was sized for
"""
import json
import os
from xml.sax.saxutils import escape

DATA = os.path.join(os.path.dirname(__file__), "data")

# KML colors are aabbggrr
TIER_STYLE = {1: ("ff2b35c2", 5), 2: ("ff8b5f1f", 3), 3: ("ffcac0b8", 2)}
TIER_NAME = {1: "Tier 1 - bridges, steep grades, last block to ER",
             2: "Tier 2 - arterial approaches (<=1.5 km)",
             3: "Tier 3 - local streets near ER"}


def main():
    d = json.load(open(os.path.join(DATA, "replay.json")))
    segs, hosp = d["segments"], {h["id"]: h for h in d["hospitals"]}
    tot = {st: [0.0] * len(segs) for st in ("advisor", "blanket")}
    for st in tot:
        for e in d["strategies"][st]["events"]:
            tot[st][e[1]] += e[4]

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           "<name>DC Hospital Access Salting Plan - Jan 6, 2025 storm replay</name>"]
    for t, (col, w) in TIER_STYLE.items():
        out.append(f'<Style id="t{t}"><LineStyle><color>{col}</color><width>{w}</width></LineStyle></Style>')
    out.append('<Style id="er"><IconStyle><scale>1.2</scale><Icon><href>'
               'http://maps.google.com/mapfiles/kml/shapes/hospitals.png</href></Icon></IconStyle></Style>')

    out.append("<Folder><name>Emergency departments</name>")
    for h in d["hospitals"]:
        out.append(f"<Placemark><name>{escape(h['name'])}</name><description>{escape(h['trauma'])}</description>"
                   f"<styleUrl>#er</styleUrl><Point><coordinates>{h['lon']},{h['lat']},0</coordinates></Point></Placemark>")
    out.append("</Folder>")

    for t in (1, 2, 3):
        out.append(f"<Folder><name>{escape(TIER_NAME[t])}</name>")
        for s in segs:
            if s["tier"] != t:
                continue
            a, b = round(tot["advisor"][s["id"]]), round(tot["blanket"][s["id"]])
            desc = (f"{s['cls']}{' - BRIDGE' if s['bridge'] else ''} - serves {hosp[s['hosp']]['name']} "
                    f"({s['dist_m']} m)<br/>{s['lanes']} lanes, {s['lane_mi']:.2f} lane-mi, grade {s['grade']}%<br/>"
                    f"Storm total salt: advisor {a} vs blanket {b} lb/lane-mi<br/>"
                    f"Microclimate: runs {s.get('cold_idx', 0):+.1f} F vs typical corridor road"
                    f"{' - frost-prone (near water)' if s.get('frost_prone') else ''}")
            coords = " ".join(f"{lon},{lat},0" for lat, lon in s["coords"])
            out.append(f"<Placemark><name>{escape(s['name'])}</name><description><![CDATA[{desc}]]></description>"
                       f"<styleUrl>#t{t}</styleUrl><LineString><tessellate>1</tessellate>"
                       f"<coordinates>{coords}</coordinates></LineString></Placemark>")
        out.append("</Folder>")
    out.append("</Document></kml>")

    path = os.path.join(DATA, "hospital_salting_plan.kml")
    open(path, "w").write("\n".join(out))
    print("wrote", path)


if __name__ == "__main__":
    main()
