"""Show how to do something with your hands, as a short sequence of text drawings: a flip-book instead of a video.

CPR, a tourniquet, the recovery position, shutting off gas, a fire extinguisher: these are actions, and one still
picture (or a caption) doesn't show the motion. A video is far too big for a text connection. So:

  1. Muse Spark breaks the task into 3-6 moments that can each be one still picture of the same person from the
     same viewpoint: what to say (with the numbers that matter), what the picture shows, the part to mark ("hands",
     "the yellow handle") and the things to draw (person, mannequin, valve).
  2. Muse Image draws the first moment (or starts from the photo the person was sent), then every other moment as an
     edit of that one, so the person, clothes and viewpoint stay the same and only the action changes.
  3. SAM 3.1 finds the things and the marked part in every frame, tracked from the first frame by overlap (SAM mixes
     up near-synonyms). All frames share one crop (the union of what moves), so flipping through them shows the motion
     and nothing jumps.
  4. Each frame becomes text: WhatsApp gets outlines in box-drawing characters with the marked part filled ▓;
     SMS gets a small silhouette in characters that keep their width in iPhone Messages (+ = for the things, # for the
     marked part, figure spaces as blanks). Below each frame: the step and what to look at.

The frames go out as one message per step; the WhatsApp version can also be one message edited in place (the
simulator plays it as an animation).
"""
import base64
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import imagemap, imagesearch, llm, museimage, sam, sketch, tonal
from .photo import BLANK, MONO_CELL, SMS_CELL, contour, subject_mask

MONO_COLS = 40
SMS_COLS = 18
MONO_ROWS = 20     # a frame is at most this tall (a tall picture gets fewer columns instead): it must fit on screen
SMS_ROWS = 9
SMS_FILL = "+=×"   # one per thing, all exactly one digit wide in SF Pro
SMS_MARK = "#"
MONO_MARK = "▓"
PLAN = ("Someone who can only receive text messages needs to do this with their hands, now: %s. %s"
        "Break it into 3 to 6 moments that can each be shown as one still picture of the same person from the same "
        "viewpoint (side view, whole body or whole object visible). For each moment give:\n"
        "- say: the instruction, under 70 characters, with every number that matters (depth, rate, seconds, turns)\n"
        "- show: what the picture shows at that moment, concretely: body position, where the hands are, which way "
        "something turns or points\n"
        "- mark: the one part to highlight in the picture, as a short noun phrase an image segmentation model can find "
        "(e.g. 'hands', 'yellow handle', 'strap')\n"
        "- search: a short web image search that would find a clear picture or diagram of this moment (e.g. 'CPR hand "
        "position chest compressions diagram')\n"
        "Also: things (1 to 3 separate people or objects to outline in every picture, most important first, never the "
        "floor, a wall or the background; each as {\"name\": a short noun phrase easy to tell apart, e.g. 'kneeling "
        "person' or 'person lying on the floor', \"kind\": one plain word for what it is, e.g. 'person', 'valve', "
        "'mannequin'}), repeat (how the moments loop, e.g. 'repeat 100-120 times a minute', or empty) and "
        "title (2-4 words). Reply as JSON: "
        '{"title": "", "things": [{"name": "", "kind": ""}], "steps": [{"say": "", "show": "", "mark": "", "search": ""}], "repeat": ""}')
STYLE = ("Clear instructional photo for a first-aid or safety guide. Plain white background and floor, nothing else in "
         "the picture, even light, side view, everything that matters fully visible. ")
EDIT = ("The same photo: same person, same clothes, same objects, same background, same camera position and framing. "
        "Change only the action, to show this moment: %s")


def _url(img):
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def plan(task, photo=None, caption=""):
    """Muse's steps for `task` (and the photo that shows it, if any). -> dict or None."""
    content = [{"type": "text", "text": PLAN % (task, ("The person was sent a photo of it: %s. " % caption) if caption else "")}]
    if photo is not None:
        content.append({"type": "image_url", "image_url": {"url": _url(photo)}})
    for _ in range(2):
        try:
            d = llm.chat_json([{"role": "user", "content": content}], max_tokens=1500)
        except Exception:
            continue
        steps = [s for s in d.get("steps") or [] if isinstance(s, dict) and s.get("say") and s.get("show")]
        if steps:
            things, kinds = [], {}
            for t in d.get("things") or []:
                name = str(t.get("name") if isinstance(t, dict) else t).strip()
                if name and name not in kinds:
                    things.append(name)
                    kinds[name] = str(t.get("kind") or "").strip() if isinstance(t, dict) else ""
            things = things[:3] or ["person"]
            return {"title": str(d.get("title") or task)[:40], "things": things,
                    "kinds": {t: kinds.get(t) or t.split()[-1] for t in things}, "steps": steps[:6],
                    "repeat": str(d.get("repeat") or "")}
    return None


def frames(p, photo=None):
    """One picture per step: the first made from text (or the photo), the rest as edits of it."""
    first = photo if photo is not None else museimage.generate(STYLE + p["steps"][0]["show"])
    if photo is not None:
        rest = p["steps"]
    else:
        rest = p["steps"][1:]
    with ThreadPoolExecutor(6) as pool:
        made = list(pool.map(lambda s: museimage.generate(EDIT % s["show"], first), rest))
    return made if photo is not None else [first] + made


def _candidates(img, prompts):
    """[(prompt, score, full-size mask)] for every match SAM gives for any of the prompts (objects, not backgrounds)."""
    out = []
    for q in prompts:
        try:
            for f in sam.segment(img, q):
                if f["mask"] is not None and 0 < f["mask"].mean() < sketch.BACKGROUND:
                    out.append((q, f["score"], f["mask"].astype(bool)))
        except Exception:
            pass
    return out


def _iou(a, b):
    return (a & b).sum() / max(1, (a | b).sum())


def track(pics, things, kinds):
    """[(thing, mask)] per frame, the same thing in every frame. SAM mixes up near-synonyms ('kneeling person' also
    matched the person lying down), so: in the first frame each thing takes its own prompt's best match that no
    earlier thing took (else the best match for its last word, 'person'); in later frames each thing takes whichever
    match overlaps its mask in the first frame most. kinds: {thing: one plain word}, the fallback prompt."""
    prompts = list(dict.fromkeys(things + [kinds[t] for t in things]))
    with ThreadPoolExecutor(len(pics)) as pool:
        cands = list(pool.map(lambda im: _candidates(im, prompts), pics))
    first, used = {}, []
    for t in things:
        for q in (t, kinds[t]):
            ok = [c for c in cands[0] if c[0] == q and all(_iou(c[2], u) < 0.5 for u in used)]
            if ok:
                first[t] = max(ok, key=lambda c: c[1])[2]
                used.append(first[t])
                break
    out = []
    for i, cs in enumerate(cands):
        if i == 0:
            out.append([(t, first[t]) for t in things if t in first])
            continue
        frame, taken = [], []
        for t in things:
            if t not in first:
                continue
            ok = [c for c in cs if all(_iou(c[2], u) < 0.5 for u in taken)]
            best = max(ok, key=lambda c: _iou(c[2], first[t]), default=None)
            if best is not None and _iou(best[2], first[t]) > 0.2:
                frame.append((t, best[2]))
                taken.append(best[2])
        out.append(frame)
    return out


def _foreground(img, cols=320):
    """Everything unlike the picture's plain background, at the picture's size."""
    rows = max(8, round(cols * img.height / img.width))
    m = subject_mask(img, cols, rows)
    return np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize(img.size, Image.NEAREST)) > 127


def _crop(masks, size, margin=0.06):
    """One box around everything drawn in every frame (x0, y0, x1, y1)."""
    ys, xs = [], []
    for m in masks:
        yy, xx = np.nonzero(m)
        ys += [yy.min(), yy.max()] if len(yy) else []
        xs += [xx.min(), xx.max()] if len(xx) else []
    if not xs:
        return (0, 0) + size
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return (max(0, int(min(xs) - margin * w)), max(0, int(min(ys) - margin * h)),
            min(size[0], int(max(xs) + margin * w) + 1), min(size[1], int(max(ys) + margin * h) + 1))


def draw_frame(items, mark, box):
    """One frame as text. items: [(name, mask)] in drawing order; mark: mask of the part to highlight or None.
    -> (WhatsApp rows, SMS rows)."""
    x0, y0, x1, y1 = box
    crop = lambda m: m[y0:y1, x0:x1]
    W, H = x1 - x0, y1 - y0
    cols = max(12, min(MONO_COLS, round(MONO_ROWS * W / H * MONO_CELL)))
    rows = max(4, round(cols * H / W / MONO_CELL))
    grid = [[" "] * cols for _ in range(rows)]
    if mark is not None and mark.any():
        cover = np.asarray(Image.fromarray(crop(mark).astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) / 255
        for r, c in zip(*np.nonzero(cover >= 0.3)):
            grid[r][c] = MONO_MARK
    for _, m in items:
        for r, line in enumerate(contour(crop(m), cols, rows)):
            for c, ch in enumerate(line):
                if ch != " " and grid[r][c] == " ":
                    grid[r][c] = ch
    scols = max(6, min(SMS_COLS, round(SMS_ROWS * W / H * SMS_CELL)))
    srows = max(3, round(scols * H / W / SMS_CELL))
    sgrid = [[BLANK] * scols for _ in range(srows)]
    for i, (_, m) in reversed(list(enumerate(items))):  # the first thing on top
        cover = np.asarray(Image.fromarray(crop(m).astype(np.uint8) * 255).resize((scols, srows), Image.BOX)) / 255
        for r, c in zip(*np.nonzero(cover >= 0.4)):
            sgrid[r][c] = SMS_FILL[i % len(SMS_FILL)]
    if mark is not None and mark.any():
        cover = np.asarray(Image.fromarray(crop(mark).astype(np.uint8) * 255).resize((scols, srows), Image.BOX)) / 255
        for r, c in zip(*np.nonzero(cover >= 0.25)):
            sgrid[r][c] = SMS_MARK
    return ["".join(r).rstrip() for r in grid], ["".join(r).rstrip(BLANK) or BLANK for r in sgrid]


# ---------- real pictures from the web, cropped to what matters ----------
# Generated pictures looked plausible but taught badly (hands drifting, wrong grips). Without a photo, each step
# starts from a real instructional picture instead: Muse writes a search for each step, the searches (plus one for
# the task) are pooled into up to POOL openly licensed diagrams and photos, Muse picks for each step the one that
# shows it best and names the area that matters (one good diagram can serve several steps, cropped differently),
# SAM finds that area, and only it (with some context) is drawn: the people and objects outlined, the part that
# matters filled ▓. When Commons and Openverse give fewer than MIN_OPEN pictures, Brave's web image search fills the
# pool after them. Muse Image is the fallback for a step nothing fits.

CANDIDATES = 6
FOCUS_MARGIN = 0.3     # around the critical area, as a share of its size on each side
FOCUS_MIN = 0.3        # the crop is at least this share of the picture's width and height
FOCUS_SHAPE = 0.6      # and its short side at least this share of its long side (a strip of hands is 5 rows of nothing)
PICK = ("These pictures were found on the web for one step of %r.\nThe step must show: %s\nThe part that matters: %s\n"
        "The person will see only a small black-and-white drawing, made of text characters, of the most important area "
        "of ONE picture, so a busy photo turns into noise. Strongly prefer an illustration or diagram of the action on a "
        "plain background; then a photo of just the action on a plain background. The part that matters must be large "
        "and visible; avoid text-heavy charts, old engravings, crowds and training rooms. Pick the closest picture that "
        "shows the part that matters doing the right thing, even if other details differ (the drawing is cropped to that "
        "part and the words carry the rest); 0 only if none shows it. Reply as JSON: "
        '{"pick": the picture number, or 0 if none shows the step, "area": a short noun phrase for the area to crop '
        'to in that picture (the part that matters and just enough around it), "why": a few words}')


POOL = 10
MIN_OPEN = 6           # fewer openly licensed pictures than this: the whole-web search (Brave) fills the pool


def web_pool(task, p):
    """Pictures for the whole task: one search per step plus one for the task, interleaved, fetched. -> [(result, image)]."""
    queries = list(dict.fromkeys([st.get("search") or "" for st in p["steps"]] + [p["title"], task]))
    queries = [q for q in queries if q]
    with ThreadPoolExecutor(len(queries)) as pool:
        lists = list(pool.map(lambda q: imagesearch.search(q, 8), queries))
    words = {w.lower() for q in queries for w in q.replace("-", " ").split() if len(w) >= 3}
    relevant = lambda r: bool(words & {w.lower() for w in r["title"].replace("-", " ").replace("_", " ").split()})
    order, seen = [], set()
    for k in range(max(len(l) for l in lists) if lists else 0):
        for l in lists:
            if k < len(l) and l[k]["url"] not in seen and relevant(l[k]):  # short retries also find shipwrecks
                seen.add(l[k]["url"])
                order.append(l[k])
    with ThreadPoolExecutor(8) as pool:
        pics = [(r, im) for r, im in zip(order[:POOL * 2], pool.map(imagesearch.fetch, order[:POOL * 2])) if im is not None]
    if len(pics) < MIN_OPEN:  # too few openly licensed pictures: fill up from the whole web (Brave), after them
        with ThreadPoolExecutor(len(queries)) as pool:
            more = [x for l in pool.map(lambda q: _safe(imagesearch.brave, q, 6), queries) for x in l or []]
        more = [x for x in dict((x["url"], x) for x in more).values() if x["url"] not in seen]
        with ThreadPoolExecutor(8) as pool:
            pics += [(r, im) for r, im in zip(more[:POOL], pool.map(imagesearch.fetch, more[:POOL])) if im is not None]
    return pics[:POOL]


def web_pick(task, step, pics):
    """-> (picture, search result, area to crop to) for one step, chosen from the pooled pictures, or None."""
    if not pics:
        return None
    content = [{"type": "text", "text": PICK % (task, step["show"], step.get("mark") or "")}]
    for i, (_, im) in enumerate(pics, 1):
        content += [{"type": "text", "text": "Picture %d:" % i}, {"type": "image_url", "image_url": {"url": _url(im)}}]
    try:
        d = llm.chat_json([{"role": "user", "content": content}])
        k = int(d.get("pick") or 0)
    except Exception:
        return None
    if not 1 <= k <= len(pics):
        return None
    r, im = pics[k - 1]
    return im, r, str(d.get("area") or step.get("mark") or "").strip()


def _plain(img):
    """True if the picture's border is one plain colour (a diagram or a studio shot)."""
    a = np.asarray(img.convert("L").resize((64, 64)), float)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return border.std() < 18


def _box(m):
    ys, xs = np.nonzero(m)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def focus(img, mark, area, things, kinds):
    """What a frame is drawn from: the part that matters, the people or objects it belongs to, and the crop around
    them. The crop is the marked part plus every thing it touches (the rescuer and the chest under the hands), with
    a margin, so a frame shows the action and not a strip of hands. -> (box, items [(name, mask)], mark mask)."""
    prompts = list(dict.fromkeys(list(things) + [kinds.get(t, "") for t in things]))
    items, used = [], []
    for q, score, m in sorted(_candidates(img, [q for q in prompts if q]), key=lambda c: -c[1]):
        if len(items) < 3 and all(_iou(m, u) < 0.5 for u in used):
            items.append((q, m))
            used.append(m)
    marks = []
    for q in dict.fromkeys([mark, area]):
        marks = _candidates(img, [q]) if q else []
        if marks:
            break
    mk = max(marks, key=lambda c: c[1])[2] if marks else None
    found_items = items
    if _plain(img):  # a diagram or studio shot: one outline of everything reads better than overlapping outlines
        fg = _foreground(img)
        if 0 < fg.mean() < 0.8:
            items = [("everything", fg)]
    boxes = []
    if mk is not None:
        x0, y0, x1, y1 = _box(mk)
        pad = max(x1 - x0, y1 - y0)
        near = np.zeros_like(mk)
        near[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = True
        boxes = [(x0, y0, x1, y1)] + [_box(m) for n, m in found_items if (m & near).any()]
    elif found_items:
        boxes = [_box(m) for _, m in found_items]
    if not boxes:
        return (0, 0) + img.size, items, mk
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w2, h2 = 1.08 * (x1 - x0) / 2, 1.08 * (y1 - y0) / 2
    w2, h2 = max(w2, FOCUS_SHAPE * h2), max(h2, FOCUS_SHAPE * w2)  # not a strip
    return (max(0, int(cx - w2)), max(0, int(cy - h2)), min(img.width, int(cx + w2)), min(img.height, int(cy + h2))), items, mk


def web_frame(task, step, things, kinds, pics):
    """One step from a real picture (or, if none fits, a generated one), cropped to what matters."""
    got = web_pick(task, step, pics)
    if got:
        img, res, area = got
        source = imagesearch.credit(res)
    else:
        img, area, source = museimage.generate(STYLE + step["show"]), step.get("mark") or "", "made with Muse Image"
    box, items, mk = focus(img, step.get("mark") or "", area, things, kinds)
    if not items:
        items = [("everything", _foreground(img))]
    mono, sms = draw_frame(items, mk, box)
    return {"mono": mono, "sms": sms, "small": imagemap.shrink(img.crop(box), width=240), "source": source, "area": area,
            "page": got[1].get("page") if got else None, "web": bool(got)}


def run(task, photo=None, caption="", source="web"):
    """The whole sequence. -> {"title", "steps": [{"say", "show", "mark", "mono", "sms", "small"}], "repeat", "sms",
    "text", "mono", "frames_mono", "things"} or {"error"}."""
    p = plan(task, photo, caption)
    if not p:
        return {"error": "Muse couldn't break this into steps"}
    tail = [p["repeat"]] if p["repeat"] else []
    text = "\n".join([p["title"] + ":"] + ["%d. %s" % (i + 1, s["say"]) for i, s in enumerate(p["steps"])] + tail)
    if photo is None and source == "web":
        return _run_web(task, p, text, tail)
    try:
        pics = frames(p, photo)
    except Exception as ex:  # no pictures (Muse Image failed): the steps in words still go out
        return {"title": p["title"], "things": p["things"], "steps": [dict(s, mono=[], sms=[], small=None) for s in p["steps"]],
                "repeat": p["repeat"], "text": text, "sms": text, "mono": text, "frames_mono": [], "frames_sms": [],
                "mono_bytes": len(text.encode()), "box": None, "warning": "no frames: %s" % type(ex).__name__}
    size = pics[0].size
    pics = [im.resize(size) for im in pics]

    tracked = track(pics, p["things"], p["kinds"])
    if not all(tracked):  # SAM found nothing in some frame: outline everything that isn't the plain background
        tracked = [t if t else [("figure", _foreground(im))] for t, im in zip(tracked, pics)]

    def mark(i):
        """The part to highlight; of several matches (both people's hands), the one touching the main thing."""
        q = p["steps"][i].get("mark")
        found = _candidates(pics[i], [q]) if q else []
        if not found:
            return None
        if tracked[i]:
            main = tracked[i][0][1]
            ys, xs = np.nonzero(main)
            near = np.zeros_like(main)
            pad = max(4, int(0.05 * main.shape[1]))
            near[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad] = True
            touching = [c for c in found if (c[2] & near).sum() > 0.5 * c[2].sum()]
            found = touching or found
        return max(found, key=lambda c: c[1])[2]

    with ThreadPoolExecutor(len(pics)) as pool:
        marks = list(pool.map(mark, range(len(pics))))
    segs = list(zip(tracked, marks))
    box = _crop([m for items, _ in segs for _, m in items] + [m for _, m in segs if m is not None], size)
    n = len(p["steps"])
    legend_mono = "%s · %s = the part to look at" % (" · ".join(p["things"]), MONO_MARK)
    legend_sms = " ".join("%s %s" % (SMS_FILL[i], t) for i, t in enumerate(p["things"])) + " · %s look here" % SMS_MARK
    out_steps, mono_msgs, sms_msgs = [], [], []
    for i, (s, (items, mark)) in enumerate(zip(p["steps"], segs)):
        mono, sms = draw_frame(items, mark, box)
        head = "%d/%d %s" % (i + 1, n, s["say"])
        out_steps.append(dict(s, mono=mono, sms=sms, small=imagemap.shrink(pics[i], width=240)))
        mono_msgs.append("\n".join([head] + mono + (["(%s)" % s["mark"]] if s.get("mark") else [])))
        sms_msgs.append("\n".join([head] + sms))
    sms = "\n\n".join([p["title"] + " (" + legend_sms + ")"] + sms_msgs + tail)
    mono = "\n\n".join([p["title"] + " (" + legend_mono + ")"] + mono_msgs + tail)
    return {"title": p["title"], "things": p["things"], "steps": out_steps, "repeat": p["repeat"], "text": text,
            "sms": sms, "mono": mono, "frames_mono": mono_msgs, "frames_sms": sms_msgs,
            "mono_bytes": len(mono.encode()), "box": box}


def _run_web(task, p, text, tail):
    """Every step from its own real picture, cropped to what matters (frames don't share a crop: different pictures)."""
    pics = _safe(web_pool, task, p) or []
    with ThreadPoolExecutor(len(p["steps"])) as pool:
        made = list(pool.map(lambda st: _safe(web_frame, task, st, p["things"], p["kinds"], pics), p["steps"]))
    n = len(p["steps"])
    out_steps, mono_msgs, sms_msgs = [], [], []
    for i, (s, f) in enumerate(zip(p["steps"], made)):
        head = "%d/%d %s" % (i + 1, n, s["say"])
        if f is None:
            out_steps.append(dict(s, mono=[], sms=[], small=None, source=None))
            mono_msgs.append(head)
            sms_msgs.append(head)
            continue
        out_steps.append(dict(s, **f))
        mono_msgs.append("\n".join([head] + f["mono"] + ["(look at: %s · picture: %s)" % (s.get("mark") or f["area"], f["source"])]))
        sms_msgs.append("\n".join([head] + f["sms"]))
    legend_sms = " ".join("%s %s" % (SMS_FILL[i], t) for i, t in enumerate(p["things"])) + " · %s look here" % SMS_MARK
    sms = "\n\n".join([p["title"] + " (" + legend_sms + ")"] + sms_msgs + tail)
    mono = "\n\n".join([p["title"] + " (%s = the part to look at)" % MONO_MARK] + mono_msgs + tail)
    return {"title": p["title"], "things": p["things"], "steps": out_steps, "repeat": p["repeat"], "text": text,
            "sms": sms, "mono": mono, "frames_mono": mono_msgs, "frames_sms": sms_msgs,
            "mono_bytes": len(mono.encode()), "box": None, "source": "web", "pool": len(pics)}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        import traceback
        traceback.print_exc()
        return None
