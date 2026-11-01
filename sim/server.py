"""The simulator: the pages and the API behind them.

    .venv/bin/python sim/server.py            # http://localhost:8765

Pages: / (the modes, every reply in its versions), /kit.html (the offline kit, as one file), /dashboard (the community
hub; the Red Hook drill), /closer (look closer: a question about a photo that stays on the phone).

POST /api/message {"text", "lat", "lon", "image"?, "from"?} -> the router: Muse picks tools; the versions + "tools"
POST /api/alert {"text", "lat", "lon"} -> {"message" (SMS), "text", "laptop" (WhatsApp map), "small", "image", "kit"}
POST /api/photo {"image": data URL} -> {"sms", "text", "mono", "small", "kit", "photo_id"}; /api/photo/high and
     /api/photo/zoom {"photo_id", "n"?} -> the detailed WhatsApp drawing
POST /api/radar {"lat", "lon"}; /api/directions {"lat", "lon", "to", "from"?}; /api/directions/move {"trip", "metres" |
     "astray"}; /api/pictures {"job"} (how-to pictures made after the words)
Hub: GET /api/hub/state, /api/hub/summary, /api/hub/thread?who=; POST /api/hub/send|assign|closer|block,
     /api/demo/beat {"name"} (sim/redhook.py)
Look closer: GET /api/closer/samples, /api/closer/state?c=; POST /api/closer/start {"sample", "question"}
Also: GET /api/find_rain, /api/samples, /api/sample?name=, /store/<record id>.jpg
"small" is the slow-connection picture (~400 px WebP); "image" the full JPEG; "kit" the plain SMS with codes.
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

from evac import codes, howto, directions, hub, photo, pipeline, radar, router, store, tonal  # noqa: E402
import redhook  # noqa: E402  (the Red Hook drill: simulated neighbours' phones around the hub)
import closer_demo  # noqa: E402  (look closer: a phone asks about a photo it keeps)

PORT = int(os.environ.get("PORT", 8765))
WHATSAPP_COLS = 58  # widest monospace map that fits the simulated WhatsApp Desktop bubble (65% of the chat pane) unwrapped
PAGES = {"/dashboard": "dashboard.html", "/closer": "closer.html"}
sms_parts = codes.sms_parts

SAMPLE_DIR = "/Library/Desktop Pictures"  # macOS ships photos here; used as sample "photos a friend sent"
# One object each, the simplest case for an outline: macOS's account pictures (HEIC, converted with sips).
OBJECT_DIR = "/Library/User Pictures"
OBJECTS = ["Fun/Fortune Cookie", "Fun/Gingerbread Man", "Animals/Penguin", "Animals/Owl", "Animals/Parrot",
           "Instruments/Guitar", "Instruments/Violin", "Flowers/Sunflower", "Flowers/Red Rose", "Nature/Leaf"]
CONVERTED = os.path.join(ROOT, "..", "cache", "samples")
GENERATED = os.path.join(ROOT, "samples")  # one object each, made with Muse Image (muse-image-1.0)


def remember_photo(img, boxes=None, parent=None):
    """A photo (or a crop of one) kept in the store; `boxes` {key number: box in its pixels} for the WhatsApp zooms."""
    return store.new("photo", image=img, meta={"boxes": boxes or {}}, parent=parent)


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


def jpeg(img, side, quality):
    img = img.convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def kit_js():
    """The kit's script with its phrasebook and the issuer's public key in front."""
    return ("var KIT_PUBKEY = %s;\nvar KIT_PACK = %s;\n" % (json.dumps(codes.public_key()), json.dumps(codes.pack())) +
            open(os.path.join(ROOT, "kit.js")).read())


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def send_bytes(self, data, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, out, t=None):
        if t is not None:
            out["seconds"] = round(time.time() - t, 1)
        self.send_bytes(json.dumps(out).encode(), "application/json")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        arg = unquote(query.split("=", 1)[1]) if "=" in query else ""
        html = "text/html; charset=utf-8"
        if path in PAGES:
            return self.send_bytes(open(os.path.join(ROOT, PAGES[path]), "rb").read(), html)
        if path == "/kit.js":
            return self.send_bytes(kit_js().encode(), "application/javascript; charset=utf-8")
        if path == "/kit.html":  # one self-contained file a person can save and use offline
            return self.send_bytes(open(os.path.join(ROOT, "kit.html")).read().replace("/*KIT_JS*/", kit_js()).encode(), html)
        if path.startswith("/store/") and path.endswith(".jpg"):  # a record's original (a report's photo, a zoom)
            img = store.image(path[len("/store/"):-4])
            return self.send_bytes(jpeg(img, 640, 88), "image/jpeg") if img else self.send_error(404)
        if path == "/api/sample":
            p = sample_path(arg)
            return self.send_bytes(jpeg(Image.open(p), 1600, 85), "image/jpeg") if p else self.send_error(404)
        api = {
            "/api/samples": lambda: {"objects": list(sample_objects()), "emergency": list(sample_emergency()), "photos": list(sample_photos())},
            "/api/find_rain": lambda: dict(zip(("lat", "lon"), radar.find_rain())),
            "/api/hub/state": hub.state,
            "/api/hub/summary": lambda: {"first": hub.summary()},
            "/api/hub/thread": lambda: hub.thread(arg),
            "/api/closer/samples": lambda: {"samples": closer_demo.samples()},
            "/api/closer/state": lambda: closer_demo.state(arg),
        }
        if path in api:
            return self.send_json(api[path]())
        return super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        t = time.time()
        at = lambda: (float(body["lat"]), float(body["lon"]))
        try:
            if self.path == "/api/alert":
                res = pipeline.run(body["text"], user=at(), laptop=WHATSAPP_COLS, image=True)
                out = {k: res.get(k) for k in ("message", "affected", "log", "text", "laptop", "image", "small")}
                out["parts"] = sms_parts(out["message"]) if out.get("message") else 0
                if res.get("scene") is not None:  # the same alert for a phone with the kit: words, map code, signature
                    out["kit"] = router.kit_alert(res)
                    out["kit_parts"] = sms_parts(out["kit"])
            elif self.path == "/api/message":
                img = base64.b64decode(body["image"].split(",", 1)[-1]) if body.get("image") else None
                kept = store.image(body["photo_id"]) if body.get("photo_id") else None
                if kept is not None:  # a question about a photo sent earlier in the Photo tab
                    img = jpeg(kept, 4096, 90)
                out = router.handle(body["text"], at(), image=img, who=body.get("from"))
                out["parts"] = sms_parts(out["sms"]) if out.get("sms") else 0
            elif self.path == "/api/photo":  # the quick version (SMS + low-detail WhatsApp outline)
                data = base64.b64decode(body["image"].split(",", 1)[-1])
                try:
                    Image.open(io.BytesIO(data)).verify()
                except Exception:
                    return self.send_json({"error": "That file isn't a picture that can be read.", "log": []}, t)
                out = photo.run(data)
                out["parts"] = sms_parts(out["sms"])
                full = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
                out["photo_id"] = remember_photo(full)
                # For a phone with the kit: the description and a 128 px picture with its named parts; a tap on a part
                # (or a box drawn on the picture) asks the server for a closer look, cropped from this full-size photo.
                parts = codes.picture(full, parts=photo.part_boxes(full, out.get("things") or []), ident=out["photo_id"])
                out["kit"] = router.kit_message(out["text"], parts)
                out["kit_parts"] = sms_parts(out["kit"])
            elif self.path in ("/api/photo/high", "/api/photo/zoom"):
                # High detail: the whole scene in tones. Zoom: the same for one numbered part, on its own crop of the
                # full-size photo; every result is kept, so a zoom can be zoomed again.
                entry, img = store.get(body["photo_id"]), store.image(body["photo_id"])
                if self.path == "/api/photo/zoom":
                    img = img.crop(tuple(entry["meta"]["boxes"][str(body["n"])]))
                work = img.copy()
                work.thumbnail((1600, 1600))
                r = tonal.draw(work)
                k = img.width / work.width
                pid = remember_photo(img, {str(n): [int(v * k) for v in b] for n, b in r["boxes"].items()}, parent=body["photo_id"])
                mono = tonal.text(r)
                out = {"mono": mono, "mono_bytes": len(mono.encode()), "mono_gzip": len(gzip.compress(mono.encode())),
                       "key": r["key"], "seen": r["seen"], "recognised": r["recognised"], "regions": r["regions"], "photo_id": pid}
            elif self.path == "/api/pictures":  # pictures for a how-to answer, made after its words were sent
                p = howto.pictures(body["job"])
                out = dict(router.steps_pictures(p["frames"]), done=p["done"], error=p.get("error"))
            elif self.path == "/api/radar":
                out = radar.run(at())
                out["parts"] = sms_parts(out["sms"])
            elif self.path == "/api/directions":
                out = directions.run(at(), body["to"], start=body.get("from") or None)
                scene = out.pop("scene", None)  # the page gets the rendered versions, not the Scene object
                trip = out.pop("trip", None)
                if "sms" in out:
                    out["parts"] = sms_parts(out["sms"])
                if trip:
                    out.update(trip=store.new("trip", obj=trip, where=trip.you, meta={"to": trip.name}), you=trip.you)
                if trip and trip.route and scene is not None:  # the kit version: words, map and the route as GPX
                    out["kit"] = router.kit_message(out.get("text") or "", codes.map_scene(scene) +
                                                    codes.route([p for p in [trip.you] if p] + trip.route, trip.name))
                    out["kit_parts"] = sms_parts(out["kit"])
            elif self.path == "/api/directions/move":
                # Simulated GPS: walk along the route, or take a wrong turn, then send the update.
                trip = store.obj(body["trip"])
                you = directions.astray(trip) if body.get("astray") else directions.along(trip, float(body.get("metres", 150)))
                out = directions.move(trip, you)
                store.update(body["trip"], obj=trip, where=you)
                out.pop("scene", None)
                out.update(trip=body["trip"], you=you, parts=sms_parts(out["sms"]))
            elif self.path == "/api/hub/send":
                out = {"text": hub.send(body["who"], body["text"])}
            elif self.path == "/api/hub/assign":
                out = hub.assign(body["volunteer"], body["report"])
            elif self.path == "/api/hub/closer":
                out = {"text": hub.ask_closer(body["report"], body.get("box"))}
            elif self.path == "/api/hub/block":
                out = hub.block(tuple(store.get(body["report"])["where"]), int(body.get("radius_m", 30)), body.get("note") or "Danger reported here")
            elif self.path == "/api/demo/beat":  # the Red Hook drill, one beat at a time
                out = redhook.beat(body["name"], **({"pace": float(body["pace"])} if "pace" in body else {})) or {}
            elif self.path == "/api/closer/start":  # a simulated phone asks about one of the sample photos
                out = {"conv": closer_demo.start(body["sample"], body.get("question") or "What is this?")}
            else:
                return self.send_error(404)
        except Exception as e:  # show the failure in the page rather than hanging it
            traceback.print_exc()
            out = {"message": None, "error": "%s: %s" % (type(e).__name__, e), "log": []}
        self.send_json(out, t)


if __name__ == "__main__":
    print("simulator on http://localhost:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
