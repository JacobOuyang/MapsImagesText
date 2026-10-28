"""A photo drawn in text as the several things in it, checked by Muse Spark before it is sent.

    1. Muse lists what it sees, most important first ("dog", "red ball", "park bench").
    2. SAM 3.1 finds each of them (in parallel) and gives its mask.
    3. The masks become one drawing: each thing's outline (marching squares, photo.contour), more important things
       on top, cropped to all of them, plus a number on each and a key.
    4. The check: the drawing, without numbers or key, is rendered as an image in Menlo (what a person sees; read as
       raw text, Muse recognised 1 of 6 outlines, as an image 4 of 6) and Muse, in a fresh request without the
       caption, lists what it can make out. A second, text-only request matches that against step 1's list.
    5. Repair: things Muse didn't recognise are dropped and the drawing redrawn (the crop zooms in on what is left);
       if it recognised nothing, the main thing is tried alone. The best attempt is sent.

Only the WhatsApp version is drawn this way; SMS keeps the single-object silhouette.
"""
import base64
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import llm, sam
from .photo import MONO_CELL, WORK_COLS, contour

MAX_THINGS = 5
MAX_ROUNDS = 3  # the first drawing and up to two repairs
FONT = "/System/Library/Fonts/Menlo.ttc"
SEE = ("This is a line drawing made with text characters from a photo. List the things you can recognise in it, "
       "most obvious first. Only list what you can actually make out from the shapes; an empty list is fine. "
       'Reply as JSON: {"things": [short names]}')
MATCH = ("Someone made a drawing of these things: %s. Looking at the drawing without being told, a viewer named: %s. "
         "Which of the drawn things did the viewer name? Count the same thing, a synonym or a broader name for it "
         "('bird' for 'penguin', 'cup' for 'mug'). "
         'Reply as JSON: {"recognised": [the drawn things they named, spelled as in the first list]}')


BACKGROUND = 0.35  # a thing whose mask covers more of the photo than this is a background, not an object
SMALL = 0.3        # a thing narrower or shorter than this share of the photo is segmented again, zoomed in


def find(img, things):
    """[(name, full-size mask)] for the things SAM finds: one match each, the biggest confident one. Small ones are
    segmented a second time on a crop around them, so their outline isn't a few blocky pixels."""

    def best(im, name):
        for prompt in dict.fromkeys([name, name.split()[-1]]):
            found = [f for f in sam.segment(im, prompt) if f["mask"] is not None]
            if found:
                ok = [f for f in found if f["score"] >= 0.8 * found[0]["score"]]
                return max(ok, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))
        return None

    def one(name):
        try:
            f = best(img, name)
            if f is None:
                return None
            m = f["mask"]
            x1, y1, x2, y2 = f["box"]
            if (x2 - x1) < SMALL * img.width and (y2 - y1) < SMALL * img.height:
                mx, my = (x2 - x1) * 0.5, (y2 - y1) * 0.5
                box = (max(0, int(x1 - mx)), max(0, int(y1 - my)), min(img.width, int(x2 + mx) + 1), min(img.height, int(y2 + my) + 1))
                z = best(img.crop(box), name)
                if z is not None:
                    m = np.zeros_like(m)
                    m[box[1]:box[3], box[0]:box[2]] = z["mask"]
        except Exception:
            return None
        return m

    with ThreadPoolExecutor(len(things)) as pool:
        masks = list(pool.map(one, things))
    return [(n, m) for i, (n, m) in enumerate(zip(things, masks))
            if m is not None and m.any() and (i == 0 or m.mean() <= BACKGROUND)]


def compose(items, cols):
    """-> (rows without numbers, rows with numbers): every item's outline in one drawing, cropped to all of them.
    Where masks overlap the more important (earlier) item wins. Masks are cropped at full size first, then brought
    to the working grid, so a small object is drawn from all the detail SAM gave it."""
    full = np.zeros(items[0][1].shape, np.uint8)
    for i, (_, m) in enumerate(items):
        full[(full == 0) & m] = i + 1
    ys, xs = np.nonzero(full)
    pad_y, pad_x = max(2, int((ys.max() - ys.min()) * 0.05)), max(2, int((xs.max() - xs.min()) * 0.05))
    full = full[max(0, ys.min() - pad_y): ys.max() + pad_y + 1, max(0, xs.min() - pad_x): xs.max() + pad_x + 1]
    W = WORK_COLS
    H = max(8, round(W * full.shape[0] / full.shape[1]))
    L = np.asarray(Image.fromarray(full).resize((W, H), Image.NEAREST)).astype(int)
    rows = max(4, round(cols * L.shape[0] / L.shape[1] / MONO_CELL))
    grid = [[" "] * cols for _ in range(rows)]
    for i in range(len(items)):
        for r, line in enumerate(contour(L == i + 1, cols, rows)):
            for c, ch in enumerate(line):
                if ch != " " and grid[r][c] == " ":
                    grid[r][c] = ch
    numbered = [row[:] for row in grid]
    for i in range(len(items)):  # the number goes inside its outline, nearest the middle
        inside = np.asarray(Image.fromarray((L == i + 1).astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) > 127
        cells = [(r, c) for r, c in zip(*np.nonzero(inside)) if numbered[r][c] == " "]
        if cells:
            cy, cx = np.mean([r for r, _ in cells]), np.mean([c for _, c in cells])
            r, c = min(cells, key=lambda rc: (rc[0] - cy) ** 2 * 4 + (rc[1] - cx) ** 2)
            numbered[r][c] = str(i + 1)
    return ["".join(r).rstrip() for r in grid], ["".join(r).rstrip() for r in numbered]


def picture(rows):
    """The drawing as a PNG in Menlo, the way a person would see it."""
    font = ImageFont.truetype(FONT, 14)
    w = max(len(r) for r in rows) * 9 + 20
    im = Image.new("RGB", (w, len(rows) * 17 + 20), "white")
    d = ImageDraw.Draw(im)
    for i, r in enumerate(rows):
        d.text((10, 10 + i * 17), r, font=font, fill="black")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def check(rows, names):
    """-> (what Muse sees in the drawing, which of `names` that covers)."""
    seen = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": SEE},
                                                       {"type": "image_url", "image_url": {"url": picture(rows)}}]}])
    seen = [str(x) for x in seen.get("things") or []]
    if not seen:
        return [], []
    got = llm.chat_json([{"role": "user", "content": MATCH % (names, seen)}]).get("recognised") or []
    return seen, [n for n in names if n in got]


def draw(img, things, cols=58):
    """The loop. -> {"rows", "items", "recognised", "seen", "attempts"} for the best attempt, or None if SAM found
    none of the things."""
    items = find(img, things[:MAX_THINGS])
    attempts = []
    for _ in range(MAX_ROUNDS):
        if not items:
            break
        plain, numbered = compose(items, cols)
        names = [n for n, _ in items]
        seen, recognised = check(plain, names)
        attempts.append({"items": names, "rows": numbered, "seen": seen, "recognised": recognised})
        if len(recognised) == len(names):
            break
        kept = [it for it in items if it[0] in recognised]
        if kept and len(kept) < len(items):
            items = kept  # drop what wasn't recognised; the crop zooms in on the rest
        elif not kept and len(items) > 1:
            items = items[:1]  # nothing recognised: the main thing alone
        else:
            break
    if not attempts:
        return None
    best = max(attempts, key=lambda a: (len(a["recognised"]), -len(a["items"])))
    return dict(best, attempts=attempts)
