"""WhatsApp as the text channel (evac/whatsapp.py, sim/whatsapp_server.py), offline: Twilio credentials are cleared
so every reply lands in whatsapp.OUTBOX instead of being sent; Muse is replaced by scripted replies.

    .venv/bin/python -m unittest discover tests
"""
import base64
import hashlib
import hmac
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
import requests  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from evac import codes, router, store, whatsapp  # noqa: E402

ME = "whatsapp:+15550160"
PHOTO = {"NumMedia": "1", "MediaContentType0": "image/jpeg", "MediaUrl0": "https://api.twilio.com/media/x"}


def photo():
    img = Image.new("RGB", (1024, 1024), (200, 200, 200))
    ImageDraw.Draw(img).ellipse((680, 180, 720, 230), fill=(20, 20, 20))
    return img


class Bridge(unittest.TestCase):
    def setUp(self):
        self.env = {k: os.environ.pop(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN") if k in os.environ}
        store.use(tempfile.mkdtemp())
        whatsapp.OUTBOX.clear()
        self.got = []  # what the router was sent
        self.saved = (router.handle, whatsapp._download)
        whatsapp._download = lambda url: photo()

    def tearDown(self):
        router.handle, whatsapp._download = self.saved
        os.environ.update(self.env)

    def script(self, *replies):
        replies = list(replies)

        def handle(text, at, image=None, who=None):
            self.got.append((text, at, who))
            r = replies.pop(0)
            return r(text) if callable(r) else r
        router.handle = handle

    def said(self):
        return [m["body"] for m in whatsapp.OUTBOX]

    def test_help(self):
        whatsapp.reply({"From": ME, "Body": "help"})
        self.assertIn("Send a photo with a question", self.said()[0])

    def test_the_reply_is_the_sms_version(self):
        self.script({"text": "plain words", "sms": "plain words\n\nMAP (north is up):\n==🔴=="}, {"text": "x", "sms": "y"})
        whatsapp.reply({"From": ME, "Body": "Fire near me?"})
        self.assertIn("Share your location", self.said()[-1])  # asked once
        whatsapp.reply({"From": ME, "Body": "", "Latitude": "40.6776", "Longitude": "-74.0086"})
        self.assertEqual(self.said()[-1], "plain words\n\nMAP (north is up):\n==🔴==")
        self.assertEqual(self.got[0], ("Fire near me?", (40.6776, -74.0086), ME))
        whatsapp.reply({"From": ME, "Body": "And now?"})  # the location is remembered
        self.assertEqual(self.got[1][1], (40.6776, -74.0086))

    def test_photo_runs_the_look_closer_loop_with_the_bridge_as_the_phone(self):
        def ask_closer(text):  # Muse can't tell: it asks for the part of the photo with the blob, in colour
            pic = codes.CODE.search(text).group(2)
            return {"text": "Not sure yet: I need a closer look at the dark spot, in colour. Your kit will crop it from your "
                            "photo; just tap Send.\nZOOM %s 82 18 10 12 C" % pic}
        self.script(ask_closer, lambda text: {"text": "Yes, a black widow." if "#Z" in text else "?"})
        whatsapp.reply(dict(PHOTO, From=ME, Body="Is this a black widow?"))
        said = self.said()
        self.assertIn("128 px grey picture", said[0])
        self.assertIn("I need a closer look at the dark spot, in colour.", said[1])
        self.assertIn("Your phone sends that part of the photo in colour", said[1])
        self.assertNotIn("ZOOM", said[1])
        self.assertNotIn("tap Send", said[1])
        self.assertEqual(said[2], "Yes, a black widow.")
        first, second = self.got[0][0], self.got[1][0]
        self.assertTrue(first.startswith("Is this a black widow?\n#P"))
        z = codes.decode(second)["complete"][0]
        self.assertEqual(z["kind"], "Z")
        parent, box, data = codes.read_zoom(z["data"])
        self.assertEqual(parent, codes.CODE.search(first).group(2))
        crop = Image.open(__import__("io").BytesIO(data)).convert("RGB")
        self.assertLess(min(crop.getpixel((x, y))[0] for x in range(crop.width) for y in range(crop.height)), 60)  # the blob is in it


class Webhook(unittest.TestCase):
    """sim/whatsapp_server.py: Twilio's signature is checked; a good request is answered at once; nothing else is served."""

    def setUp(self):
        import whatsapp_server
        self.env = {k: os.environ.get(k) for k in ("TWILIO_AUTH_TOKEN", "PUBLIC_URL", "TWILIO_ACCOUNT_SID")}
        os.environ.update(TWILIO_AUTH_TOKEN="test-token", PUBLIC_URL="https://bridge.example")
        os.environ.pop("TWILIO_ACCOUNT_SID", None)
        self.calls = []
        self.saved = whatsapp.incoming
        whatsapp.incoming = self.calls.append
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), whatsapp_server.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        whatsapp.incoming = self.saved
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def post(self, params, signature):
        return requests.post(self.url + "/whatsapp", data=urlencode(params), headers={
            "Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature}, timeout=10)

    def test_signed_request_is_accepted(self):
        params = {"From": ME, "Body": "hi", "NumMedia": "0"}
        data = "https://bridge.example/whatsapp" + "".join(k + params[k] for k in sorted(params))
        sig = base64.b64encode(hmac.new(b"test-token", data.encode(), hashlib.sha1).digest()).decode()
        r = self.post(params, sig)
        self.assertEqual(r.status_code, 200)
        self.assertIn("<Response>", r.text)
        self.assertEqual(self.calls, [params])

    def test_unsigned_request_is_refused(self):
        self.assertEqual(self.post({"From": ME, "Body": "hi"}, "forged").status_code, 403)
        self.assertEqual(self.calls, [])

    def test_only_the_webhook_is_served(self):
        self.assertEqual(requests.get(self.url + "/dashboard", timeout=10).status_code, 404)
        self.assertEqual(requests.get(self.url + "/", timeout=10).text, "ok")


if __name__ == "__main__":
    unittest.main()
