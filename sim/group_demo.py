"""A WhatsApp group with Muse in it, simulated (no WhatsApp): one neighbour shares a photo, the group gets MuZip's
128 px sketch, and whoever asks Muse about it gets an answer from close-ups that Muse asks the sharer's phone for.
The original never leaves the sharer's phone. Everything under the chat is real: the look-closer loop
(evac/closer.py) with Muse Spark and SAM 3.1, and a simulated phone (sim/phones.py) that crops its own original.
Used by sim/group.html.

    conv = start("redhook/flooded_corner")              # Ana shares the photo
    ask(conv, "Ben", "What does the little sign under the street sign say?")
    ask(conv, "Chen", "Which warehouse is this? What number is on the wall?")   # once Ben's is answered
    state(conv)                                         # the group's messages, each question's close-ups and texts
"""
import base64
import io
import os
import threading
import time

from PIL import Image

from evac import closer, codes, router, store
from phones import Phone

ROOT = os.path.dirname(__file__)
MEMBERS = {"Ana": "+15550201", "Ben": "+15550202", "Chen": "+15550203"}
SHARER = "Ana"
CONVS = {}


def start(sample, who=SHARER):
    """`who` shares a sample photo with the group: everyone gets the 128 px code; the original stays on her phone."""
    conv = "g%d" % (len(CONVS) + 1)
    img = Image.open(os.path.join(ROOT, "samples", sample + ".jpg")).convert("RGB")
    c = CONVS[conv] = {"sample": sample, "sharer": who, "messages": [], "questions": [], "busy": False,
                       "original_bytes": os.path.getsize(os.path.join(ROOT, "samples", sample + ".jpg")), "size": list(img.size)}
    phone = c["phone"] = Phone(MEMBERS[who], lambda _, text: _from_phone(c, text), delay=0.6)
    parts = phone.picture(img)
    c["pic"] = parts[0].split(".")[0][2:]  # the phone's id for the picture
    sketch = codes.decode(codes.text(parts))["complete"][0]["data"]
    c["sketch"] = Image.open(io.BytesIO(sketch)).convert("L")
    _say(c, who, "", texts=codes.sms_parts(codes.text(parts)), image="sketch", note="shared a photo")
    return conv


def _say(c, who, text, texts=0, image=None, note=None):
    c["messages"].append({"from": who, "text": text, "texts": texts, "image": image, "note": note, "at": time.time()})


def _from_phone(c, text):
    """The sharer's kit answering a ZOOM by itself: the close-up goes to Muse, and Muse's reply to the group."""
    q = c["questions"][-1]
    q["texts"] += codes.sms_parts(text)
    for i, z in enumerate(codes.decode(text)["complete"]):  # one Z code per close-up Muse asked for
        webp = codes.read_zoom(z["data"])[2]
        _say(c, c["sharer"] + "'s phone", "", texts=codes.sms_parts(text) if i == 0 else 0,
             image="data:image/webp;base64," + base64.b64encode(webp).decode(), note="sent a close-up by itself")
    out = router.handle(text, (0.0, 0.0), who=MEMBERS[c["sharer"]])
    _muse(c, q, out["text"])
    return out


def _muse(c, q, text):
    """Muse's reply in the group; a request for a close-up goes on to the sharer's phone."""
    m = codes.ZOOM.search(text)
    words = text[:m.start()].strip() if m else text
    words = words.replace(" Your kit crops it from your photo and sends it back.", "").replace(" Your kit crops them from your photo and sends them back.", "")
    q["texts"] += codes.sms_parts(text)
    if m:
        _say(c, "Muse", words.replace("Not sure yet: I need", "Asking %s's phone for" % c["sharer"]), texts=codes.sms_parts(text))
        c["phone"].got(text)
    else:
        q["answer"], q["done"] = words, True
        _say(c, "Muse", "@%s %s" % (q["who"], words), texts=codes.sms_parts(text))
        c["busy"] = False


def ask(conv, who, question):
    """A member asks Muse about the shared photo. -> None, or why it can't start yet."""
    c = CONVS[conv]
    if c["busy"]:
        return "Muse is still answering the last question."
    c["busy"] = True
    _say(c, who, "@Muse " + question)
    q = {"who": who, "question": question, "texts": 0, "done": False, "answer": None}
    c["questions"].append(q)

    def run():
        try:  # Muse already holds the group's 128 px sketch: only the question is new
            q["inquiry"], text = closer.start(MEMBERS[c["sharer"]], c["sketch"], c["pic"], question)
            _muse(c, q, text)
        except Exception as ex:
            _say(c, "Muse", "Sorry, something went wrong (%s)." % type(ex).__name__)
            c["busy"] = False
    threading.Thread(target=run, daemon=True).start()
    return None


def state(conv):
    c = CONVS[conv]
    qs = []
    for q in c["questions"]:
        inq = store.get(q["inquiry"]) if q.get("inquiry") else None
        views = closer.got(inq) if inq else []
        qs.append({"who": q["who"], "question": q["question"], "texts": q["texts"], "done": q["done"], "answer": q["answer"],
                   "boxes": [v["got_box"] for v in views], "pic_size": inq and inq["meta"]["pic"]["size"]})
    return {"sample": c["sample"], "sharer": c["sharer"], "messages": c["messages"], "questions": qs, "busy": c["busy"],
            "original_bytes": c["original_bytes"], "size": c["size"], "members": list(MEMBERS) + ["Muse"],
            "sketch": "/api/group/sketch?c=" + conv}


def sketch_png(conv):
    buf = io.BytesIO()
    CONVS[conv]["sketch"].save(buf, "PNG")
    return buf.getvalue()
