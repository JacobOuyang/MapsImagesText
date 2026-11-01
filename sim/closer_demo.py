"""The "look closer" demo: a simulated phone asks a question about a photo it keeps, over text, and the real router
answers, asking the phone for closer looks when it needs them (evac/closer.py). Used by sim/closer.html.

    conv = start("black_widow", "Is this a black widow?")   # runs in the background
    state(conv)                                              # the texts so far, the inquiry, what Muse saw
"""
import os
import threading
import time

from PIL import Image

from evac import codes, router, store
from phones import Phone

ROOT = os.path.dirname(__file__)
SAMPLES = os.path.join(ROOT, "samples", "spiders")
HERE = (40.6776, -74.0086)   # where the phone is (it only matters for local advice)
PAUSE = 1.5                  # seconds the person takes to tap Send in the kit
CONVS = {}                   # conversation id -> {"texts": [...], "inquiry", "sample", "question", "done"}


def samples():
    return sorted(f[:-4] for f in os.listdir(SAMPLES) if f.endswith(".jpg"))


def start(sample, question):
    conv = "c%d" % (len(CONVS) + 1)
    c = CONVS[conv] = {"texts": [], "inquiry": None, "sample": sample, "question": question, "done": False, "started": time.time()}

    def send(who, text):
        c["texts"].append({"from": "phone", "text": text, "parts": codes.sms_parts(text), "at": time.time()})
        out = router.handle(text, HERE, who=who)
        c["inquiry"] = out.get("inquiry") or c["inquiry"]
        c["texts"].append({"from": "service", "text": out["text"], "parts": codes.sms_parts(out["text"]), "at": time.time(),
                           "seconds": out.get("seconds")})
        if codes.ZOOM.search(out["text"]):
            phone.got(out["text"])  # the kit crops the original; after PAUSE the person taps Send
        else:
            c["done"] = True
        return out

    phone = Phone("+1555013%d" % len(CONVS), send, delay=PAUSE)
    img = Image.open(os.path.join(SAMPLES, sample + ".jpg")).convert("RGB")
    threading.Thread(target=phone.text, args=(question + "\n" + codes.text(phone.picture(img)),), daemon=True).start()
    return conv


def state(conv):
    c = CONVS[conv]
    inq = store.get(c["inquiry"]) if c["inquiry"] else None
    return {"sample": c["sample"], "question": c["question"], "done": c["done"], "texts": c["texts"],
            "original_bytes": os.path.getsize(os.path.join(SAMPLES, c["sample"] + ".jpg")),
            "inquiry": inq and {"id": inq["id"], **inq["meta"]}}
