"""Look closer: answer a question about a photo that stays on the phone, over text, by asking only for the parts of
it that decide the answer.

The loop is general: the phone holds the original, the server holds the model, and the channel between them is narrow.
  1. The phone sends the question and the cheapest useful view: the whole photo at 128 px, in grey (a few texts).
  2. Muse looks at every view so far (enlarged, each labelled with where it sits in the photo, what it cost and
     whether it already shows that part at full resolution) and either answers, saying how sure it is, or asks for ONE
     more view: a box inside any view so far (the whole photo, or a closer look to zoom in step by step), whether
     colour matters, and why.
  3. The phone's kit crops that box from its original and sends it back as a Z code, in colour if asked. Boxes go out
     in the 128 px picture's pixels whatever view they were drawn on: a crop is never smaller than 128 of the
     original's pixels, so even the deepest useful zoom is several of those pixels wide.
  4. Again, until Muse answers or the budget runs out (BUDGET texts from the phone, or MAX_VIEWS closer looks); then
     it must answer with what it has.

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

from . import codes, llm, sam, store

MAX_VIEWS = 8   # closer looks Muse may ask for before it must answer (finding one bottle among 30 took 5),
BUDGET = 160    # or texts the phone may send in all (the photo and every closer look; reading a timetable takes high detail)
FULL = 1.3      # a close-up came back this much wider than asked for: the photo has no finer detail there
SHOWN = 512     # pictures are enlarged to this size for Muse: at 128 px a detail is a few pixels
SAM_MIN = 0.5   # SAM's outline of the thing on a close-up counts from this confidence,
SAM_SIZE = 0.15  # and when it is at least this share of the close-up's side (a speck is something else)
SAME = 0.6      # a box overlapping a view it has already got this much (IoU), at no more detail, shows nothing new

ASK = """Someone texted you a question about a photo. Their phone has the full photo, but over text you only get small
pieces of it. {views}

Question: "{question}"

Answer only when what you can see makes you sure. While you can still ask, never answer that you can't tell and
never answer while only fairly sure: ask for the closer look that would make you sure. If the detail that decides
the answer is too small or too blurry, or needs colour to see, ask for ONE closer look at it: a tight box around
that detail inside one of the images (the whole photo, or a closer look to zoom in further), as [left, top, right,
bottom] fractions of that image's width and height, which image it is in, whether you need colour, what it is and
what you need to see on it, in a few plain words the person will read. Ask for colour when the question names a
colour or colour would help find or identify the thing (in grey an amber bottle looks like any other). Keep the box
tight: the detail and a little margin (a box much bigger than the detail costs texts and shows it no sharper). Don't
ask for a closer look inside an image that already shows its part at full resolution. To identify a few separate
small symbols (two or three warning lights or icons), look at each one by itself: a tight box around just that one
symbol, nothing else in it, one per closer look. Closer looks come back at up to 128 pixels (a small thing at the photo's full
resolution, as many pixels as it has there); to read small text or tell many small things apart (a timetable, price tags, a shelf of labels), ask for high
detail: 256 pixels, about 3 to 4 times the texts. Each image says how much closer it is than the whole photo: asking
again for about the same part at the same detail shows nothing new, so draw a smaller box or ask for high detail.
{budget}

Reply with JSON: {{"answer": your answer in one or two short plain sentences, or "" if you ask for a closer look,
"sure": "yes" | "fairly" | "no", "need": null or {{"in": the image's number, "box": [left, top, right, bottom], "colour": true or false,
"detail": "normal" or "high",
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
    out = [("Image 1 is the whole photo at %dx%d pixels, in grey, enlarged (%d texts)." % (w, h, rec["meta"]["pic"].get("texts", 0)),
            enlarged(store.image(inq)))]
    for i, v in enumerate(got(rec), 2):
        x, y, bw, bh = v["got_box"]
        px = max(store.image(v["zoom"]).size)  # 128 or HIGH, or fewer for a small part at full resolution
        label =("Image %d is a closer look at the part of the photo from %d%% to %d%% across and %d%% to %d%% down, "
                 "%s, %s (%d pixels, about %d times closer than image 1), sent back by their phone (%d texts)." % (
                     i, 100 * x / w, 100 * (x + bw) / w, 100 * y / h, 100 * (y + bh) / h, "in colour" if v["colour"] else "in grey",
                     "in high detail" if v.get("high") else "in normal detail", px,
                     max(1, round(max(w / bw, h / bh) * px / 128)), v.get("texts", 0)))
        if v.get("full"):
            label += " It shows that part at the photo's full resolution: a closer look inside it would show nothing new."
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
    used = texts_used(rec)
    left = min(MAX_VIEWS - len(rec["meta"].get("views", [])), (BUDGET - used) // 5)  # a grey closer look is about 5 texts
    views = _views(inq)
    budget = ("So far their phone has sent %d texts; you can ask for up to %d more closer look%s (about 5 texts each in grey, "
              "15 in colour, 3 to 4 times that in high detail; %d texts left in all)." % (used, left, "" if left == 1 else "s", BUDGET - used) if left > 0 else
              "You can't ask for more: answer now with what you have, and say how sure you are.")
    content = [{"type": "text", "text": ASK.format(views=" ".join(v[0] for v in views), question=rec["meta"]["question"], budget=budget)}]
    content += [llm.image_part(png) for _, png in views]
    for attempt in range(3):
        try:
            d = llm.chat_json([{"role": "user", "content": content}])
        except Exception as ex:
            d = {"answer": "", "sure": "no", "need": None, "error": str(ex)[:120]}
        need = d.get("need") if left > 0 else None
        box = isinstance(need, dict) and in_photo(rec, need.get("in"), box_fractions(need.get("box")))
        high = isinstance(need, dict) and str(need.get("detail") or "").lower() == "high"
        note = None
        if box and attempt < 2:
            # Asking again for a view it has: on a bus timetable runs asked eight times for the same 128 px view
            # of the whole table, which never got sharper.
            note = repeated(rec, box, high)
        elif not box and attempt == 0 and left > 0 and d.get("sure") != "yes" and not d.get("error"):
            # It answered without being sure while it could still look (the prompt says not to; runs that gave up
            # early missed a bottle on the next shelf).
            note = ("You answered without being sure, but you can still ask for %d closer look%s. Ask for the one most "
                    "likely to make you sure; answer only if you are sure." % (left, "" if left == 1 else "s"))
        if not note:
            break
        content[0] = dict(content[0], text=content[0]["text"] + "\n\n" + note)
    if not box:
        need = None
    if not need and not d.get("answer"):
        d["answer"] = "I can't tell from what I can see."
        d["sure"] = "no"
    return {"answer": str(d.get("answer") or ""), "sure": str(d.get("sure") or "no"), "safety": str(d.get("safety") or ""),
            "need": need and {"box": box, "in": need.get("in") or 1, "colour": bool(need.get("colour")), "high": high,
                              "object": str(need.get("object") or ""), "why": _why(need.get("look_at"), need.get("to_see") or need.get("why"))}}


def repeated(rec, box, high):
    """Why a requested box (fractions of the photo) would show nothing new, or None: it is about a view Muse already
    has (image 1, the whole photo at 128 px, counts) at no more detail."""
    w, h = rec["meta"]["pic"]["size"]
    have = [(1, [0, 0, 1, 1], False)]
    for i, v in enumerate(got(rec), 2):
        x, y, bw, bh = v["got_box"]
        have.append((i, [x / w, y / h, (x + bw) / w, (y + bh) / h], v.get("high", False)))
    l, t, r, b = box
    for i, (L, T, R, B), was_high in have:
        inter = max(0, min(r, R) - max(l, L)) * max(0, min(b, B) - max(t, T))
        union = (r - l) * (b - t) + (R - L) * (B - T) - inter
        if union and inter / union >= SAME and (was_high or not high):
            return ("That box is about the same part as image %d at the same detail, so it would show nothing new. Draw a "
                    "smaller box around just the detail, or ask for high detail." % i)
    return None


def got(rec):
    """The closer looks that came back, in order: what Muse calls images 2, 3, ..."""
    return [v for v in rec["meta"].get("views", []) if v.get("zoom")]


def texts_used(rec):
    """Texts the phone has sent for this question: the photo and every closer look."""
    return rec["meta"]["pic"].get("texts", 0) + sum(v.get("texts", 0) for v in got(rec))


def in_photo(rec, image, box):
    """A box as fractions of image `image` (1 = the whole photo, 2... = the closer looks) -> fractions of the whole
    photo, or None. A model that leaves out which image means the whole photo."""
    if not box:
        return None
    try:
        image = int(image or 1)
    except (TypeError, ValueError):
        image = 1
    views = got(rec)
    if not 1 <= image <= len(views) + 1:
        return None
    if image == 1:
        return box
    w, h = rec["meta"]["pic"]["size"]
    x, y, bw, bh = views[image - 2]["got_box"]
    l, t, r, b = box
    return [(x + l * bw) / w, (y + t * bh) / h, (x + r * bw) / w, (y + b * bh) / h]


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
        need = d["need"]
        views = meta.get("views", []) + [{"box": box, "colour": need["colour"], "high": need.get("high", False), "why": need["why"],
                                          "object": need.get("object", ""), "asked": time.time()}]
        store.update(inq, meta={"views": views, "decisions": decisions, "status": "asking"})
        how = ", ".join(x for x, on in (("in colour", need["colour"]), ("in high detail", need.get("high"))) if on)
        return ("Not sure yet: I need a closer look at %s%s. Your kit crops it from your photo and sends it back.\nZOOM %s %d %d %d %d%s%s"
                % (need["why"], ", " + how if how else "", meta["pic"]["id"], *box, " C" if need["colour"] else "", " H" if need.get("high") else ""))
    store.update(inq, meta={"decisions": decisions, "status": "answered", "answer": d["answer"], "sure": d["sure"]})
    sure = {"yes": "", "fairly": " (fairly sure)", "no": " (not sure)"}.get(d["sure"], "")
    return d["answer"].rstrip() + sure + ("\n" + d["safety"] if d["safety"] else "")


def start(who, picture, pic_id, question, texts=0):
    """A question with a photo from a phone (`picture`: the PIL image as received, `pic_id`: the phone's id for it,
    `texts`: what the message cost). -> (inquiry id, the reply to send)."""
    inq = store.new("inquiry", who=who, image=picture,
                    meta={"question": question, "pic": {"id": pic_id, "size": list(picture.size), "texts": texts}, "views": [], "status": "open"})
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
    asked, side = views[-1]["box"], max(box[2], box[3])
    small = max(store.image(z).size) < (codes.HIGH if views[-1].get("high") else 128)  # sent at its own size: full resolution
    views[-1].update(zoom=z, got_box=box, bytes=len(data), got=time.time(), full=small or side > FULL * 1.2 * max(asked[2], asked[3]),
                     texts=codes.sms_parts(codes.text(codes.parts("Z", codes.zoom_head(parent, box) + data, parent[0] + "z"))))
    views[-1]["sam"] = _outline(store.image(z), views[-1].get("object"), box, rec["meta"]["pic"]["size"])
    views[-1]["sam_tried"] = bool(views[-1].get("object") and sam.available())
    store.update(rec["id"], meta={"views": views})
    return rec["id"], _reply(rec["id"], _decide(rec["id"]))
