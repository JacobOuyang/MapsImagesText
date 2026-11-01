"""Look closer: answer a question about a photo that stays on the phone, over text, by asking only for the parts of
it that decide the answer.

The loop is general: the phone holds the original, the server holds the model, and the channel between them is narrow.
  1. The phone sends the question and the cheapest useful view: the whole photo at 128 px, in grey (a few texts).
  2. Muse looks at every view so far (enlarged, each labelled with where it sits in the photo) and either answers,
     saying how sure it is, or asks for ONE more view: a box in the photo, whether colour matters, and why.
  3. The phone's kit crops that box from its original and sends it back as a Z code, in colour if asked.
  4. Again, until Muse answers or the budget runs out (MAX_VIEWS closer looks); then it must answer with what it has.

A spider that might be a black widow is the test case: in grey at 128 px it is a dark blob; the red hourglass under
its abdomen decides the answer, and it is only visible close up and in colour.

SAM 3.1 can't help in round 1: in the 128 px picture a spider is about 14 pixels, and no enlarging, sharpening, tiling
or generative restoring made SAM find it (evals/closer/RESULTS.md; Muse Image's restoration erased the spider). Muse
finds it from context. SAM comes in on the close-ups, where the thing fills the frame: it outlines what Muse asked
to look at, and Muse gets SAM's exact place for it before deciding again.

A question is an `inquiry` record (the picture as received, the question, the phone's picture id and size, every view
and decision); each view that comes back is a `zoom` record whose parent is the inquiry.
"""
import io
import time

from PIL import Image

from . import llm, sam, store

MAX_VIEWS = 3   # closer looks Muse may ask for before it must answer
SHOWN = 512     # pictures are enlarged to this size for Muse: at 128 px a detail is a few pixels
SAM_MIN = 0.5   # SAM's outline of the thing on a close-up counts from this confidence,
SAM_SIZE = 0.15  # and when it is at least this share of the close-up's side (a speck is something else)

ASK = """Someone texted you a question about a photo. Their phone has the full photo, but over text you only get small
pieces of it. {views}

Question: "{question}"

If what you can see answers the question with confidence, answer it. If the detail that decides the answer is too
small or too blurry, or needs colour to see, ask for ONE closer look at it instead: a tight box around that detail in
the whole photo ([left, top, right, bottom] as fractions of its width and height), whether you need colour, what it
is and what you need to see on it, in a few plain words the person will read. {budget}

Reply with JSON: {{"answer": your answer in one or two short plain sentences, or "" if you ask for a closer look,
"sure": "yes" | "fairly" | "no", "need": null or {{"box": [left, top, right, bottom], "colour": true or false,
"look_at": a short noun phrase such as "the dark spot under the shelf", "to_see": a short phrase such as "the markings
under its body", "object": what that thing most likely is, in one or two plain words such as "spider"}},
"safety": one short line of safety advice if the thing could hurt someone, else ""}}"""


def enlarged(img):
    """PNG bytes of `img` enlarged to SHOWN px: what Muse is shown of a small picture."""
    k = SHOWN / max(img.size)
    out = img.convert("RGB").resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def _views(inq):
    """[(label, PNG bytes)]: the whole photo, then every closer look, in order."""
    rec = store.get(inq)
    w, h = rec["meta"]["pic"]["size"]
    out = [("Image 1 is the whole photo at %dx%d pixels, in grey, enlarged." % (w, h), enlarged(store.image(inq)))]
    for i, v in enumerate(rec["meta"].get("views", []), 2):
        if not v.get("zoom"):
            continue
        x, y, bw, bh = v["got_box"]
        label = ("Image %d is a closer look at the part of the photo from %d%% to %d%% across and %d%% to %d%% down, "
                 "%s, sent back by their phone." % (i, 100 * x / w, 100 * (x + bw) / w, 100 * y / h, 100 * (y + bh) / h,
                                                     "in colour" if v["colour"] else "in grey"))
        if v.get("sam"):  # where a segmentation model outlined the thing, in the whole photo's terms
            l, t, r, b = v["sam"]["in_photo"]
            label += (" A segmentation model (SAM 3.1) outlines %s in it, at %d%% to %d%% across and %d%% to %d%% down of the "
                      "whole photo (confidence %.2f)." % ("a " + v["sam"]["phrase"], 100 * l, 100 * r, 100 * t, 100 * b, v["sam"]["score"]))
        elif v.get("sam_tried"):
            label += " A segmentation model (SAM 3.1) found no %s in it." % v["object"]
        out.append((label, enlarged(store.image(v["zoom"]))))
    return out


def _decide(inq):
    """Muse looks at every view so far: -> {"answer", "sure", "need", "safety"}."""
    rec = store.get(inq)
    left = MAX_VIEWS - len(rec["meta"].get("views", []))
    views = _views(inq)
    budget = ("You can ask for %d more closer look%s." % (left, "" if left == 1 else "s") if left > 0 else
              "You can't ask for more: answer now with what you have, and say how sure you are.")
    content = [{"type": "text", "text": ASK.format(views=" ".join(v[0] for v in views), question=rec["meta"]["question"], budget=budget)}]
    content += [llm.image_part(png) for _, png in views]
    try:
        d = llm.chat_json([{"role": "user", "content": content}])
    except Exception as ex:
        d = {"answer": "", "sure": "no", "need": None, "error": str(ex)[:120]}
    need = d.get("need") if left > 0 else None
    if not isinstance(need, dict) or not box_fractions(need.get("box")):
        need = None
    if not need and not d.get("answer"):
        d["answer"] = "I can't tell from what I can see."
        d["sure"] = "no"
    return {"answer": str(d.get("answer") or ""), "sure": str(d.get("sure") or "no"), "safety": str(d.get("safety") or ""),
            "need": need and {"box": box_fractions(need["box"]), "colour": bool(need.get("colour")), "object": str(need.get("object") or ""),
                              "why": _why(need.get("look_at"), need.get("to_see") or need.get("why"))}}


def _why(look_at, to_see):
    """'the spider, in colour, to see the markings under its body' (either part may be missing)."""
    look_at, to_see = str(look_at or "").strip().rstrip("."), str(to_see or "").strip().rstrip(".")
    return (look_at or "one part of the photo") + (" to see " + to_see if to_see and not to_see.startswith("to ") else
                                                  " " + to_see if to_see else "")


def box_fractions(b):
    """[left, top, right, bottom] fractions, clamped; None if it isn't a box."""
    try:
        l, t, r, bo = [min(1.0, max(0.0, float(v))) for v in b]
    except (TypeError, ValueError):
        return None
    return [l, t, r, bo] if r > l and bo > t else None


def _reply(inq, d):
    """The text that goes back to the phone: the answer, or the request its kit answers by itself."""
    rec = store.get(inq)
    meta = rec["meta"]
    decisions = meta.get("decisions", []) + [dict(d, at=time.time())]
    if d["need"]:
        w, h = meta["pic"]["size"]
        l, t, r, b = d["need"]["box"]
        box = [round(l * w), round(t * h), max(1, round((r - l) * w)), max(1, round((b - t) * h))]
        views = meta.get("views", []) + [{"box": box, "colour": d["need"]["colour"], "why": d["need"]["why"],
                                          "object": d["need"].get("object", ""), "asked": time.time()}]
        store.update(inq, meta={"views": views, "decisions": decisions, "status": "asking"})
        return ("Not sure yet: I need a closer look at %s%s. Your kit will crop it from your photo; just tap Send.\nZOOM %s %d %d %d %d%s"
                % (d["need"]["why"], ", in colour" if d["need"]["colour"] else "", meta["pic"]["id"], *box, " C" if d["need"]["colour"] else ""))
    store.update(inq, meta={"decisions": decisions, "status": "answered", "answer": d["answer"], "sure": d["sure"]})
    sure = {"yes": "", "fairly": " (fairly sure)", "no": " (not sure)"}.get(d["sure"], "")
    return d["answer"].rstrip() + sure + ("\n" + d["safety"] if d["safety"] else "")


def start(who, picture, pic_id, question):
    """A question with a photo from a phone (`picture`: the PIL image as received, `pic_id`: the phone's id for it).
    -> (inquiry id, the reply to send)."""
    inq = store.new("inquiry", who=who, image=picture,
                    meta={"question": question, "pic": {"id": pic_id, "size": list(picture.size)}, "views": [], "status": "open"})
    return inq, _reply(inq, _decide(inq))


def _outline(closeup, thing, box, size):
    """SAM 3.1 on a close-up, for what Muse thinks the thing is ("spider"): {box in the close-up, in_photo: the same as
    fractions of the whole photo, score, phrase}, or None (nothing confident and big enough, or no SAM)."""
    if not thing or not sam.available():
        return None
    phrase = thing.lower().removeprefix("the ").removeprefix("a ").strip()
    cw, ch = closeup.size
    try:
        found = [f for f in sam.segment(closeup, phrase) if f["score"] >= SAM_MIN
                 and max((f["box"][2] - f["box"][0]) / cw, (f["box"][3] - f["box"][1]) / ch) >= SAM_SIZE]
    except Exception:
        return None
    if not found:
        return None
    x1, y1, x2, y2 = found[0]["box"]
    bx, by, bw, bh = box
    w, h = size
    to_photo = lambda fx, fy: ((bx + fx * bw) / w, (by + fy * bh) / h)
    (l, t), (r, b) = to_photo(x1 / cw, y1 / ch), to_photo(x2 / cw, y2 / ch)
    return {"box": [x1 / cw, y1 / ch, x2 / cw, y2 / ch], "in_photo": [round(v, 4) for v in (l, t, r, b)],
            "score": round(found[0]["score"], 2), "phrase": phrase}


def open_for(who, pic_id):
    """The inquiry still waiting for a closer look at this phone's picture, or None."""
    return next((r for r in store.find("inquiry") if r["who"] == who and r["meta"]["pic"]["id"] == pic_id
                 and r["meta"].get("status") == "asking"), None)


def receive(who, parent, box, data):
    """A closer look the phone sent back (a Z code's parent, box and picture). -> (inquiry id, the reply) or None when
    no inquiry is waiting for it."""
    rec = open_for(who, parent)
    if rec is None:
        return None
    views = rec["meta"]["views"]
    z = store.new("zoom", image=Image.open(io.BytesIO(data)), parent=rec["id"], box=box, who=who,
                  meta={"bytes": len(data), "colour": views[-1]["colour"]})
    views[-1].update(zoom=z, got_box=box, bytes=len(data), got=time.time())
    views[-1]["sam"] = _outline(store.image(z), views[-1].get("object"), box, rec["meta"]["pic"]["size"])
    views[-1]["sam_tried"] = bool(views[-1].get("object") and sam.available())
    store.update(rec["id"], meta={"views": views})
    return rec["id"], _reply(rec["id"], _decide(rec["id"]))
