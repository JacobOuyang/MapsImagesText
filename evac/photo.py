"""Photos that won't load: the server (which has a connection) looks at the photo with Muse Spark and sends back
what's in it, plus the photo drawn as the bare minimum: the outline of its main subject (zoomed in when the
subject is small), or the borders of its few big shapes when there is no single subject.

  low    - SMS: description + the subject as a solid silhouette (or 3 flat tones), in iPhone-safe characters
  medium - WhatsApp: description + the subject's outline in line-drawing characters
  high   - the photo itself (small: 400 px WebP)
"""
import base64
import gzip
import io
import math
import traceback

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import imagemap, llm, sam

# Lightest to darkest; all exactly one digit wide in SF Pro 17 px (ink measured on a canvas: 0, 24, 52).
BLANK = "\u2007"  # figure space: a blank SMS row that keeps its height
SMS_TONES = BLANK + "+#"  # three flat tones for a photo drawn as its main regions
SMS_CELL = 22 / 10.28   # iMessage line height / digit width
BOXY = 0.85             # a silhouette filling this much of its own box is just a box (a sign, a pill): it shows nothing
MONO_CELL = 16.9 / 7.53  # WhatsApp monospace line height / Menlo cell width at 12.5 px
PROMPT = ("Describe this photo for someone whose phone could not load it. Reply as JSON: "
          '{"caption": one sentence, "details": [up to 3 short facts a person would want to know, including any '
          'readable text], "object": the one main thing the photo shows, as a short noun phrase an image '
          'segmentation model could find (e.g. "penguin", "fortune cookie", "flamingo"), or "" for a scene with '
          'no main thing, "category": one broad word for the main object that a segmentation model would surely know '
          '(e.g. "food", "animal", "bird", "vehicle", "furniture"), "things": [up to 5 separate objects you can see, most important first, each a short noun '
          'phrase a segmentation model could find; the main object first; objects only, not surfaces or backgrounds '
          'such as sky, water, grass, ground, walls or floors]}')


def describe(jpeg):
    """Muse Spark's caption, details and the main object's name (SAM's prompt). Falls back to an empty description."""
    if not llm.available():
        return {"caption": "A photo (no description: model not configured).", "details": [], "object": "", "things": []}
    part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
    for attempt in range(2):  # an occasional empty or malformed reply: ask once more
        try:
            d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": PROMPT}, part]}])
            break
        except Exception as e:
            if attempt:
                return {"caption": "A photo (description failed: %s)." % e, "details": [], "object": "", "things": []}
    obj = str(d.get("object") or "").strip()
    things = d.get("things") or []
    things = [str(t.get("name", "") if isinstance(t, dict) else t).strip() for t in (things if isinstance(things, list) else [things])]
    things = [t for t in things if t]
    if obj and obj not in things:
        things.insert(0, obj)
    return {"caption": d.get("caption") or "", "details": [str(x) for x in d.get("details") or []][:3],
            "object": obj, "category": str(d.get("category") or "").strip(), "things": list(dict.fromkeys(things))}


# ---------- outlines: the bare minimum of a photo ----------
# A tone sketch shades every pixel, so ripples, mist and stripes crowd out the thing the photo is of. Instead:
# find the one object the photo is of (whatever differs from the main colours along its border), crop to it so it
# fills the width a map gets, and draw only its contour. Photos with no single object (a landscape) get the
# borders of their few biggest colour regions instead. SAM 3.1 would give a real object mask; this is the
# no-model stand-in.

WORK_COLS = 240  # the object is found on a grid this wide (square pixels), then cropped and drawn smaller
# Marching squares: the four corners of a cell (top-left, top-right, bottom-right, bottom-left; 1 = inside) ->
# the box-drawing glyph that joins the edge midpoints the contour crosses. All one cell wide in Menlo.
CONTOUR = {0b1000: "╯", 0b0100: "╰", 0b0010: "╭", 0b0001: "╮", 0b1100: "─", 0b1001: "│", 0b1010: "╳"}
CONTOUR.update({0b1111 ^ k: v for k, v in list(CONTOUR.items())})


def _kmeans(X, k, iters=20):
    C = X[np.argsort(X[:, 0])[np.linspace(0, len(X) - 1, k).astype(int)]].copy()  # spread over lightness
    for _ in range(iters):
        a = ((X[:, None] - C[None]) ** 2).sum(2).argmin(1)
        for j in range(k):
            if (a == j).any():
                C[j] = X[a == j].mean(0)
    return C, a


def _otsu(v):
    h, edges = np.histogram(v, 64)
    p, best, t = h / max(1, h.sum()), -1, edges[32]
    for i in range(1, 64):
        w0, w1 = p[:i].sum(), p[i:].sum()
        if w0 and w1:
            m0, m1 = (p[:i] * edges[:i]).sum() / w0, (p[i:] * edges[i:-1]).sum() / w1
            if w0 * w1 * (m0 - m1) ** 2 > best:
                best, t = w0 * w1 * (m0 - m1) ** 2, edges[i]
    return t


def _colour(img, cols, rows):
    """Blurred YCbCr at twice the grid resolution, lightness weighted down so colour separates objects."""
    sm = img.filter(ImageFilter.GaussianBlur(max(img.size) / 200)).convert("YCbCr")
    return np.asarray(sm.resize((cols * 2, rows * 2), Image.BOX), float) * np.array([0.7, 1.2, 1.2])


def _components(m):
    """Connected True regions of a bool array, as lists of cells, biggest first."""
    rows, cols = m.shape
    seen, out = np.zeros_like(m, bool), []
    for r, c in zip(*np.nonzero(m)):
        if seen[r, c]:
            continue
        comp, stack = [], [(r, c)]
        seen[r, c] = True
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < rows and 0 <= xx < cols and m[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        out.append(comp)
    return sorted(out, key=len, reverse=True)


def subject_mask(img, cols, rows):
    """bool (rows, cols): pixels unlike the background. The background is the colours that make up most of the
    photo's border; an object touching the edge (a penguin cut off at the bottom) adds colours that cover only a
    small part of it, so they don't count."""
    A = _colour(img, cols, rows)
    C, a = _kmeans(np.concatenate([A[0], A[-1], A[:, 0], A[:, -1]]), 4)
    share = np.bincount(a, minlength=len(C)) / len(a)
    bg = C[(share >= 0.15) | (share == share.max())]
    d = np.sqrt(((A[:, :, None] - bg[None, None]) ** 2).sum(3)).min(2)
    m = Image.fromarray(((d > max(_otsu(d.ravel()), 18)) * 255).astype(np.uint8)).resize((cols, rows), Image.BOX)
    k = max(3, cols // 34) | 1  # 7 on the working grid: closes icing lines on a cookie, stripes on a zebra
    m = m.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return np.asarray(m) > 110  # median: drop specks; max then min: close gaps


def single_object(m):
    """The biggest piece of a mask, with its holes filled."""
    keep = np.zeros_like(m)
    comps = _components(m)
    if comps:
        ys, xs = zip(*comps[0])
        keep[list(ys), list(xs)] = True
    for comp in _components(~keep):  # background pieces that don't reach the edge are holes
        if not any(y in (0, m.shape[0] - 1) or x in (0, m.shape[1] - 1) for y, x in comp):
            ys, xs = zip(*comp)
            keep[list(ys), list(xs)] = True
    return keep


def contour(mask, cols, rows):
    """A high-resolution mask -> rows of box-drawing characters tracing its edge as one continuous line."""
    blur = mask.shape[1] / cols / 2  # smooth away details smaller than half a character
    im = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(blur))
    k = np.asarray(im.resize((cols + 1, rows + 1), Image.BOX)) > 127
    code = (k[:-1, :-1].astype(int) << 3) | (k[:-1, 1:] << 2) | (k[1:, 1:] << 1) | k[1:, :-1]
    return [[CONTOUR.get(int(v), " ") for v in row] for row in code]


def layout_regions(img, cols, rows, k=3):
    """int (rows, cols): the photo split into k colour regions, small islands merged into their surroundings."""
    A = _colour(img, cols, rows)
    C, a = _kmeans(A.reshape(rows, 2, cols, 2, 3).mean((1, 3)).reshape(-1, 3), k)
    L = a.reshape(rows, cols)
    for _ in range(3):
        for j in range(k):
            for comp in _components(L == j):
                if len(comp) < cols * rows // 25:
                    nb = [L[yy, xx] for y, x in comp for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                          if 0 <= yy < rows and 0 <= xx < cols and L[yy, xx] != j]
                    if nb:
                        t = max(set(nb), key=nb.count)
                        for y, x in comp:
                            L[y, x] = t
    return L, C[:, 0]


def edges(L, cell_aspect):
    """Line characters along the borders of a label array: each border drawn once, on the cell before it."""
    rows, cols = L.shape
    out = [[" "] * cols for _ in range(rows)]
    P = np.pad(L, 1, mode="edge")
    for r in range(rows):
        for c in range(cols):
            if not ((c + 1 < cols and L[r, c + 1] != L[r, c]) or (r + 1 < rows and L[r + 1, c] != L[r, c])):
                continue
            M = (P[r:r + 3, c:c + 3] == L[r, c]).astype(float)
            gx, gy = M[:, 2].sum() - M[:, 0].sum(), M[2].sum() - M[0].sum()
            e = (math.degrees(math.atan2(gy * cell_aspect, gx)) + 90) % 180  # the border runs across the gradient
            out[r][c] = "─" if e < 22.5 or e >= 157.5 else "╲" if e < 67.5 else "│" if e < 112.5 else "╱"
    return out


def sms_shape_useful(rows, mask, name):
    """Is the SMS silhouette worth its SMS? -> (bool, why). Not when it is just a filled box (a sign, a pill, a pen:
    the shape inside is what matters and a silhouette loses it), and not when Muse, shown only the silhouette, can't
    tell what it is: then it costs 3-6 SMS and can mislead (a CPR scene drew as one blob). The words go either way."""
    if mask is not None and mask.any():
        ys, xs = np.nonzero(mask)
        extent = mask.sum() / ((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
        if extent >= BOXY:
            return False, "fills %.0f%% of its box: a plain shape" % (100 * extent)
    if not name or not llm._key():
        return True, "not checked"
    from . import sketch
    seen, recognised = sketch.check([r.replace(BLANK, " ") for r in rows], [name])
    if not recognised:
        return False, "Muse, shown only the silhouette, saw: %s" % (", ".join(seen) or "nothing")
    return True, "Muse recognised it as: %s" % ", ".join(seen)


def _trim(rows):
    """Rows as strings, without the empty ones above and below the drawing."""
    out = ["".join(r) for r in rows]
    busy = [i for i, r in enumerate(out) if r.strip(" " + BLANK)]
    return out[busy[0]:busy[-1] + 1] if busy else out


def find_object(img):
    """-> the object's mask cropped to it (with a margin), on the working grid, or None if there is no single
    object (nothing stands out, or everything does)."""
    m = single_object(subject_mask(img, WORK_COLS, max(8, round(WORK_COLS * img.height / img.width))))
    if not 0.01 <= m.mean() <= 0.85:
        return None
    ys, xs = np.nonzero(m)
    pad_y, pad_x = max(2, int((ys.max() - ys.min()) * 0.06)), max(2, int((xs.max() - xs.min()) * 0.06))
    return m[max(0, ys.min() - pad_y): ys.max() + pad_y + 1, max(0, xs.min() - pad_x): xs.max() + pad_x + 1]


def sam_object(img, name, category=""):
    """SAM 3.1 finds `name` -> (the object's mask on a working grid, cropped to it; how many SAM found; where the
    shape came from), or None if SAM found nothing (after retrying with the name's last word: "eight ball" ->
    "ball"). Of several matches (a flock), the biggest confident one is drawn, at the full working resolution of its
    own crop, so a small bird in a big photo is drawn as large as a close-up.

    With Meta's mask decoder installed the shape is SAM's own mask. Without it, SAM's box still says where the
    object is: the colour method runs on that box plus a margin, whose edges really are background, and an ellipse
    in the box stands in when even that fails (a black ball on black)."""
    found = []
    for prompt in dict.fromkeys(p for p in [name, name.split()[-1], category] if p):  # "fortune cookie" → "cookie" → "food"
        try:
            found = sam.segment(img, prompt)
        except Exception:
            return None
        if found:
            break
    if not found:
        return None
    confident = [f for f in found if f["score"] >= 0.8 * found[0]["score"]]
    f = max(confident, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))
    x1, y1, x2, y2 = f["box"]
    mx, my = (x2 - x1) * 0.12, (y2 - y1) * 0.12
    X1, Y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    X2, Y2 = min(img.width, int(x2 + mx) + 1), min(img.height, int(y2 + my) + 1)
    W = WORK_COLS
    H = max(8, round(W * (Y2 - Y1) / max(1, X2 - X1)))
    if f["mask"] is not None:
        part = Image.fromarray(f["mask"][Y1:Y2, X1:X2].astype(np.uint8) * 255).resize((W, H), Image.BOX)
        m, how = np.asarray(part) > 127, "SAM 3.1 mask"
    else:
        m, how = single_object(subject_mask(img.crop((X1, Y1, X2, Y2)), W, H)), "SAM 3.1 box, shape by colour"
        if not 0.05 <= m.mean() <= 0.95:  # the colour method can't see it: draw the box's ellipse
            yy, xx = np.mgrid[0:H, 0:W] + 0.5
            k = W / (X2 - X1)
            cx, cy, rx, ry = ((x1 + x2) / 2 - X1) * k, ((y1 + y2) / 2 - Y1) * k, (x2 - x1) * k / 2, (y2 - y1) * k / 2
            m, how = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1, "SAM 3.1 box"
    ys, xs = np.nonzero(m)
    if not len(xs):
        return None
    pad_y, pad_x = max(2, int((ys.max() - ys.min()) * 0.06)), max(2, int((xs.max() - xs.min()) * 0.06))
    crop = m[max(0, ys.min() - pad_y): ys.max() + pad_y + 1, max(0, xs.min() - pad_x): xs.max() + pad_x + 1]
    return crop, len(found), how


def run(data, sms_cols=24, mono_cols=58, description=None, whatsapp=True):  # the widths the maps use
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    d = description or describe(buf.getvalue())

    lines = ["Photo: " + d["caption"]] + ["- " + x for x in d["details"]]
    name = d.get("object") or ""
    found = sam_object(img, name, d.get("category", "")) if name and sam.available() else None
    if found:
        obj, total, how = found
    else:
        obj, total, how = find_object(img), 1, "colour"
    if obj is not None:  # one object, cropped so it fills the width: its contour, and a solid silhouette for SMS
        kind = "object"
        mono_rows = contour(obj, mono_cols, max(4, round(mono_cols * obj.shape[0] / obj.shape[1] / MONO_CELL)))
        rows = max(3, round(sms_cols * obj.shape[0] / obj.shape[1] / SMS_CELL))
        m = np.asarray(Image.fromarray(obj.astype(np.uint8) * 255).resize((sms_cols, rows), Image.BOX)) > 100
        sms_rows = [["#" if v else BLANK for v in row] for row in m]
    else:  # no single object: the borders between its few big regions, and 3 flat tones for SMS
        kind = "layout"
        L, _ = layout_regions(img, mono_cols, max(4, round(mono_cols * img.height / img.width / MONO_CELL)))
        mono_rows = edges(L, MONO_CELL)
        L, light = layout_regions(img, sms_cols, max(3, round(sms_cols * img.height / img.width / SMS_CELL)))
        tone = {j: SMS_TONES[i] for i, j in enumerate(np.argsort(-light))}  # lightest region blank, darkest #
        sms_rows = [[tone[v] for v in row] for row in L]
    what = (name or "the main object") if total == 1 else "one of the %d in the photo" % total
    key = ("(outline of %s, found by %s)" % (what, "SAM 3.1" if how != "colour" else "colour")
           if kind == "object" else "(outlines of the main shapes)")
    sms_lines = [r.rstrip(BLANK) or BLANK for r in _trim(sms_rows)]
    shape_ok, shape_why = sms_shape_useful(sms_lines, obj if kind == "object" else None, name or d.get("category", ""))
    sms = "\n".join(lines + ([""] + sms_lines + [key] if shape_ok else []))
    mono = "\n".join([r.rstrip() for r in _trim(mono_rows)] + [key])
    checked = None
    if whatsapp and d.get("things") and sam.available() and sam.masks_available():
        from . import sketch  # WhatsApp: the several things, drawn together and checked by Muse
        try:
            checked = sketch.draw(img, d["things"], mono_cols, fallback=d.get("category", ""))
        except Exception:  # the plain single-object drawing above is the fallback, never an error
            traceback.print_exc()
    if checked:
        names = checked["items"]
        mono = "\n".join(checked["rows"] + ["", " · ".join("%d %s" % (i + 1, n) for i, n in enumerate(names)),
                                             "(Muse, shown only this drawing, recognised: %s)" % (", ".join(checked["recognised"]) or "nothing")])
    return {"caption": d["caption"], "details": d["details"], "object": name, "outline": kind, "shape_from": how,
            "sms_shape": shape_ok, "sms_shape_why": shape_why, "sms_shape_rows": sms_lines,
            "things": d.get("things", []), "check": checked and {k: checked[k] for k in ("items", "recognised", "seen")},
            "attempts": checked and [{k: a[k] for k in ("items", "seen", "recognised")} for a in checked["attempts"]],
            "sms": sms, "text": "\n".join(lines), "mono": mono,
            "mono_bytes": len(mono.encode()), "mono_gzip": len(gzip.compress(mono.encode())),
            "photo_bytes": len(data), "width": img.width, "height": img.height, "small": imagemap.shrink(img)}
