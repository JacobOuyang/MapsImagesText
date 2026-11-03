"""The simulator: pages that show the look-closer loop with a simulated phone, and the API behind them.

    .venv/bin/python sim/server.py            # http://localhost:8765

Pages: /closer (a phone asks about a photo it keeps; the real router, Muse Spark and SAM 3.1 answer, asking the phone
for close-ups), /compare (one photo and question three ways: the photo, WebP at the loop's size, the loop; from the
last run of evals/kit/three_ways.py).

Look closer: GET /api/closer/samples, /api/closer/state?c=; POST /api/closer/start {"sample", "question"}
Also: GET /api/compare, /kit.js (the code reader the pages use), /store/<record id>.jpg, /eval/three_ways/<file>.png
"""
import io
import json
import os
import sys
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, ".."))
sys.path.insert(0, ROOT)
from evac import codes, llm, store  # noqa: E402
import closer_demo  # noqa: E402  (a phone asks about a photo it keeps)

PORT = int(os.environ.get("PORT", 8765))
PAGES = {"/": "closer.html", "/closer": "closer.html", "/compare": "compare.html"}
EVAL = os.path.join(ROOT, "..", "out", "eval")


def jpeg(img, side, quality):
    img = img.convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def kit_js():
    """The code reader with its phrasebook and the issuer's public key in front."""
    return ("var KIT_PUBKEY = %s;\nvar KIT_PACK = %s;\n" % (json.dumps(codes.public_key()), json.dumps(codes.pack())) +
            open(os.path.join(ROOT, "kit.js")).read())


def compare_rows():
    """The last run of evals/kit/three_ways.py, for /compare."""
    p = os.path.join(EVAL, "three_ways", "results.json")
    return {"rows": json.load(open(p)) if os.path.exists(p) else []}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_bytes(self, data, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send_bytes(json.dumps(obj).encode(), "application/json", code)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        arg = unquote(query.split("=", 1)[1]) if "=" in query else ""
        if path in PAGES:
            return self.send_bytes(open(os.path.join(ROOT, PAGES[path]), "rb").read(), "text/html; charset=utf-8")
        if path == "/kit.js":
            return self.send_bytes(kit_js().encode(), "application/javascript; charset=utf-8")
        if path.startswith("/store/") and path.endswith(".jpg"):  # a record's picture (a phone's 128 px photo, a close-up)
            img = store.image(path[len("/store/"):-4])
            return self.send_bytes(jpeg(img, 640, 88), "image/jpeg") if img else self.send_error(404)
        if path.startswith("/eval/three_ways/") and path.endswith(".png") and ".." not in path:
            p = os.path.join(EVAL, path[len("/eval/"):])
            return self.send_bytes(open(p, "rb").read(), "image/png") if os.path.exists(p) else self.send_error(404)
        api = {"/api/closer/samples": lambda: {"samples": closer_demo.samples()},
               "/api/closer/state": lambda: closer_demo.state(arg),
               "/api/compare": compare_rows}
        if path in api:
            return self.send_json(api[path]())
        return super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            if self.path == "/api/closer/start":  # a simulated phone asks about one of the sample photos
                return self.send_json({"conv": closer_demo.start(body["sample"], body.get("question") or "What is this?")})
            self.send_json({"error": "not found"}, 404)
        except Exception as ex:
            traceback.print_exc()
            self.send_json({"error": "%s: %s" % (type(ex).__name__, ex)}, 500)


if __name__ == "__main__":
    llm._load_dotenv()
    print("simulator on http://127.0.0.1:%d (model %s)" % (PORT, "on" if llm.available() else "off: set LLAMA_API_KEY in .env"), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
