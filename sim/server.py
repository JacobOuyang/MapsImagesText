"""Local two-phone simulator: serves sim/index.html and runs the pipeline for each alert.

    python3 sim/server.py            # http://localhost:8765

POST /api/alert {"text", "lat", "lon"} -> {"message" (SMS), "text", "laptop" (WhatsApp map), "small", "image", ...}
POST /api/photo {"image": data URL} -> {"sms", "text", "mono", "small", "parts", sizes, ...}
POST /api/radar {"lat", "lon"} -> {"sms", "text", "mono", "small", "image", "parts", ...}
POST /api/directions {"lat", "lon", "to", "from"?} -> {"sms", "text", "mono", "small", "image", "parts", "trip", ...}
POST /api/directions/move {"trip", "metres" | "astray": true} -> the update after a simulated GPS fix
"small" is the slow-connection picture (~400 px WebP); "image" the full JPEG.
POST /api/message {"text", "lat", "lon", "image"?} -> router: Muse picks tools; same versions + "tools" it called
GET /api/find_rain, /api/samples, /api/sample?name=
"""
import base64
import gzip
import io
import json
import os
import subprocess
import sys
import time
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))

from evac import directions, photo, pipeline, radar, router, tonal  # noqa: E402

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
PHOTOS = {}  # photo id -> {"image": full-size photo or zoomed crop, "boxes": {key number: box in its pixels}}


def remember_photo(img):
    pid = "%x" % id(img)
    PHOTOS[pid] = {"image": img, "boxes": {}}
    return pid
SAMPLE_DIR = "/Library/Desktop Pictures"  # macOS ships photos here; used as sample "photos a friend sent"
# One object each, the simplest case for an outline: macOS's account pictures (HEIC, converted with sips).
OBJECT_DIR = "/Library/User Pictures"
OBJECTS = ["Fun/Fortune Cookie", "Fun/Gingerbread Man", "Animals/Penguin", "Animals/Owl", "Animals/Parrot",
           "Instruments/Guitar", "Instruments/Violin", "Flowers/Sunflower", "Flowers/Red Rose", "Nature/Leaf"]
CONVERTED = os.path.join(ROOT, "..", "cache", "samples")
GENERATED = os.path.join(ROOT, "samples")  # one object each, made with Muse Image (muse-image-1.0)


def sample_objects():
    out = {f[:-4]: os.path.join(GENERATED, f) for f in sorted(os.listdir(GENERATED)) if f.endswith(".jpg")} \
        if os.path.isdir(GENERATED) else {}
    out.update({o.split("/")[1]: os.path.join(OBJECT_DIR, o + ".heic") for o in OBJECTS
                if os.path.exists(os.path.join(OBJECT_DIR, o + ".heic"))})
    return out


def sample_emergency():
    d = os.path.join(GENERATED, "emergency")  # one clear subject each: actions to do, things to identify (Muse Image)
    return {f[:-4]: os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jpg")} if os.path.isdir(d) else {}


def sample_photos():
    try:
        return {f[:-4]: os.path.join(SAMPLE_DIR, f) for f in sorted(os.listdir(SAMPLE_DIR))
                if f.endswith(".jpg") and not f.startswith(("Abstract", "Chroma"))}
    except OSError:
        return {}


def sample_path(name):
    path = sample_objects().get(name) or sample_emergency().get(name) or sample_photos().get(name)
    if path and path.endswith(".heic"):  # Pillow can't read HEIC; macOS's sips can
        os.makedirs(CONVERTED, exist_ok=True)
        jpg = os.path.join(CONVERTED, name + ".jpg")
        if not os.path.exists(jpg):
            subprocess.run(["sips", "-s", "format", "jpeg", path, "--out", jpg], capture_output=True, check=True)
        path = jpg
    return path


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
            return self.send_json({"objects": list(sample_objects()), "emergency": list(sample_emergency()),
                                   "photos": list(sample_photos())})
        if self.path.startswith("/api/sample?name="):
            path = sample_path(unquote(self.path.split("=", 1)[1]))
            if not path:
                return self.send_error(404)
            img = Image.open(path).convert("RGB")
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
                if body.get("photo_id") in PHOTOS:  # a question about a photo sent earlier in the Photo tab
                    buf = io.BytesIO()
                    PHOTOS[body["photo_id"]]["image"].save(buf, "JPEG", quality=90)
                    img = buf.getvalue()
                out = router.handle(body["text"], (float(body["lat"]), float(body["lon"])), image=img)
                out["parts"] = sms_parts(out["sms"]) if out.get("sms") else 0
            elif self.path == "/api/photo":  # the quick version (SMS + low-detail WhatsApp outline)
                data = base64.b64decode(body["image"].split(",", 1)[-1])
                try:
                    Image.open(io.BytesIO(data)).verify()
                except Exception:
                    return self.send_json({"error": "That file isn't a picture that can be read.", "log": []}, t)
                out = photo.run(data)
                out["parts"] = sms_parts(out["sms"])
                out["photo_id"] = remember_photo(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))
            elif self.path in ("/api/photo/high", "/api/photo/zoom"):
                # High detail: the whole scene in tones. Zoom: the same for one numbered part, on its own crop of the
                # full-size photo; every result is kept, so a zoom can be zoomed again.
                entry = PHOTOS[body["photo_id"]]
                if self.path == "/api/photo/zoom":
                    box = entry["boxes"][str(body["n"])]
                    img = entry["image"].crop(box)
                else:
                    img = entry["image"]
                work = img.copy()
                work.thumbnail((1600, 1600))
                r = tonal.draw(work)
                k = img.width / work.width
                pid = remember_photo(img)
                PHOTOS[pid]["boxes"] = {str(n): tuple(int(v * k) for v in b) for n, b in r["boxes"].items()}
                mono = tonal.text(r)
                out = {"mono": mono, "mono_bytes": len(mono.encode()), "mono_gzip": len(gzip.compress(mono.encode())),
                       "key": r["key"], "seen": r["seen"], "recognised": r["recognised"], "regions": r["regions"],
                       "photo_id": pid}
            elif self.path == "/api/radar":
                out = radar.run((float(body["lat"]), float(body["lon"])))
                out["parts"] = sms_parts(out["sms"])
            elif self.path == "/api/directions":
                out = directions.run((float(body["lat"]), float(body["lon"])), body["to"], start=body.get("from") or None)
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
