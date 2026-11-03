"""WhatsApp as the text channel (Twilio): a person messages the Muse number.

    a photo (with a question)  -> the look-closer loop (evac/closer.py): Muse sees only the 128 px grey picture and
                                  asks for closer looks; this bridge plays the phone's kit, cropping each one from the
                                  original, and shows the person every step and what it cost
    a text with codes          -> straight to the router: what MuZip on the person's computer sends (the photo as a
                                  128 px code with the question, then each close-up Muse asks for)
    any other message          -> a plain answer from Muse Spark (router.handle)
    "help"                     -> what it can do

Twilio posts every incoming message to POST /whatsapp (sim/whatsapp_server.py, a small server of its own; only it
faces the internet, through a tunnel). It checks Twilio's signature, answers at once (Twilio waits at most 15 s; Muse
can take longer) and replies through Twilio's REST API.

.env: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, PUBLIC_URL (the tunnel's https address, which Twilio signs), WHATSAPP_FROM
(default the sandbox, whatsapp:+14155238886). Without credentials, replies are only logged (OUTBOX): what tests read.
"""
import base64
import hashlib
import hmac
import io
import os
import random
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

from . import closer, codes, router, smsimage, store

FROM = os.environ.get("WHATSAPP_FROM", "whatsapp:+14155238886")
MAX_BODY = 1500          # Twilio refuses WhatsApp bodies over 1600 characters
OUTBOX = []              # replies when there are no Twilio credentials (tests, a dry run)
ORIGINALS = {}           # (who, picture id) -> the full photo: what the phone's kit keeps
_pool = ThreadPoolExecutor(4)

HELP = ("Hi! I'm Muse, by text message.\n"
        "- Send a photo with a question (\"which of these is for a headache?\"). I only get a tiny version, so I'll ask "
        "for a closer look at the parts that matter; your photo stays on your phone.\n"
        "- Or just ask me something.")


def _env(name):
    return os.environ.get(name, "")


# ---------- Twilio ----------

def signature_ok(url, params, signature):
    """Twilio's X-Twilio-Signature: HMAC-SHA1 (auth token) of the URL followed by every POST field, sorted by name.
    Without a token (local testing) every request is accepted."""
    token = _env("TWILIO_AUTH_TOKEN")
    if not token:
        return True
    data = url + "".join(k + params[k] for k in sorted(params))
    want = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(want, signature or "")


def log(who, direction, body):
    """Every message in and out, kept in the store (kind "wa_msg"): what the test phone (sim/wa_phone.py) reads."""
    store.new("wa_msg", who=who, meta={"dir": direction, "body": body, "at": time.time()})


def send(to, body):
    """One WhatsApp message (several if it's longer than MAX_BODY)."""
    for chunk in _split(body):
        log(to, "out", chunk)
        if not (_env("TWILIO_ACCOUNT_SID") and _env("TWILIO_AUTH_TOKEN")):
            OUTBOX.append({"to": to, "body": chunk, "at": time.time()})
            continue
        r = requests.post("https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % _env("TWILIO_ACCOUNT_SID"),
                          data={"From": FROM, "To": to, "Body": chunk}, auth=(_env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")),
                          timeout=30)
        if r.status_code >= 300:
            print("twilio refused a message:", r.status_code, r.text[:300], flush=True)


def _split(body):
    out, cur = [], ""
    for line in (body or "").split("\n"):
        if len(cur) + len(line) + 1 > MAX_BODY and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line
    return [c[i:i + MAX_BODY] for c in out + [cur] for i in range(0, max(1, len(c)), MAX_BODY)]


def _download(url, who=""):
    """The photo exactly as WhatsApp delivered it. Its size is kept (a `wa_media` record) to set MuZip against how
    WhatsApp itself sends a photo."""
    auth = (_env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")) if _env("TWILIO_AUTH_TOKEN") else None
    r = requests.get(url, auth=auth, timeout=60)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    store.new("wa_media", who=who, meta={"bytes": len(r.content), "type": r.headers.get("Content-Type", ""), "size": list(img.size),
                                         "format": img.format, "at": time.time()})
    return img.convert("RGB")


# ---------- one incoming message ----------

def incoming(params):
    """A message Twilio posted (its form fields). Does the work in the background; returns at once."""
    _pool.submit(_safe, params)


def _safe(params):
    try:
        reply(params)
    except Exception as ex:
        traceback.print_exc()
        send(params.get("From", ""), "Sorry, something went wrong on my side (%s). Please try again." % type(ex).__name__)


def reply(params):
    """Work out the answer to one message and send it."""
    who, body = params.get("From", ""), (params.get("Body") or "").strip()
    has_photo = int(params.get("NumMedia") or 0) and (params.get("MediaContentType0") or "").startswith("image/")
    log(who, "in", body or ("[photo]" if has_photo else ""))
    if body.lower() in ("help", "hi", "hello", "start", "?") and not has_photo:
        return send(who, HELP)
    if has_photo:
        return look_closer(who, _download(params["MediaUrl0"], who), body or "What is this?")
    out = router.handle(body, who=who)
    send(who, out.get("text") or "I couldn't work that out.")


# ---------- a photo: the look-closer loop, with this bridge as the phone's kit ----------

def look_closer(who, img, question):
    """What a phone with the kit does: send the photo at 128 px in grey with the question, keep the original, and
    answer each request for a closer look by cropping it. The person sees every step and what it cost."""
    pic = random.choice("abcdefghjkmnpqrstuvwxyz") + random.choice("23456789abcdefghjkmnpqrstuvwxyz")
    ORIGINALS[(who, pic)] = img
    message = question + "\n" + codes.text(codes.parts("P", smsimage.webp(img), pic))
    send(who, "Sending your photo as a %d px grey picture: %d texts. Muse only sees that." % (smsimage.SIDE, codes.sms_parts(message)))
    for _ in range(closer.MAX_VIEWS + 1):  # the loop ends by then; this only guards against a runaway
        out = router.handle(message, who=who)
        text = out.get("text") or ""
        m = codes.ZOOM.search(text)
        if not m or m.group(1) != pic:
            return send(who, text or "I couldn't work that out.")
        words = text[:m.start()].replace("Your kit crops it from your photo and sends it back.", "").strip()
        message = codes.answer_zoom(img, m, pic)[0]
        o = codes.zoom_options(m)
        send(who, "%s\n(Your phone sends that part of the photo%s%s: %d texts.)" % (words, " in colour" if o["colour"] else "",
                                                                                 " in high detail" if o["side"] else "", codes.sms_parts(message)))
    send(who, "I couldn't work that out.")
