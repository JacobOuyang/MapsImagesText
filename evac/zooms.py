"""Zooms the server chooses: a closer drawing of one area, sent only when it changes what the receiver can rebuild.

The receiver of a high drawing can turn it back into grey: every glyph's ink is known, and each drawing carries a
one-line tone legend ("tones ·82 ░64 ▒41 ▓20"). The server has the photo, so it can do the same and see exactly what
is still wrong. From that:

  1. Subject or scene. When the photo has a main subject (it covers 1-60% of the photo and doesn't fill the frame),
     only the subject's error counts: the base drawing is the emphasised one (light background), and every zoom is
     blank outside the subject's outline, blank meaning "keep what's there". Otherwise every pixel counts and the
     base drawing is the faithful one.
  2. Where to look: the error the receiver would see, summed in blocks of one base cell, top 15% of blocks, in
     connected pieces. A piece that fills less than a third of its box (a diagonal ridge, a curving shore) is halved
     along its long side, again and again, so the boxes follow the curve instead of covering the frame.
  3. How big: each box is drawn 2 or 4 times finer than the base drawing, its grid sized to the box (a strip along a
     ridge might be 30 x 5 cells, not 58 x 26), at most 58 columns. Each cell takes one of five shades by its own
     grey (five greys fitted to the box, sent in its legend); the main edge inside (the subject's outline, or the
     line between light and dark when there's a strong one) is traced with ╭ ╮ ╯ ╰ so curves read as curves.
  4. Keep or drop: every candidate is decoded and pasted as the receiver would. Of those that remove at least KEEP of
     the error the base drawing left, the one that removes the most per byte is kept; then the error is measured
     again (so a later zoom can go inside an earlier one) and it starts over. When nothing passes, it stops: some
     photos get no zooms at all, others many. There is no fixed number.

Also here: the receiver's side (glyph ink, legend, decode, paste), shared with the evals.
"""
import base64
import io
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import llm, sketch, tonal
from .photo import MONO_CELL, _components, _kmeans, contour

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
SHADE_CHARS = " ·░▒▓█"
COLS = 58
CANVAS_W = 232      # the receiver's rebuild (and the error the server measures) at this width: 4 x 58
KEEP = 0.04         # a zoom is sent only if it removes at least this share of the error the base drawing left
BG_WEIGHT = 0.05    # with a main subject, background error counts this much (it is meant to be coarse)
SCALES = (2, 4)     # zooms are drawn this many times finer than the base drawing
MIN_COLS = 6
TOP = 0.15          # blocks with the most error, as a share of the blocks that count
FILL = 1 / 3        # a piece of error filling less of its box than this is split along its long side
EDGE = 0.24         # a light/dark split this strong (0-1 grey) inside a scene zoom is traced as a line
SUBJECT_AREA = (0.01, 0.6)
SMS_PART = 67       # UCS-2 characters per SMS part
FOCUS_MARGIN = 0.25 # a detail's zoom box: its own box plus this much on each side, so it is seen in place
MATTERS = ("Someone who cannot see this photo will get only a small black-and-white drawing of it made of text "
           "characters, plus a caption. Which 1 to 3 small details of the photo matter most to understand it or to act on "
           "it: what decides its meaning (where hands are placed, which way a handle or an arrow points, a symbol on a "
           "sign, a marking that identifies an animal or plant, what a person is holding)? Each a short noun phrase an "
           "image segmentation model could find, most important first. Not the whole subject. Reply as JSON: "
           '{"details": [...]}')


# ---------- the receiver: glyphs -> grey ----------

_INK = {}


def ink(ch):
    """Share of a Menlo cell (WhatsApp's cell shape) that the glyph covers in ink."""
    if ch not in _INK:
        font = ImageFont.truetype(FONT_PATH, 40)
        w = font.getlength("M")
        h = w * MONO_CELL
        im = Image.new("L", (int(round(w)), int(round(h))), 255)
        ImageDraw.Draw(im).text((0, h / 2), ch, font=font, fill=0, anchor="lm")
        _INK[ch] = 1 - np.asarray(im, float).mean() / 255
    return _INK[ch]


def ink_map(rows, cols=COLS):
    out = np.zeros((len(rows), cols))
    for r, line in enumerate(rows):
        for c, ch in enumerate(line[:cols]):
            out[r, c] = ink(ch) if ch != " " else 0.0
    return out


def lightness(img, cols, rows):
    """The photo's lightness (0 black .. 1 white) averaged into a cols x rows grid."""
    return np.asarray(img.convert("L").resize((cols, rows), Image.BOX), float) / 255


def legend(rows, light):
    """The mean lightness of the photo's cells drawn with each shading glyph (what the server sends)."""
    out = {}
    for ch in SHADE_CHARS:
        vals = [light[r, c] for r, line in enumerate(rows) for c, x in enumerate(line.ljust(light.shape[1])[:light.shape[1]])
                if r < light.shape[0] and x == ch]
        if vals:
            out[ch] = round(float(np.mean(vals)), 2)
    return out


def legend_text(leg):
    return "tones " + " ".join("%s%d" % (ch, round(100 * v)) for ch, v in leg.items())


def legend_bytes(leg):
    return len(legend_text(leg).encode())


def decode(rows, leg, cols=COLS, transparent=False):
    """A drawing + its legend -> lightness per cell. Outline cells are filled from their neighbours. transparent:
    blank cells are not part of the drawing (NaN: keep what's under them), as in a subject zoom."""
    g = np.full((len(rows), cols), np.nan)
    blank = np.zeros(g.shape, bool)
    for r, line in enumerate(rows):
        for c, ch in enumerate(line.ljust(cols)[:cols]):
            if ch == " " and transparent:
                blank[r, c] = True
            elif ch in leg:
                g[r, c] = leg[ch]
            elif ch == " ":
                g[r, c] = leg.get(" ", 1.0)
    for _ in range(60):
        holes = np.isnan(g) & ~blank
        if not holes.any():
            break
        p = np.pad(g, 1, constant_values=np.nan)
        nb = np.stack([p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]])
        known = (~np.isnan(nb)).sum(0)
        if not (known[holes] > 0).any():
            break
        fill = np.nansum(nb, axis=0) / np.maximum(known, 1)
        g = np.where(holes & (known > 0), fill, g)
    if transparent:
        return g
    return np.where(np.isnan(g), np.nanmean(g) if (~np.isnan(g)).any() else 0.5, g)


def paste(canvas, grey, box, img_size):
    """Paste a decoded drawing (lightness per cell) into `canvas` over `box` (in photo pixels). NaN cells are left."""
    H, W = canvas.shape
    kx, ky = W / img_size[0], H / img_size[1]
    x0, y0, x1, y1 = int(round(box[0] * kx)), int(round(box[1] * ky)), int(round(box[2] * kx)), int(round(box[3] * ky))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    known = ~np.isnan(grey)
    g = np.where(known, grey, 0.0)
    up = lambda a: np.asarray(Image.fromarray(a.astype(np.float32), "F").resize((x1 - x0, y1 - y0), Image.BILINEAR), float)
    part, w = up(np.clip(g, 0, 1) * known), up(known.astype(float))
    region = canvas[y0:y1, x0:x1]
    canvas[y0:y1, x0:x1] = np.where(w > 0.5, part / np.maximum(w, 1e-6), region)


def _blur(x, sigma=1.5, size=7):
    k = np.exp(-((np.arange(size) - size // 2) ** 2) / (2 * sigma ** 2))
    k /= k.sum()
    p = size // 2
    x = np.pad(x, p, mode="reflect")
    x = np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 1, x)
    return np.apply_along_axis(lambda v: np.convolve(v, k, "valid"), 0, x)


def ssim_map(a, b):
    """SSIM per pixel of two lightness images in [0, 1] (Gaussian window 7, sigma 1.5)."""
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    ma, mb = _blur(a), _blur(b)
    va, vb, cov = _blur(a * a) - ma * ma, _blur(b * b) - mb * mb, _blur(a * b) - ma * mb
    return ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma * ma + mb * mb + c1) * (va + vb + c2))


def ssim(a, b):
    return float(ssim_map(a, b).mean())


# ---------- the server: which zooms to send ----------

def matters(img):
    """Muse's 1-3 details that decide what the photo means ("the valve handle", "the arrow"), as SAM prompts."""
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=85)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": MATTERS},
                                                       {"type": "image_url", "image_url": {"url": url}}]}])
        got = d.get("details") or []
        return list(dict.fromkeys(str(x).strip() for x in (got if isinstance(got, list) else [got]) if str(x).strip()))[:3]
    except Exception:
        return []


def focus(img):
    """[(detail, mask at img's size)] for the details that matter, found by SAM. Empty when Muse or SAM has nothing."""
    names = matters(img)
    found = sketch.find(img, names) if names else []
    return [(n, m) for n, m in found if m.any() and m.mean() < 0.5]


def subject_mask(base, size):
    """The main subject of a tonal.draw result as a bool mask of `size` (w, h), or None."""
    L, gray, named, main, root = base["layers"]
    if main is None:
        return None
    m = np.isin(L, [l for l in np.unique(L) if root.get(l) == main])
    if not m.any():
        return None
    return np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize(size, Image.BILINEAR)) > 127


def mode(mask):
    """'subject' when the photo has a main subject that is a focus (not the whole frame), else 'scene'."""
    if mask is None:
        return "scene"
    ys, xs = np.nonzero(mask)
    spans = (xs.max() - xs.min() + 1) / mask.shape[1], (ys.max() - ys.min() + 1) / mask.shape[0]
    ok = SUBJECT_AREA[0] <= mask.mean() <= SUBJECT_AREA[1] and min(spans) < 0.95
    return "subject" if ok else "scene"


def _pieces(blocks, depth=0):
    """Connected high-error blocks -> boxes (in blocks) that follow them: a sparse piece is halved along its long
    side until each part fills its box (curves become a chain of small boxes)."""
    out = []
    for comp in (_components(blocks) if depth == 0 else [blocks]):
        ys, xs = np.array([p[0] for p in comp]), np.array([p[1] for p in comp])
        h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
        if len(comp) < FILL * h * w and max(h, w) >= 4 and depth < 5 and len(comp) >= 4:
            axis = xs if w >= h else ys
            cut = np.median(axis)
            lo = [p for p, a in zip(comp, axis) if a < cut] or comp[:len(comp) // 2]
            hi = [p for p, a in zip(comp, axis) if a >= cut] or comp[len(comp) // 2:]
            out += _pieces(lo, depth + 1) + _pieces(hi, depth + 1)
        else:
            out.append((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, len(comp)))
    return out


def _candidates(err, weight, block, extra=()):
    """Boxes worth trying, as fractions of the photo (x0, y0, x1, y1), most error first."""
    H, W = err.shape
    bh, bw = H // block, W // block
    e = (err * weight)[:bh * block, :bw * block].reshape(bh, block, bw, block).sum((1, 3))
    counts = weight[:bh * block, :bw * block].reshape(bh, block, bw, block).sum((1, 3)) > 0.5 * block * block * BG_WEIGHT
    vals = e[counts]
    if not len(vals) or vals.max() <= 0:
        return list(extra)
    hot = (e >= np.quantile(vals, 1 - TOP)) & (e > 0)
    boxes = []
    for x0, y0, x1, y1, n in _pieces(hot):
        x0, y0, x1, y1 = max(0, x0 - 1), max(0, y0 - 1), min(bw, x1 + 1), min(bh, y1 + 1)  # one block of margin
        if x1 - x0 < 3:
            x0, x1 = max(0, x0 - 1), min(bw, x1 + 1)
        if y1 - y0 < 2:
            y0, y1 = max(0, y0 - 1), min(bh, y1 + 1)
        boxes.append((e[y0:y1, x0:x1].sum(), (x0 / bw, y0 / bh, x1 / bw, y1 / bh)))
    boxes.sort(key=lambda b: -b[0])
    return list(extra) + [b for _, b in boxes[:8]]


def _snap(frac):
    """Box fractions -> whole percents, which is what the ZOOM header says (the receiver places it from those)."""
    x0, y0, x1, y1 = (int(math.floor(frac[0] * 100)), int(math.floor(frac[1] * 100)),
                      int(math.ceil(frac[2] * 100)), int(math.ceil(frac[3] * 100)))
    return max(0, x0), max(0, y0), min(100, max(x0 + 1, x1)), min(100, max(y0 + 1, y1))


def draw_zoom(full, pct, cols, mask=None):
    """One zoom: the photo's box `pct` (whole percents) as `cols` columns of five fitted shades, its main edge traced.
    With `mask` (the subject, full size), cells outside the subject are blank and mean "keep what's there".
    -> (message text, rows, legend)."""
    box = (round(pct[0] * full.width / 100), round(pct[1] * full.height / 100),
           round(pct[2] * full.width / 100), round(pct[3] * full.height / 100))
    crop = full.crop(box)
    rows = max(2, round(cols * crop.height / crop.width / MONO_CELL))
    g = lightness(crop, cols, rows)
    inside = np.ones(g.shape, bool)
    m = None
    if mask is not None:
        m = mask[box[1]:box[3], box[0]:box[2]]
        cover = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) / 255
        inside = cover >= 0.4
    shades = "·░▒▓█" if mask is not None else " ·░▒▓"  # lightest to darkest; with a subject, blank is "not drawn"
    grid = [[" "] * cols for _ in range(rows)]
    if inside.any():
        vals = g[inside].reshape(-1, 1)
        C, _ = _kmeans(vals, min(5, len(np.unique(vals))))
        centres = np.sort(C[:, 0])[::-1]  # lightest first
        for r in range(rows):
            for c in range(cols):
                if inside[r, c]:
                    grid[r][c] = shades[int(np.abs(centres - g[r, c]).argmin())]
    edge = None
    if m is not None and 0.02 < m.mean() < 0.98:
        edge = m
    elif mask is None:  # a scene: trace the split between light and dark if it is strong
        fine = np.asarray(crop.convert("L").resize((cols * 4, rows * 8), Image.BOX), float) / 255
        C, a = _kmeans(fine.reshape(-1, 1), 2)
        if abs(C[0, 0] - C[1, 0]) >= EDGE and 0.1 <= a.mean() <= 0.9:
            edge = (a == int(np.argmin(C[:, 0]))).reshape(fine.shape)
    if edge is not None:
        for r, line in enumerate(contour(edge, cols, rows)):
            for c, ch in enumerate(line):
                if ch != " ":
                    grid[r][c] = ch
    out = ["".join(r).rstrip() for r in grid]
    leg = legend(out, g)
    if mask is not None:
        leg.pop(" ", None)
    head = "ZOOM %d %d %d %d" % pct
    return "\n".join([head] + out + [legend_text(leg)]), out, leg


def plan(full, base, keep=KEEP, budget=None, details=None):
    """Choose the zooms to send after a high drawing. `base` is tonal.draw's result for `full` (any resolution).
    details: [(name, mask)] from focus(): then only those details' error counts (everything else BG_WEIGHT), and each
    detail's own box is always a candidate, so the zooms go to what matters rather than to wherever pixels differ.
    -> {"mode", "base_rows", "base_text", "zooms": [{"pct", "cols", "rows", "text", "bytes", "chars", "removed"}],
        "canvas", "ref", "weight", "error": [error left after the base and after each zoom, as a share of the base's],
        "rounds": [{"error": per-pixel error map, "tried": [{"pct", "cols", "bytes", "removed", "kept"}]}] (for the evals)}"""
    H = round(CANVAS_W * full.height / full.width)
    ref = lightness(full, CANVAS_W, H)
    mask = subject_mask(base, full.size)
    kind = mode(mask)
    L, gray, named, main, root = base["layers"]
    base_rows = tonal.render(L, gray, named, main, COLS, root, emphasis=kind == "subject")[0]
    leg = legend(base_rows, lightness(full, COLS, len(base_rows)))
    canvas = np.ones((H, CANVAS_W))
    paste(canvas, decode(base_rows, leg), (0, 0) + full.size, full.size)
    if kind == "subject":
        m = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((CANVAS_W, H), Image.BOX)) / 255
        weight = BG_WEIGHT + (1 - BG_WEIGHT) * (m > 0.2)
        ys, xs = np.nonzero(mask)
        extra = [(xs.min() / full.width, ys.min() / full.height, (xs.max() + 1) / full.width, (ys.max() + 1) / full.height)]
    else:
        weight, extra = np.ones((H, CANVAS_W)), []
    if details:
        weight = np.full((H, CANVAS_W), BG_WEIGHT)
        extra = []
        for name, m in details:
            m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((CANVAS_W, H), Image.BOX)) / 255 > 0.2
            ys, xs = np.nonzero(m)
            weight[max(0, ys.min() - 3):ys.max() + 4, max(0, xs.min() - 3):xs.max() + 4] = 1.0  # the detail and its edge
            bw, bh = (xs.max() + 1 - xs.min()) / CANVAS_W, (ys.max() + 1 - ys.min()) / H
            extra.append((max(0, xs.min() / CANVAS_W - FOCUS_MARGIN * bw), max(0, ys.min() / H - FOCUS_MARGIN * bh),
                          min(1, (xs.max() + 1) / CANVAS_W + FOCUS_MARGIN * bw), min(1, (ys.max() + 1) / H + FOCUS_MARGIN * bh)))
    total = lambda c: float((weight * (1 - ssim_map(c, ref))).sum())  # what the receiver still gets wrong
    start = total(canvas)
    out = {"mode": kind, "details": [n for n, _ in details or []], "base_rows": base_rows, "base_text": "\n".join(base_rows + [legend_text(leg)]), "zooms": [],
           "ref": ref, "weight": weight, "error": [1.0], "rounds": [], "base_canvas": canvas.copy()}
    sent = len(out["base_text"].encode())
    drawn, chosen = {}, set()  # drawings don't change between rounds; only what they'd remove does
    while start > 0 and (budget is None or sent < budget):
        err = 1 - ssim_map(canvas, ref)
        now = total(canvas)
        best, tried = None, []
        for frac in _candidates(err, weight, CANVAS_W // COLS, extra):
            pct = _snap(frac)
            for s in SCALES:
                cols = max(MIN_COLS, min(COLS, round((pct[2] - pct[0]) / 100 * COLS * s)))
                if pct in chosen:  # a box is sent once, at one level of detail
                    continue
                if (pct, cols) not in drawn:
                    drawn[pct, cols] = draw_zoom(full, pct, cols, mask if kind == "subject" else None)
                text, rows, zleg = drawn[pct, cols]
                trial = canvas.copy()
                box = (pct[0] * full.width / 100, pct[1] * full.height / 100, pct[2] * full.width / 100, pct[3] * full.height / 100)
                paste(trial, decode(rows, zleg, cols, transparent=kind == "subject"), box, full.size)
                removed = (now - total(trial)) / start
                nbytes = len(text.encode())
                tried.append({"pct": pct, "cols": cols, "bytes": nbytes, "removed": removed, "kept": False})
                if removed >= keep and (best is None or removed / nbytes > best["removed"] / best["bytes"]):
                    best = {"pct": pct, "cols": cols, "rows": len(rows), "text": text, "bytes": nbytes,
                            "chars": len(text), "removed": removed, "canvas": trial}
        if not details:  # the subject's box is tried once; the details' boxes stay candidates until each is sent
            extra = []
        out["rounds"].append({"error": err, "tried": tried})
        for t in tried:
            t["kept"] = best is not None and (t["pct"], t["cols"]) == (best["pct"], best["cols"])
        if best is None or (budget is not None and sent + best["bytes"] > budget):
            break
        canvas = best.pop("canvas")
        chosen.add(best["pct"])
        sent += best["bytes"]
        out["zooms"].append(best)
        out["error"].append(total(canvas) / start)
    out["canvas"] = canvas
    out["bytes"] = sent
    out["chars"] = len(out["base_text"]) + sum(z["chars"] for z in out["zooms"])
    out["sms"] = sum(math.ceil(len(t) / SMS_PART) for t in [out["base_text"]] + [z["text"] for z in out["zooms"]])
    return out


# ---------- close-ups of what matters, with what to look for ----------
# A person looks at a picture by going to the part that decides it: the hourglass on a spider's belly, the score line
# on a pill, the edge of a leaf, which way a handle points. Each close-up is drawn like the high drawing with that
# part as its subject (heavy outline, ░▒▓ inside, the rest light), and says in words what to look for and what it
# means for what to do.

DETAIL_COLS = 58
DETAIL_SMS_COLS = 20
DETAIL_MARGIN = 0.35   # context around the part, as a share of its size on each side
DETAILS = ("Someone can't see this photo; they get a caption and small black-and-white close-up drawings made of text. "
           "%sWhich 1 to 3 details would a careful person look at closely to know what this is or what to do (a marking "
           "or pattern, the shape of a part or an edge, a symbol, which way something points or turns)? For each give: "
           "name (a short noun phrase an image segmentation model can find in the photo, a part, not the whole "
           "subject) and look (one sentence under 100 characters: what to look for in the close-up and what it means "
           "for them, e.g. 'A red hourglass on the belly marks a black widow: don't touch it.'). Reply as JSON: "
           '{"details": [{"name": "", "look": ""}]}')


def details(img, question=None):
    """Muse's details to zoom on, with what to look for: [{"name", "look"}]."""
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, "JPEG", quality=85)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    ask = DETAILS % (("They asked: %r. Aim the close-ups at that. " % question) if question else "")
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": ask},
                                                       {"type": "image_url", "image_url": {"url": url}}]}])
    except Exception:
        return []
    out = []
    for x in d.get("details") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            out.append({"name": str(x["name"]).strip(), "look": str(x.get("look") or "").strip()})
    return out[:3]


def close_up(img, name, look="", mask=None):
    """One close-up of the part `name` (SAM finds it unless its `mask` is given).
    -> {"name", "look", "box", "mono", "sms", "rows", "bytes", "legend"} or None (not found)."""
    from .photo import BLANK, SMS_CELL, sms_shape_useful
    if mask is None:
        found = sketch.find(img, [name])
        if not found:
            return None
        mask = found[0][1]
    m = mask
    ys, xs = np.nonzero(m)
    if not len(ys) or m.mean() > 0.6:
        return None
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    side = max(w, h) * (1 + 2 * DETAIL_MARGIN)  # square-ish, so a thin part still gets its surroundings
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    box = (max(0, int(cx - side / 2)), max(0, int(cy - side / 2)), min(img.width, int(cx + side / 2)), min(img.height, int(cy + side / 2)))
    crop = img.crop(box)
    work = crop.copy()
    work.thumbnail((1024, 1024))
    r = tonal.draw(work, DETAIL_COLS, names=[name], check=False)
    rows = r["plain"]
    # SMS: the part filled #, the rest of the crop's darker half +, in characters that keep their width in Messages
    cm = m[box[1]:box[3], box[0]:box[2]]
    srows = max(3, round(DETAIL_SMS_COLS * crop.height / crop.width / SMS_CELL))
    part = np.asarray(Image.fromarray(cm.astype(np.uint8) * 255).resize((DETAIL_SMS_COLS, srows), Image.BOX)) / 255
    grey = lightness(crop, DETAIL_SMS_COLS, srows)
    sms_rows = ["".join("#" if part[rr, c] >= 0.35 else ("+" if grey[rr, c] < np.median(grey) - 0.08 else BLANK)
                        for c in range(DETAIL_SMS_COLS)).rstrip(BLANK) or BLANK for rr in range(srows)]
    ok, why = sms_shape_useful(sms_rows, None, name)
    head = "ZOOM: %s" % name
    mono = "\n".join([head, look] + rows) if look else "\n".join([head] + rows)
    sms = "\n".join([head, look] + (sms_rows if ok else [])) if look else "\n".join([head] + (sms_rows if ok else []))
    leg = legend(rows, lightness(crop, DETAIL_COLS, max(1, len(rows))))
    return {"name": name, "look": look, "box": box, "rows": rows, "mono": mono, "sms": sms, "sms_drawing": ok,
            "sms_why": why, "bytes": len(mono.encode()), "legend": leg}


def close_ups(img, question=None):
    """The details that matter, each as a close-up with what to look for (in parallel)."""
    from concurrent.futures import ThreadPoolExecutor
    ds = details(img, question)
    if not ds:
        return []
    with ThreadPoolExecutor(len(ds)) as pool:
        made = list(pool.map(lambda d: close_up(img, d["name"], d["look"]), ds))
    return [z for z in made if z]


# ---------- the complete WhatsApp picture: sent once, only when it is complete and understood ----------
# The WhatsApp reply used to go out in pieces (an outline first, the high drawing later, close-ups after). Now
# nothing goes to WhatsApp until the whole set is ready and checked:
#   1. Muse lists the photo's parts and, separately, the 1-3 details that decide it (details()); SAM finds both.
#   2. The base drawing is made with the details among its parts, and every detail is carried into it: outlined in
#      light ╭─╮ lines (the subject's outline is heavy ┏━┓) when it covers at least CARRY_CELLS cells, else its cells
#      are marked █. A detail can't vanish into a flat region any more.
#   3. Each detail gets a close-up. It is measured: the receiver's rebuild (base decoded, close-up pasted over its box)
#      must get at least HELPS less wrong over that detail, else it isn't sent.
#   4. Muse is shown only the drawings (base + close-ups, no words) and says what the photo is and what it can make
#      out; a text-only match against the caption's subject and the detail names says what was understood. A
#      close-up is sent if it helps the rebuild or is read (the valve handle barely changed the pixels but is the
#      point). If the subject wasn't understood, the base is redrawn on one tone scale (no emphasis) and checked
#      again; if it still isn't, no drawing of the whole photo is sent, only close-ups that were read.

CARRY_CELLS = 6
DETAIL_MARK = "█"
HELPS = 0.10
SEE_ALL = ("These drawings are made of text characters from one photo: first the whole photo, then close-ups of some of "
           "its parts. Looking only at the drawings, what is the photo of, and which parts can you make out in the "
           'close-ups? Reply as JSON: {"subject": a few words, "parts": [short names]}')


def carry(rows, masks, cols=COLS):
    """Draw each detail's mask into a base drawing: light outline when big enough, else █ on its cells."""
    n = len(rows)
    grid = [list(r.ljust(cols)[:cols]) for r in rows]
    for _, m in masks:
        cover = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((cols, n), Image.BOX)) / 255
        cells = list(zip(*np.nonzero(cover >= 0.3)))
        if len(cells) >= CARRY_CELLS:
            for r, line in enumerate(contour(m, cols, n)):
                for c, ch in enumerate(line):
                    if ch != " ":
                        grid[r][c] = ch
        else:
            for r, c in cells or [np.unravel_index(int(cover.argmax()), cover.shape)]:
                grid[r][c] = DETAIL_MARK
    return ["".join(r).rstrip() for r in grid]


def _rebuild(img, rows, ups):
    """The receiver's grey rebuild: the base decoded, then each close-up decoded and pasted over its box."""
    H = round(CANVAS_W * img.height / img.width)
    canvas = np.ones((H, CANVAS_W))
    paste(canvas, decode(rows, legend(rows, lightness(img, COLS, len(rows)))), (0, 0) + img.size, img.size)
    for z in ups:
        paste(canvas, decode(z["rows"], z["legend"], DETAIL_COLS), z["box"], img.size)
    return canvas


def _measure(img, rows, z, mask):
    """How much less wrong the rebuild gets over the detail when its close-up is pasted in (share of before)."""
    ref = lightness(img, CANVAS_W, round(CANVAS_W * img.height / img.width))
    before, after = _rebuild(img, rows, []), _rebuild(img, rows, [z])
    m = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(ref.shape[::-1], Image.BOX)) / 255 > 0.2
    ys, xs = np.nonzero(m)
    if not len(ys):
        return 0.0
    sl = (slice(max(0, ys.min() - 3), ys.max() + 4), slice(max(0, xs.min() - 3), xs.max() + 4))
    e0, e1 = float((1 - ssim_map(before, ref))[sl].mean()), float((1 - ssim_map(after, ref))[sl].mean())
    return (e0 - e1) / e0 if e0 > 0 else 0.0


def _stack(drawings):
    """Drawings (lists of rows) rendered in Menlo one under the other, as a PNG data URL."""
    ims = [sketch.picture_image(r) for r in drawings]
    w = max(i.width for i in ims)
    out = Image.new("RGB", (w, sum(i.height for i in ims) + 24 * len(ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height + 24
    out.thumbnail((2000, 4000))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def understood(drawings, subject, parts):
    """-> (what Muse saw, which of [subject] + parts it named) from the drawings alone."""
    try:
        seen = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": SEE_ALL},
                                                           {"type": "image_url", "image_url": {"url": _stack(drawings)}}]}])
        said = [str(seen.get("subject") or "")] + [str(x) for x in (seen.get("parts") or []) if str(x)]
        names = [subject] + list(parts)
        got = llm.chat_json([{"role": "user", "content": sketch.MATCH % (names, said)}]).get("recognised") or []
        got = [str(x) for x in (got if isinstance(got, list) else [got])]
    except Exception as ex:
        return ["(check failed: %s)" % type(ex).__name__], []
    return said, [n for n in names if n in got]


def complete(img, question=None, subject=""):
    """The whole WhatsApp reply for a photo, built and checked before anything is sent. `subject`: what the caption
    says the photo is (the check must find it). -> {"base", "close_ups", "dropped", "details", "understood",
    "seen", "recognised", "attempts", "mono", "mono_bytes", "messages"}."""
    from concurrent.futures import ThreadPoolExecutor
    work = img.copy()
    work.thumbnail((1600, 1600))
    with ThreadPoolExecutor(2) as pool:
        f_parts, f_details = pool.submit(tonal.parts, work), pool.submit(details, work, question)
        names, ds = f_parts.result() or [], f_details.result()
    found = dict(sketch.find(work, [d["name"] for d in ds])) if ds else {}
    ds = [d for d in ds if d["name"] in found and 0 < found[d["name"]].mean() < 0.6]
    masks = [(d["name"], found[d["name"]]) for d in ds]
    if subject:  # the drawing is centred on what the caption says the photo is (not on Muse's first-listed part)
        names = [subject] + [n for n in names if n.lower() != subject.lower()]
    all_names = names + [d["name"] for d in ds if d["name"] not in names]
    with ThreadPoolExecutor(1 + len(ds)) as pool:
        f_base = pool.submit(tonal.draw, work, COLS, all_names, False)
        ups = [pool.submit(close_up, work, d["name"], d["look"], found[d["name"]]) for d in ds]
        base = f_base.result()
        ups = [u.result() for u in ups]
    subject = subject or base["main"] or (names[0] if names else "")
    L, gray, named, main, root = base["layers"]
    attempts = []
    for emphasis in (True, False):
        rows = carry(tonal.render(L, gray, named, main, COLS, root, emphasis=emphasis)[0], masks)
        made = [dict(z, gain=_measure(work, rows, z, m)) for z, (_, m) in zip(ups, masks) if z is not None]
        seen, rec = understood([rows] + [z["rows"] for z in made], subject, [z["name"] for z in made])
        kept = [z for z in made if z["gain"] >= HELPS or z["name"] in rec]  # it helps the rebuild, or it is read
        attempts.append({"emphasis": emphasis, "rows": rows, "kept": kept, "dropped": [z for z in made if z not in kept],
                         "seen": seen, "recognised": rec})
        if subject in rec:
            break
    best = max(attempts, key=lambda a: (subject in a["recognised"], len(a["recognised"]), a["emphasis"]))
    ok = subject in best["recognised"]
    marks = [d["name"] for d in ds]
    if ok:
        base_text = "\n".join(best["rows"] + ([("Marked in the drawing (╭╮ outline, █ small): " + ", ".join(marks))] if marks else []))
        msgs = [base_text] + [z["mono"] for z in best["kept"]]
    else:  # the whole drawing wasn't understood: don't send it; only close-ups that were read on their own
        best["kept"] = [z for z in best["kept"] if z["name"] in best["recognised"]]
        msgs = ["(No drawing of the whole photo: shown only the drawing, Muse couldn't tell what it was.)"] + [z["mono"] for z in best["kept"]]
    check = "(Muse, shown only these drawings, made out: %s)" % (", ".join(s for s in best["seen"] if s) or "nothing")
    mono = "\n\n".join(msgs + [check])
    return {"base": best["rows"], "close_ups": best["kept"], "dropped": [(z["name"], round(z["gain"], 2)) for z in best["dropped"]],
            "details": ds, "understood": ok, "subject": subject, "seen": best["seen"],
            "recognised": best["recognised"], "attempts": [{k: a[k] for k in ("emphasis", "seen", "recognised")} for a in attempts],
            "mono": mono, "mono_bytes": len(mono.encode()), "messages": msgs + [check]}
