"""The high-detail WhatsApp drawing of a photo: the whole scene in tones, not just one outline.

The outline drawing (evac/sketch.py) is the quick, low-detail version: one object, its edge. In most photos that loses
the background and every small thing. Here everything is segmented and shaded by its grey value:

  1. Muse lists every part of the photo, subjects and backgrounds ("eagle", "waterfall", "mist", "cliff").
  2. SAM 3.1 segments each (sketch.find: refined by colour contrast, small ones re-segmented zoomed in); smaller
     things are laid over larger ones, and what no part covers is split into brightness bands.
  3. Recursion: a region whose brightness still varies a lot inside is split in two by brightness, twice at most
     (the mist inside "waterfall", the shadow side of a teapot). Specks that don't stand out are merged into
     their neighbour; specks that do (a duck on pale water) are kept.
  4. The main subject always stands out and the background stays light: background regions are shaded by their
     mean grey with only blank, · and ░; the subject with ░ ▒ ▓ from its own tones, never blank, inside a heavy
     outline; a subject too small to outline is marked █, which nothing else uses (a small bird on bright mist).
  5. Parts get numbers in a key, so "zoom 2" can redraw part 2 the same way, on its own crop (the recursion goes
     one level deeper each time).
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


def _split(L, gray, label, next_label, depth, min_px, root):
    """Split a region in two by brightness while it varies a lot inside; recurse into both halves. New regions
    remember the part they came from in `root`, so a subject split into light and dark halves is still the subject."""
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
            root[next_label] = root[label]
            new.append(next_label)
            next_label += 1
    for lab in [label] + new:
        next_label = _split(L, gray, lab, next_label, depth - 1, min_px, root)
    return next_label


def _merge_small(L, gray, min_px, root):
    """Fold small regions into their most common neighbour, unless they stand out from it or belong to a different
    part (a small dark part of the subject must not vanish into the background)."""
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
                if abs(gray[m].mean() - gray[L == t].mean()) < STANDS_OUT and root.get(t) == root.get(lab, root.get(t)):
                    L[m] = t
                    changed = True
    return L


def regions(img, names):
    """-> (label map on the working grid, grey values, {label: part name}, {label: root label}) covering the whole
    photo. A region's root is the part (or brightness band) it was split from."""
    H = max(8, round(W * img.height / img.width))
    gray = np.asarray(img.convert("L").resize((W, H), Image.BOX), float)
    found = sketch.find(img, names) if names else []
    L = np.zeros((H, W), int)
    named, root, lab = {}, {}, 1
    small = [(n, np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((W, H), Image.BOX)) > 100) for n, m in found]
    for n, m in sorted(small, key=lambda nm: -nm[1].sum()):  # larger first, so smaller things lie on top
        if m.any():
            L[m] = lab
            named[lab], root[lab] = n, lab
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
                root[lab] = lab
                lab += 1
    min_px = max(8, W * H // 300)
    for l in list(np.unique(L)):
        lab = _split(L, gray, l, lab, SPLIT_DEPTH, min_px, root)
    return _merge_small(L, gray, min_px, root), gray, named, root


def _cell(H, Wd, rows, cols, r, c):
    return (slice(r * H // rows, max(r * H // rows + 1, (r + 1) * H // rows)),
            slice(c * Wd // cols, max(c * Wd // cols + 1, (c + 1) * Wd // cols)))


def _levels(values, weights, qs):
    """Cut points (darkest first) at the area-weighted quantiles `qs` of region brightness."""
    vals = np.concatenate([np.full(min(int(w), 3000), v) for v, w in zip(values, weights)]) if len(values) else np.array([255.0])
    return np.quantile(vals, qs)[::-1]


def render(L, gray, named, main, cols=58, root=None, emphasis=True):
    """-> (rows without numbers, rows with numbers, {number: part name}, subject cells (bool rows x cols)).

    emphasis=True (the default): the main subject stands out and the background is light. Background regions use
    only blank, · and ░ (the lightest 60% blank); the subject is shaded from its own tones with ░ ▒ ▓ (never blank,
    so a white mug still shows on a white table) and outlined in heavy lines; a subject too small to outline is
    marked █, which nothing else uses. emphasis=False: every region shaded on one scale (the lightest half blank,
    then · ░ ▒ ▓) and small strongly different regions marked █ or blank: closer to the photo's tones, for comparison."""
    root = root or {l: l for l in np.unique(L)}
    H, Wd = L.shape
    rows = max(4, round(cols * H / Wd / MONO_CELL))
    labels = np.unique(L)
    mean = {l: gray[L == l].mean() for l in labels}
    area = {l: (L == l).sum() for l in labels}
    in_subject = {l: main is not None and root.get(l) == main for l in labels}
    subj_mask = np.isin(L, [l for l in labels if in_subject[l]])
    grid = [[" "] * cols for _ in range(rows)]
    subject_cells = np.zeros((rows, cols), bool)

    if emphasis and subj_mask.any():
        bg = [l for l in labels if not in_subject[l]]
        sj = [l for l in labels if in_subject[l]]
        bg_cuts = _levels([mean[l] for l in bg], [area[l] for l in bg], [0.6, 0.85])
        sj_cuts = _levels([mean[l] for l in sj], [area[l] for l in sj], [1 / 3, 2 / 3])
        cover = np.asarray(Image.fromarray(subj_mask.astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) / 255.0
        tiny = (cover >= 0.3).sum() < 12
        for r in range(rows):
            for c in range(cols):
                ys, xs = _cell(H, Wd, rows, cols, r, c)
                ids, cnt = np.unique(L[ys, xs], return_counts=True)
                if tiny and cover[r, c] >= 0.08:
                    grid[r][c], subject_cells[r, c] = "█", True
                    continue
                if not tiny and cover[r, c] >= 0.3:
                    s_ids = [(i, n) for i, n in zip(ids, cnt) if in_subject[i]]
                    dom = max(s_ids, key=lambda t: t[1])[0]
                    grid[r][c] = "░▒▓"[int(sum(mean[dom] < x for x in sj_cuts))]
                    subject_cells[r, c] = True
                    continue
                b_ids = [(i, n) for i, n in zip(ids, cnt) if not in_subject[i]] or list(zip(ids, cnt))
                dom = max(b_ids, key=lambda t: t[1])[0]
                level = int(sum(mean[dom] < x for x in bg_cuts))
                for i, n in b_ids:  # a small background thing that stands out: one step darker, never █
                    if i != dom and n / cnt.sum() >= 0.1 and mean[i] < mean[dom] - STANDS_OUT and area[i] < 0.02 * L.size:
                        level = min(2, level + 1)
                grid[r][c] = " ·░"[level]
        if not tiny:
            for r, line in enumerate(contour(subj_mask, cols, rows)):
                for c, ch in enumerate(line):
                    if ch != " ":
                        grid[r][c] = sketch.HEAVY.get(ch, ch)
    else:
        cuts = _levels([mean[l] for l in labels], [area[l] for l in labels], [0.5, 0.7, 0.85, 0.95])
        for r in range(rows):
            for c in range(cols):
                ys, xs = _cell(H, Wd, rows, cols, r, c)
                ids, cnt = np.unique(L[ys, xs], return_counts=True)
                dom = ids[cnt.argmax()]
                pick = dom
                for i, n in zip(ids, cnt):
                    if i != dom and n / cnt.sum() >= 0.1 and abs(mean[i] - mean[dom]) >= STANDS_OUT and area[i] < 0.02 * L.size:
                        pick = i
                grid[r][c] = SHADES[int(sum(mean[pick] < x for x in cuts))] if pick == dom else ("█" if mean[pick] < mean[dom] else " ")
                subject_cells[r, c] = in_subject[pick]
        if subj_mask.mean() > 0.01:
            for r, line in enumerate(contour(subj_mask, cols, rows)):
                for c, ch in enumerate(line):
                    if ch != " ":
                        grid[r][c] = ch

    numbered, key = [row[:] for row in grid], {}
    parts_area = {lab: sum(area[l] for l in labels if root.get(l) == lab) for lab in named}
    order = sorted(named, key=lambda lab: (lab != main, -parts_area[lab]))
    for n, lab in enumerate([lab for lab in order if parts_area[lab]], 1):
        ys, xs = np.nonzero(np.isin(L, [l for l in labels if root.get(l) == lab]))
        r = min(rows - 1, int(np.median(ys) * rows / H))
        c = min(cols - 1, int(np.median(xs) * cols / Wd))
        if n < 10:
            numbered[r][c] = str(n)
        key[n] = named[lab]
    return (["".join(r).rstrip() for r in grid], ["".join(r).rstrip() for r in numbered], key, subject_cells)


def draw(img, cols=58, names=None, check=True, segment=True, emphasis=True):
    """The high-detail drawing of `img` (a photo, or a zoomed crop of one). -> {"rows", "plain", "key", "boxes", "parts",
    "main", "subject_cells", "seen", "recognised", "regions", "layers"}; `boxes` maps each key number to its part's box
    in `img` pixels, for zoom; `layers` keeps the segmentation so it can be re-rendered (evals).
    segment=False skips Muse and SAM: brightness regions only."""
    if names is None:
        names = parts(img) if segment else []
    L, gray, named, root = regions(img, names)
    main = next((l for l, n in named.items() if names and n == names[0]), None)
    plain, numbered, key, subject_cells = render(L, gray, named, main, cols, root, emphasis)
    boxes, k = {}, img.width / L.shape[1]
    for n, name in key.items():
        lab = next(l for l, nm in named.items() if nm == name)
        ys, xs = np.nonzero(np.isin(L, [l for l in np.unique(L) if root.get(l) == lab]))
        boxes[n] = (int(xs.min() * k), int(ys.min() * k), int((xs.max() + 1) * k), int((ys.max() + 1) * k))
    seen, recognised = sketch.check(plain, names) if (check and names) else ([], [])
    return {"rows": numbered, "plain": plain, "key": key, "boxes": boxes, "parts": names,
            "main": named.get(main), "subject_cells": subject_cells, "seen": seen, "recognised": recognised,
            "regions": int(len(np.unique(L))), "layers": (L, gray, named, main, root)}


def zoom(img, box, cols=58, check=True, emphasis=True):
    """Redraw one part of a photo the same way, on its own crop with a margin (one level of recursion)."""
    crop = img.crop(zoom_box(img, box))
    return draw(crop, cols, check=check, emphasis=emphasis), crop


def zoom_box(img, box, margin=0.15):
    x0, y0, x1, y1 = box
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return max(0, int(x0 - mx)), max(0, int(y0 - my)), min(img.width, int(x1 + mx)), min(img.height, int(y1 + my))


def text(result):
    """The WhatsApp message body for a drawing."""
    key = " · ".join("%d %s" % (n, name) for n, name in sorted(result["key"].items()))
    return "\n".join(result["rows"] + ["", key, "(Reply ZOOM and a number for a closer look. Muse, shown only this drawing, "
                                       "made out: %s)" % (", ".join(result["seen"]) or "nothing")])
