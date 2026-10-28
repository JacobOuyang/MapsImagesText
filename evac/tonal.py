"""The high-detail WhatsApp drawing of a photo: the whole scene in tones, not just one outline.

The outline drawing (evac/sketch.py) is the quick, low-detail version: one object, its edge. In most photos that loses
the background and every small thing. Here everything is segmented and shaded by its grey value:

  1. Muse lists every part of the photo, subjects and backgrounds ("eagle", "waterfall", "mist", "cliff").
  2. SAM 3.1 segments each (sketch.find: refined by colour contrast, small ones re-segmented zoomed in); smaller
     things are laid over larger ones, and what no part covers is split into brightness bands.
  3. Recursion: a region whose brightness still varies a lot inside is split in two by brightness, twice at most
     (the mist inside "waterfall", the shadow side of a teapot). Specks that don't stand out are merged into
     their neighbour; specks that do (a duck on pale water) are kept.
  4. Each region is shaded by its mean grey value: blank (the lightest half of the picture), · ░ ▒ ▓. A cell takes
     its main region's shade, unless a small region that differs from it strongly covers a tenth of it: then it is
     █ (darker) or blank (lighter), so a small dark bird on bright mist stays visible.
  5. Only the main subject gets an outline; parts get numbers in a key, so "zoom 2" can redraw part 2 the same
     way, on its own crop (the recursion goes one level deeper each time).
  6. Muse checks the drawing blind, as in sketch.check.
"""
import base64
import io

import numpy as np
from PIL import Image

from . import llm, sketch
from .photo import MONO_CELL, _components, _kmeans, contour

W = 240             # working grid width (square pixels)
SHADES = " ·░▒▓"    # lightest to darkest; all one cell wide in Menlo
MAX_PARTS = 8
SPLIT_STD = 24      # a region whose grey values vary more than this (std) is split in two
SPLIT_DEPTH = 2
STANDS_OUT = 55     # a small region this much darker or lighter than its neighbour is kept, and marked in its cell
LIST = ("List every distinct part of this photo, so that together they cover the whole picture: the main subjects and "
        "the backgrounds (sky, water, mist, ground, walls...). Each a short noun phrase an image segmentation model could "
        "find. The most important subject first, then the rest from largest to smallest. Reply as JSON: "
        '{"parts": [...]} (at most %d).' % MAX_PARTS)


def parts(img):
    """Muse's list of the photo's parts, main subject first."""
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    for attempt in range(2):
        try:
            d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": LIST},
                                                           {"type": "image_url", "image_url": {"url": url}}]}])
            got = d.get("parts") or []
            return [str(p).strip() for p in (got if isinstance(got, list) else [got]) if str(p).strip()][:MAX_PARTS]
        except Exception:
            if attempt:
                return []


def _split(L, gray, label, next_label, depth, min_px):
    """Split a region in two by brightness while it varies a lot inside; recurse into both halves."""
    m = L == label
    if depth == 0 or m.sum() < 4 * min_px or gray[m].std() < SPLIT_STD:
        return next_label
    C, a = _kmeans(gray[m].reshape(-1, 1), 2)
    dark = np.zeros_like(m)
    dark[m] = a == int(np.argmin(C[:, 0]))
    new = []
    for comp in _components(dark):
        if len(comp) >= min_px:
            ys, xs = zip(*comp)
            L[list(ys), list(xs)] = next_label
            new.append(next_label)
            next_label += 1
    for lab in [label] + new:
        next_label = _split(L, gray, lab, next_label, depth - 1, min_px)
    return next_label


def _merge_small(L, gray, min_px):
    """Fold small regions into their most common neighbour, unless they stand out from it."""
    changed = True
    while changed:
        changed = False
        for lab in np.unique(L):
            m = L == lab
            if m.sum() >= min_px:
                continue
            ys, xs = np.nonzero(m)
            nb = []
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                v = L[np.clip(ys + dy, 0, L.shape[0] - 1), np.clip(xs + dx, 0, L.shape[1] - 1)]
                nb += [x for x in v.tolist() if x != lab]
            if nb:
                t = max(set(nb), key=nb.count)
                if abs(gray[m].mean() - gray[L == t].mean()) < STANDS_OUT:
                    L[m] = t
                    changed = True
    return L


def regions(img, names):
    """-> (label map on the working grid, grey values, {label: part name}) covering the whole photo."""
    H = max(8, round(W * img.height / img.width))
    gray = np.asarray(img.convert("L").resize((W, H), Image.BOX), float)
    found = sketch.find(img, names) if names else []
    L = np.zeros((H, W), int)
    named, lab = {}, 1
    small = [(n, np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((W, H), Image.BOX)) > 100) for n, m in found]
    for n, m in sorted(small, key=lambda nm: -nm[1].sum()):  # larger first, so smaller things lie on top
        if m.any():
            L[m] = lab
            named[lab] = n
            lab += 1
    rest = L == 0
    if rest.any():  # what no part covers: brightness bands
        _, a = _kmeans(gray[rest].reshape(-1, 1), 3)
        band = np.zeros_like(L)
        band[rest] = a + 1
        for b in range(1, 4):
            for comp in _components(band == b):
                ys, xs = zip(*comp)
                L[list(ys), list(xs)] = lab
                lab += 1
    min_px = max(8, W * H // 300)
    for l in list(np.unique(L)):
        lab = _split(L, gray, l, lab, SPLIT_DEPTH, min_px)
    return _merge_small(L, gray, min_px), gray, named


def render(L, gray, named, main, cols=58):
    """-> (rows without numbers, rows with numbers, {number: part name})."""
    H, Wd = L.shape
    rows = max(4, round(cols * H / Wd / MONO_CELL))
    labels = np.unique(L)
    mean = {l: gray[L == l].mean() for l in labels}
    area = {l: (L == l).sum() for l in labels}
    vals = np.concatenate([np.full(min(area[l], 3000), mean[l]) for l in labels])
    cuts = np.quantile(vals, [0.5, 0.7, 0.85, 0.95])[::-1]  # the lightest half stays blank
    level = lambda g: int(sum(g < c for c in cuts))  # strictly darker than a cut: the region at the median stays blank
    grid = [[" "] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * H // rows, max(r * H // rows + 1, (r + 1) * H // rows)
            x0, x1 = c * Wd // cols, max(c * Wd // cols + 1, (c + 1) * Wd // cols)
            ids, cnt = np.unique(L[y0:y1, x0:x1], return_counts=True)
            dom = ids[cnt.argmax()]
            pick = dom
            for i, n in zip(ids, cnt):
                if i != dom and n / cnt.sum() >= 0.1 and abs(mean[i] - mean[dom]) >= STANDS_OUT and area[i] < 0.02 * L.size:
                    pick = i
            grid[r][c] = SHADES[level(mean[pick])] if pick == dom else ("█" if mean[pick] < mean[dom] else " ")
    if main is not None and (L == main).sum() > 0.01 * L.size:  # the main subject's outline on top
        for r, line in enumerate(contour(L == main, cols, rows)):
            for c, ch in enumerate(line):
                if ch != " ":
                    grid[r][c] = ch
    numbered, key = [row[:] for row in grid], {}
    for n, (lab, name) in enumerate(sorted(named.items(), key=lambda kv: -area.get(kv[0], 0) * (kv[0] != main)), 1):
        if lab not in area:
            continue
        ys, xs = np.nonzero(L == lab)
        r = min(rows - 1, int(np.median(ys) * rows / H))
        c = min(cols - 1, int(np.median(xs) * cols / Wd))
        numbered[r][c] = str(n) if n < 10 else numbered[r][c]
        key[n] = name
    return ["".join(r).rstrip() for r in grid], ["".join(r).rstrip() for r in numbered], key


def draw(img, cols=58, names=None):
    """The high-detail drawing of `img` (a photo, or a zoomed crop of one). -> {"rows", "key", "boxes", "parts",
    "seen", "recognised", "regions"}; `boxes` maps each key number to its part's box in `img` pixels, for zoom."""
    names = names or parts(img)
    L, gray, named = regions(img, names)
    main = next((l for l, n in named.items() if names and n == names[0]), None)
    plain, numbered, key = render(L, gray, named, main, cols)
    boxes = {}
    k = img.width / L.shape[1]
    for n, name in key.items():
        lab = next(l for l, nm in named.items() if nm == name)
        ys, xs = np.nonzero(L == lab)
        boxes[n] = (int(xs.min() * k), int(ys.min() * k), int((xs.max() + 1) * k), int((ys.max() + 1) * k))
    seen, recognised = sketch.check(plain, names) if names else ([], [])
    return {"rows": numbered, "key": key, "boxes": boxes, "parts": names, "seen": seen, "recognised": recognised,
            "regions": int(len(np.unique(L)))}


def zoom(img, box, cols=58):
    """Redraw one part of a photo the same way, on its own crop with a margin (one level of recursion)."""
    x0, y0, x1, y1 = box
    mx, my = (x1 - x0) * 0.15, (y1 - y0) * 0.15
    crop = img.crop((max(0, int(x0 - mx)), max(0, int(y0 - my)), min(img.width, int(x1 + mx)), min(img.height, int(y1 + my))))
    return draw(crop, cols), crop


def text(result):
    """The WhatsApp message body for a drawing."""
    key = " · ".join("%d %s" % (n, name) for n, name in sorted(result["key"].items()))
    return "\n".join(result["rows"] + ["", key, "(Reply ZOOM and a number for a closer look. Muse, shown only this drawing, "
                                       "made out: %s)" % (", ".join(result["seen"]) or "nothing")])
