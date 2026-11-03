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
SAMPLES = os.path.join(ROOT, "samples")
FOLDERS = ("real", "hard", "spiders")  # photos where a small detail decides the answer; the spiders came first
HERE = (40.6776, -74.0086)   # where the phone is (it only matters for local advice)
PAUSE = 1.0                  # seconds the kit takes to crop and send a closer look
CONVS = {}                   # conversation id -> {"texts": [...], "inquiry", "sample", "question", "done"}


def samples():
    """"hard/extinguisher", "spiders/black_widow", ..."""
    return [d + "/" + f[:-4] for d in FOLDERS for f in sorted(os.listdir(os.path.join(SAMPLES, d))) if f.endswith(".jpg")]


def path(sample):
    """The photo's file ("black_widow" alone still means the spider)."""
    return os.path.join(SAMPLES, sample if "/" in sample else "spiders/" + sample) + ".jpg"


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
            phone.got(out["text"])  # the kit crops the original and sends it after PAUSE
        else:
            c["done"] = True
        return out

    phone = Phone("+1555013%d" % len(CONVS), send, delay=PAUSE)
    img = Image.open(path(sample)).convert("RGB")
    threading.Thread(target=phone.text, args=(question + "\n" + codes.text(phone.picture(img)),), daemon=True).start()
    return conv


def state(conv):
    c = CONVS[conv]
    inq = store.get(c["inquiry"]) if c["inquiry"] else None
    return {"sample": c["sample"], "question": c["question"], "done": c["done"], "texts": c["texts"],
            "original_bytes": os.path.getsize(path(c["sample"])),
            "inquiry": inq and {"id": inq["id"], **inq["meta"]}}
