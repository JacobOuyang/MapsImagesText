"""Local two-phone simulator: serves sim/index.html and runs the pipeline for each alert.

    python3 sim/server.py            # http://localhost:8765

POST /api/alert {"text", "lat", "lon"} -> {"message" (SMS), "text", "laptop" (WhatsApp map), "small", "image", ...}
POST /api/photo {"image": data URL} -> {"sms", "text", "mono", "small", "parts", sizes, ...}
POST /api/radar {"lat", "lon"} -> {"sms", "text", "mono", "small", "image", "parts", ...}
POST /api/directions {"lat", "lon", "to"} -> {"sms", "text", "mono", "small", "image", "parts", "trip", ...}
POST /api/directions/move {"trip", "metres" | "astray": true} -> the update after a simulated GPS fix
"small" is the slow-connection picture (~400 px WebP); "image" the full JPEG.
POST /api/message {"text", "lat", "lon", "image"?} -> router: Muse picks tools; same versions + "tools" it called
GET /api/find_rain, /api/samples, /api/sample?name=
"""
import base64
import io
import json
import os
import sys
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))

from evac import directions, photo, pipeline, radar, router  # noqa: E402

PORT = int(os.environ.get("PORT", 8765))
WHATSAPP_COLS = 58  # widest monospace map that fits the simulated WhatsApp Desktop bubble (65% of the chat pane) unwrapped


def sms_parts(text):
    """Parts an SMS of this text takes: GSM-7 (160/153) if it fits that alphabet, else UCS-2 (70/67)."""
    gsm = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
    if all(ch in gsm for ch in text):
        return 1 if len(text) <= 160 else -(-len(text) // 153)
    units = len(text.encode("utf-16-le")) // 2
    return 1 if units <= 70 else -(-units // 67)


TRIPS = {}  # trip id -> directions.Trip, for the simulated walk
SAMPLE_DIR = "/Library/Desktop Pictures"  # macOS ships photos here; used as sample "photos a friend sent"


def sample_photos():
    try:
        return sorted(f[:-4] for f in os.listdir(SAMPLE_DIR) if f.endswith(".jpg") and not f.startswith(("Abstract", "Chroma")))
    except OSError:
        return []


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_json(self, out, t=None):
        if t is not None:
            out["seconds"] = round(time.time() - t, 1)
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/samples":
            return self.send_json({"photos": sample_photos()})
        if self.path.startswith("/api/sample?name="):
            name = unquote(self.path.split("=", 1)[1])
            if name not in sample_photos():
                return self.send_error(404)
            img = Image.open(os.path.join(SAMPLE_DIR, name + ".jpg")).convert("RGB")
            img.thumbnail((1600, 1600))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if self.path == "/api/find_rain":
            t = time.time()
            lat, lon = radar.find_rain()
            return self.send_json({"lat": lat, "lon": lon}, t)
        return super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        t = time.time()
        try:
            if self.path == "/api/alert":
                res = pipeline.run(body["text"], user=(float(body["lat"]), float(body["lon"])), laptop=WHATSAPP_COLS, image=True)
                out = {"message": res["message"], "affected": res["affected"], "log": res["log"],
                       "text": res.get("text"), "laptop": res.get("laptop"), "image": res.get("image"),
                       "small": res.get("small")}
                out["parts"] = sms_parts(out["message"]) if out.get("message") else 0
            elif self.path == "/api/message":
                img = base64.b64decode(body["image"].split(",", 1)[-1]) if body.get("image") else None
                out = router.handle(body["text"], (float(body["lat"]), float(body["lon"])), image=img)
                out["parts"] = sms_parts(out["sms"]) if out.get("sms") else 0
            elif self.path == "/api/photo":
                out = photo.run(base64.b64decode(body["image"].split(",", 1)[-1]))
                out["parts"] = sms_parts(out["sms"])
            elif self.path == "/api/radar":
                out = radar.run((float(body["lat"]), float(body["lon"])))
                out["parts"] = sms_parts(out["sms"])
            elif self.path == "/api/directions":
                out = directions.run((float(body["lat"]), float(body["lon"])), body["to"])
                out.pop("scene", None)  # the page gets the rendered versions, not the Scene object
                trip = out.pop("trip", None)
                if trip:
                    out["trip"] = "%x" % id(trip)
                    TRIPS[out["trip"]] = trip
                    out["you"] = trip.you
                if "sms" in out:
                    out["parts"] = sms_parts(out["sms"])
            elif self.path == "/api/directions/move":
                # Simulated GPS: walk along the route, or take a wrong turn, then send the update.
                trip = TRIPS[body["trip"]]
                you = (directions.astray(trip) if body.get("astray")
                       else directions.along(trip, float(body.get("metres", 150))))
                out = directions.move(trip, you)
                out.pop("scene", None)
                out.update(trip=body["trip"], you=you, parts=sms_parts(out["sms"]))
            else:
                return self.send_error(404)
        except Exception as e:  # show the failure in the page rather than hanging it
            traceback.print_exc()
            out = {"message": None, "error": "%s: %s" % (type(e).__name__, e), "log": []}
        self.send_json(out, t)


if __name__ == "__main__":
    print("simulator on http://localhost:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
