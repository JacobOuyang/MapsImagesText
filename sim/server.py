"""Local two-phone simulator: serves sim/index.html and runs the pipeline for each alert.

    python3 sim/server.py            # http://localhost:8765

POST /api/alert {"text": alert SMS, "lat": .., "lon": ..} -> {"message", "affected", "parts", "seconds", "log"}
"""
import json
import os
import sys
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))

from evac import pipeline  # noqa: E402

PORT = int(os.environ.get("PORT", 8765))


def sms_parts(text):
    """Parts an SMS of this text takes: GSM-7 (160/153) if it fits that alphabet, else UCS-2 (70/67)."""
    gsm = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
    if all(ch in gsm for ch in text):
        return 1 if len(text) <= 160 else -(-len(text) // 153)
    units = len(text.encode("utf-16-le")) // 2
    return 1 if units <= 70 else -(-units // 67)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_POST(self):
        if self.path != "/api/alert":
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        t = time.time()
        try:
            res = pipeline.run(body["text"], user=(float(body["lat"]), float(body["lon"])))
            out = {"message": res["message"], "affected": res["affected"], "log": res["log"]}
        except Exception as e:  # show the failure in the chat rather than hanging the phone
            traceback.print_exc()
            out = {"message": None, "error": "%s: %s" % (type(e).__name__, e), "log": []}
        out["seconds"] = round(time.time() - t, 1)
        out["parts"] = sms_parts(out["message"]) if out.get("message") else 0
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print("simulator on http://localhost:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
