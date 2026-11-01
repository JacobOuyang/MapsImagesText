"""SMuSe: a small Mac app that sends from your WhatsApp to the Muse number and does the kit's work on this computer.

    .venv/bin/python sim/smuse.py          # http://127.0.0.1:8780 (or double-click SMuSe.app: sim/make_smuse_app.sh)

Drop a photo and ask a question. The kit turns the photo into a 128 px grey code (about 10 texts) and sends it with the
question from your WhatsApp; the original stays here. When Muse asks for a closer look, the kit crops that part of the
original (in colour if asked) and sends it, by itself, until Muse answers. The page shows the conversation, the
original with every crop outlined, and what Muse saw. Plain questions work too.

Muse's replies are read from the bridge's log on this Mac (sim/whatsapp_server.py records every message), so the kit
never reads WhatsApp. Sending: "whatsapp" (from your account; see sim/wa_phone.py: needs Accessibility permission
for this app) or "sim" (signed like Twilio, straight to the bridge; replies still reach your WhatsApp).
"""
import base64
import io
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".."))
from PIL import Image  # noqa: E402

import wa_phone  # noqa: E402
from evac import codes, smsimage  # noqa: E402

PORT = int(os.environ.get("SMUSE_PORT", 8780))
BRIDGE = "http://127.0.0.1:%s" % os.environ.get("WHATSAPP_PORT", 8770)
STATE = {"via": os.environ.get("SMUSE_VIA", "whatsapp"), "conv": None}
_lock = threading.Lock()


def data_url(img, kind="JPEG", side=None):
    img = img.convert("RGB")
    if side:
        img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, kind, quality=85)
    return "data:image/%s;base64,%s" % (kind.lower(), base64.b64encode(buf.getvalue()).decode())


def _say(conv, who, text):
    conv["texts"].append({"from": who, "text": text, "parts": codes.sms_parts(text), "at": time.time()})


def _exchange(conv, message, who):
    """Send one message from you, wait for Muse's replies, record both. -> the replies."""
    t = time.time() - 1
    _say(conv, "you", message)
    wa_phone.send(message, who, STATE["via"], BRIDGE)
    out = wa_phone.replies(who, t)
    for r in out:
        _say(conv, "muse", r)
    if not out:
        _say(conv, "note", "No reply yet. Is the bridge running, and does Twilio's sandbox webhook point at it?")
    return out


def run(conv, img, question):
    try:
        who = wa_phone.me()
        if img is None:
            _exchange(conv, question, who)
            return
        pic = random.choice("abcdefghjkmnpqrstuvwxyz") + random.choice("23456789abcdefghjkmnpqrstuvwxyz")
        sent = smsimage.webp(img)
        conv.update(original=data_url(img, side=900), size=list(smsimage.fit(img).size),
                    seen="data:image/webp;base64," + base64.b64encode(sent).decode())
        message = question + "\n" + codes.text(codes.parts("P", sent, pic))
        for _ in range(5):
            out = _exchange(conv, message, who)
            ask = next((codes.ZOOM.search(r) for r in reversed(out) if codes.ZOOM.search(r)), None)
            if not ask or ask.group(1) != pic:
                return
            nums, colour = [int(x) for x in ask.group(2).split()], bool(ask.group(3))
            data, region, box, size = codes.crop_zoom(img, (0, 0, img.width, img.height), smsimage.fit(img).size, nums, colour=colour)
            conv["views"].append({"box": box, "colour": colour, "bytes": len(data),
                                  "image": "data:image/webp;base64," + base64.b64encode(data).decode()})
            message = codes.text(codes.parts("Z", codes.zoom_head(pic, box) + data, pic[0] + "z"))
    except Exception as ex:
        conv["error"] = str(ex)
    finally:
        conv["done"] = True


def start(img, question):
    conv = {"question": question, "texts": [], "views": [], "done": False, "error": None, "started": time.time()}
    with _lock:
        STATE["conv"] = conv
    threading.Thread(target=run, args=(conv, img, question), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def send(self, data, kind, code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj):
        self.send(json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        if self.path == "/":
            return self.send(open(os.path.join(ROOT, "smuse.html"), "rb").read(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            try:
                me = wa_phone.me()
                me = me[:-4].replace(me[9:-4], "•" * len(me[9:-4])) + me[-4:]  # only the last digits on screen
            except RuntimeError:
                me = None
            return self.json({"via": STATE["via"], "me": me, "conv": STATE["conv"]})
        self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/via":
            STATE["via"] = "sim" if body.get("via") == "sim" else "whatsapp"
            return self.json({"via": STATE["via"]})
        if self.path == "/api/ask":
            if STATE["conv"] and not STATE["conv"]["done"]:
                return self.json({"error": "Still waiting on the last question."})
            img = None
            if body.get("image"):
                img = Image.open(io.BytesIO(base64.b64decode(body["image"].split(",", 1)[1]))).convert("RGB")
            start(img, (body.get("question") or "").strip() or ("What is this?" if img else "help"))
            return self.json({"ok": True})
        self.send(b"not found", "text/plain", 404)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print("SMuSe on http://127.0.0.1:%d" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
