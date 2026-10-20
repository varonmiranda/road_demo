"""Assemble publish/ for sharing the demo as a claude.ai Artifact.
The main page gets its own <html>/<head>/<body> wrapper stripped (the Artifact adds one);
supporting pages and data ship as-is. Refresh live data first: python3 live_plan.py"""
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "publish")
DATA = ["replay.js", "live.js", "regression.js", "basemap.js"]

shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(os.path.join(OUT, "data"))
os.makedirs(os.path.join(OUT, "vendor"))

page = open(os.path.join(HERE, "index.html")).read()
for pat in (r"<!doctype html>\s*", r"<html[^>]*>\s*", r"</?head>\s*", r"<body>\s*", r"</body>\s*", r"</html>\s*",
            r'<meta charset="utf-8">\s*', r'<meta name="viewport"[^>]*>\s*'):
    page = re.sub(pat, "", page, flags=re.I)
open(os.path.join(OUT, "index.html"), "w").write(page)
shutil.copy(os.path.join(HERE, "regression.html"), OUT)
for f in DATA:
    shutil.copy(os.path.join(HERE, "data", f), os.path.join(OUT, "data", f))
shutil.copy(os.path.join(HERE, "vendor", "leaflet.css"), os.path.join(OUT, "vendor", "leaflet.css"))
print("publish/ ready:", sorted(os.listdir(OUT)), sorted(os.listdir(os.path.join(OUT, "data"))))
