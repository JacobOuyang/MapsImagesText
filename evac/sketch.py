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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import llm, sam
from .photo import MONO_CELL, WORK_COLS, _components, _kmeans, _otsu, contour

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
AGREE = 0.85       # the colour-refined mask must overlap SAM's this much (IoU), or SAM's is kept: eagle 0.90,
                   # dog 0.95 kept; bicycle 0.76, flamingo 0.54 (background pulled in) not
REFINE_PX = 480     # the colour refinement works on the object's crop at most this wide
TINY = 0.03        # a main object smaller than this share of the photo is drawn in its scene, plus a close-up
HEAVY = {"─": "━", "│": "┃", "╭": "┏", "╮": "┓", "╯": "┛", "╰": "┗", "╳": "╋"}  # objects; the scene stays light
SMALL = 0.3        # a thing narrower or shorter than this share of the photo is segmented again, zoomed in


def find(img, things, fallback=""):
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
            if f is None and name == things[0] and fallback:  # SAM didn't know "fortune cookie"; it knows "food"
                f = best(img, fallback)
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
        return refine(img, m)

    with ThreadPoolExecutor(min(8, len(things))) as pool:
        masks = list(pool.map(one, things))
    return [(n, m) for i, (n, m) in enumerate(zip(things, masks))
            if m is not None and m.any() and (i == 0 or m.mean() <= BACKGROUND)]


def refine(img, m):
    """SAM says which pixels are the object; colour contrast says exactly where its edge is. SAM 3.1's masks are
    smooth blobs (an eagle's feathered wing tips and white head were lost), while the colour method alone can't tell
    the bird from the dark waterfall behind it. So: the colour-contrast pixels inside a slightly grown SAM mask,
    plus the eroded core of the SAM mask (parts colour misses, like the white head, and no holes)."""
    ys, xs = np.nonzero(m)
    if len(ys) < 20:
        return m
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    my, mx = max(4, (y1 - y0) // 4), max(4, (x1 - x0) // 4)
    Y0, Y1, X0, X1 = max(0, y0 - my), min(m.shape[0], y1 + my), max(0, x0 - mx), min(m.shape[1], x1 + mx)
    # Work at most REFINE_PX wide: the grow/shrink filters are slow on a full-size crop (one photo took 6 minutes).
    w = min(REFINE_PX, X1 - X0)
    h = max(4, round(w * (Y1 - Y0) / (X1 - X0)))
    colour = _sharp_colour(img.crop((X0, Y0, X1, Y1)).resize((w, h), Image.BOX))
    part = Image.fromarray(m[Y0:Y1, X0:X1].astype(np.uint8) * 255).resize((w, h), Image.BOX)
    k = min(9, max(3, (min(w, h) // 12) | 1))
    grown = part
    for _ in range(2):  # reach ~1/6 of the object beyond SAM's edge: feathered wing tips, legs, antennae
        grown = grown.filter(ImageFilter.MaxFilter(min(15, max(3, (min(w, h) // 12) | 1))))
    grown = np.asarray(grown) > 127
    core = np.asarray(part.filter(ImageFilter.MinFilter(k))) > 127
    sam_px = np.asarray(part) > 127
    edge = colour & grown
    if edge.sum() < 0.4 * sam_px.sum():  # colour sees little of it (a white mug on white): keep SAM's
        return m
    refined = edge | core
    if (refined & sam_px).sum() / max(1, (refined | sam_px).sum()) < AGREE:
        return m  # colour pulled in background (a brick wall behind a bicycle, water around a flamingo)
    both = Image.fromarray((refined * 255).astype(np.uint8)).resize((X1 - X0, Y1 - Y0), Image.NEAREST)
    out = m.copy()
    out[Y0:Y1, X0:X1] = np.asarray(both) > 127
    return out


def _sharp_colour(crop):
    """Pixels unlike the crop's main border colours, unblurred and unclosed (photo.subject_mask smooths the edge
    away; here the edge is the point)."""
    A = np.asarray(crop.convert("YCbCr"), float) * np.array([0.7, 1.2, 1.2])
    C, a = _kmeans(np.concatenate([A[0], A[-1], A[:, 0], A[:, -1]]), 3)
    share = np.bincount(a, minlength=len(C)) / len(a)
    bg = C[(share >= 0.15) | (share == share.max())]
    d = np.sqrt(((A[:, :, None] - bg[None, None]) ** 2).sum(3)).min(2)
    return d > max(_otsu(d.ravel()), 18)


def background(img, cols, rows, k=3):
    """The scene's big shapes from brightness alone: borders between k bands (sky, tree line, water...), heavily
    smoothed so fog gradients and ripples don't become lines."""
    W = WORK_COLS
    H = max(8, round(W * img.height / img.width))
    g = img.convert("L").filter(ImageFilter.GaussianBlur(max(img.size) / 80)).resize((W, H), Image.BOX)
    C, a = _kmeans(np.asarray(g, float).reshape(-1, 1), k)
    L = a.reshape(H, W)
    for _ in range(2):
        for j in range(k):
            for comp in _components(L == j):
                if len(comp) < W * H // 40:
                    nb = [L[yy, xx] for y, x in comp[:500] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                          if 0 <= yy < H and 0 <= xx < W and L[yy, xx] != j]
                    if nb:
                        t = max(set(nb), key=nb.count)
                        for y, x in comp:
                            L[y, x] = t
    grid = [[" "] * cols for _ in range(rows)]
    for j in range(k):
        for r, line in enumerate(contour(L == j, cols, rows)):
            for c, ch in enumerate(line):
                if ch != " " and grid[r][c] == " ":
                    grid[r][c] = ch
    return grid


def scene(img, items, cols):
    """-> (rows without numbers, rows with numbers): the whole photo. Objects in heavy lines where they're big enough
    to outline, as solid blocks where they're a few cells; the scene's big shapes in light lines underneath."""
    rows = max(4, round(cols * img.height / img.width / MONO_CELL))
    grid = background(img, cols, rows)
    marks = []
    for i, (_, m) in enumerate(items):
        cover = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) / 255.0
        if (cover > 0.5).sum() >= 15:
            small = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((WORK_COLS * 2, max(8, round(
                WORK_COLS * 2 * img.height / img.width))), Image.BOX)) > 100
            for r, line in enumerate(contour(small, cols, rows)):
                for c, ch in enumerate(line):
                    if ch != " ":
                        grid[r][c] = HEAVY.get(ch, ch)
        cells = list(zip(*np.nonzero(cover > 0.12))) or [np.unravel_index(cover.argmax(), cover.shape)]
        if (cover > 0.5).sum() < 15:
            for r, c in cells:
                grid[r][c] = "█"
        marks.append(cells)
    numbered = [row[:] for row in grid]
    for i, cells in enumerate(marks):  # the number just right of each object, on a blank cell
        r, c = max(cells, key=lambda rc: rc[1])
        for cc in range(c + 1, min(cols, c + 3)):
            if numbered[r][cc] == " ":
                numbered[r][cc] = str(i + 1)
                break
    return ["".join(r).rstrip() for r in grid], ["".join(r).rstrip() for r in numbered]


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


def picture_image(rows):
    """The drawing as a PIL image in Menlo, the way a person would see it."""
    font = ImageFont.truetype(FONT, 14)
    w = max([len(r) for r in rows] + [1]) * 9 + 20
    im = Image.new("RGB", (w, len(rows) * 17 + 20), "white")
    d = ImageDraw.Draw(im)
    for i, r in enumerate(rows):
        d.text((10, 10 + i * 17), r, font=font, fill="black")
    return im


def picture(rows):
    """The drawing as a PNG data URL."""
    im = picture_image(rows)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def check(rows, names):
    """-> (what Muse sees in the drawing, which of `names` that covers)."""
    try:
        seen = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": SEE},
                                                           {"type": "image_url", "image_url": {"url": picture(rows)}}]}])
        seen = seen.get("things") or []
        seen = [str(x) for x in (seen if isinstance(seen, list) else [seen])]
        if not seen:
            return [], []
        got = llm.chat_json([{"role": "user", "content": MATCH % (names, seen)}]).get("recognised") or []
        got = [str(x) for x in (got if isinstance(got, list) else [got])]
    except Exception as ex:  # a malformed or failed reply counts as "not recognised", never as an error
        return ["(check failed: %s)" % type(ex).__name__], []
    return seen, [n for n in names if n in got]


def draw(img, things, cols=58, fallback=""):
    """The loop. -> {"rows", "items", "recognised", "seen", "attempts"} for the best attempt, or None if SAM found
    none of the things."""
    items = find(img, things[:MAX_THINGS], fallback)
    attempts = []
    tiny = bool(items) and items[0][1].mean() < TINY
    for _ in range(MAX_ROUNDS):
        if not items:
            break
        if tiny:  # a small subject in a big scene: the scene with it marked, then a close-up of it
            sp, sn = scene(img, items, cols)
            cp, cn = compose(items[:1], cols)
            plain, numbered = sp + [""] + cp, sn + ["", "1 close up:"] + cn
        else:
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
        elif not kept and tiny:
            tiny = False  # still nothing: the close-up alone
        else:
            break
    if not attempts:
        return None
    best = max(attempts, key=lambda a: (len(a["recognised"]), -len(a["items"])))
    return dict(best, attempts=attempts)
