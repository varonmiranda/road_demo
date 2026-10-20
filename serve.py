"""Local server for the demo, with a refresh endpoint for live mode.

    python3 serve.py        then open http://localhost:8765            (storm replay)
                                      http://localhost:8765/?mode=live  (live NWS forecast)

POST /api/refresh?drill=0  re-pulls the NWS forecast (live_plan.py) and rewrites data/live.js
"""
import json
import os
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import live_plan

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8765))   # preview tools may assign a port


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")     # always serve the freshest live.js
        super().end_headers()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/api/refresh":
            self.send_error(404)
            return
        try:
            drill = float(parse_qs(url.query).get("drill", ["0"])[0])
            body = {"ok": True, "summary": live_plan.main(drill)}
            code = 200
        except Exception as e:  # report NWS outages to the page instead of crashing the server
            traceback.print_exc()
            body, code = {"ok": False, "error": str(e)}, 502
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"Serving on http://localhost:{PORT}  (live mode: http://localhost:{PORT}/?mode=live)")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
