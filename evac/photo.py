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

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from . import imagemap, llm

# Lightest to darkest; all exactly one digit wide in SF Pro 17 px (ink measured on a canvas: 0, 24, 52).
BLANK = "\u2007"  # figure space: a blank SMS row that keeps its height
SMS_TONES = " +#"  # three flat tones for a photo drawn as its main regions
SMS_CELL = 22 / 10.28   # iMessage line height / digit width
MONO_CELL = 16.9 / 7.53  # WhatsApp monospace line height / Menlo cell width at 12.5 px
PROMPT = ("Describe this photo for someone whose phone could not load it. Reply as JSON: "
          '{"caption": one sentence, "details": [up to 3 short facts a person would want to know, including any '
          'readable text], "subjects": [up to 4 {"name": 1-3 words, "box": [x0, y0, x1, y1] as fractions of the '
          "width and height}]}")


def describe(jpeg):
    """Muse Spark's caption, details and subject boxes. Falls back to an empty description."""
    if not llm.available():
        return {"caption": "A photo (no description: model not configured).", "details": [], "subjects": []}
    part = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": PROMPT}, part]}])
    except Exception as e:
        return {"caption": "A photo (description failed: %s)." % e, "details": [], "subjects": []}
    subs = []
    for s in d.get("subjects") or []:
        try:
            x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in s["box"])
            if x1 > x0 and y1 > y0:
                subs.append({"name": str(s["name"]), "box": [x0, y0, x1, y1]})
        except (KeyError, TypeError, ValueError):
            continue
    return {"caption": d.get("caption") or "", "details": [str(x) for x in d.get("details") or []][:3], "subjects": subs[:4]}


# ---------- outlines: the bare minimum of a photo ----------
# A tone sketch shades every pixel, so ripples, mist and stripes crowd out the thing the photo is of. Instead:
# find the subject (whatever differs from the colours along the photo's border), clean it to its main pieces,
# zoom in if it is small, and draw only its edge. Photos with no clear subject (a landscape) get the borders of
# their few biggest colour regions instead: horizon, water line, cliff. SAM 3.1 would give a real subject mask;
# this is the no-model stand-in.

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
    """Blurred YCbCr at twice the cell resolution, lightness weighted down so colour separates subjects."""
    sm = img.filter(ImageFilter.GaussianBlur(max(img.size) / 200)).convert("YCbCr")
    return np.asarray(sm.resize((cols * 2, rows * 2), Image.BOX), float) * np.array([0.7, 1.2, 1.2])


def _components(m):
    """Connected True regions of a bool array, as lists of cells, biggest first."""
    rows, cols = m.shape
    seen, out = np.zeros_like(m, bool), []
    for r in range(rows):
        for c in range(cols):
            if m[r, c] and not seen[r, c]:
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
    """bool (rows, cols): the main subject, its biggest pieces only, holes filled."""
    A = _colour(img, cols, rows)
    C, _ = _kmeans(np.concatenate([A[0], A[-1], A[:, 0], A[:, -1]]), 4)  # the background's colours
    d = np.sqrt(((A[:, :, None] - C[None, None]) ** 2).sum(3)).min(2)
    m = Image.fromarray(((d > max(_otsu(d.ravel()), 18)) * 255).astype(np.uint8)).resize((cols, rows), Image.BOX)
    m = m.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    m = np.asarray(m) > 110  # median: drop specks; max then min: close gaps such as a zebra's stripes
    comps = _components(m)
    keep = np.zeros_like(m)
    for comp in comps:
        if len(comp) >= max(3, 0.3 * len(comps[0])):
            for y, x in comp:
                keep[y, x] = True
    for comp in _components(~keep):  # fill holes: background pieces that don't reach the edge
        if not any(y in (0, rows - 1) or x in (0, cols - 1) for y, x in comp):
            for y, x in comp:
                keep[y, x] = True
    return keep


def layout_regions(img, cols, rows, k=3):
    """int (rows, cols): the photo split into k colour regions, small islands merged into their surroundings."""
    A = _colour(img, cols, rows)
    small = A.reshape(rows, 2, cols, 2, 3).mean((1, 3))
    C, a = _kmeans(small.reshape(-1, 3), k)
    L = a.reshape(rows, cols)
    for _ in range(3):
        for j in range(k):
            for comp in _components(L == j):
                if len(comp) < cols * rows // 25:
                    y, x = comp[0]
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


def _crop_to(img, m):
    """Zoom on a small subject: the photo cropped to the mask's box with a margin, or None."""
    ys, xs = np.nonzero(m)
    if not len(xs):
        return None
    rows, cols = m.shape
    x0, x1, y0, y1 = xs.min() / cols, (xs.max() + 1) / cols, ys.min() / rows, (ys.max() + 1) / rows
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    w, h = max(w * 1.8, h * 1.8 * img.height / img.width), max(h * 1.8, w * 1.8 * img.width / img.height)
    w, h = max(w, 0.25), max(h, 0.25)  # at least a quarter of the photo, in the photo's own shape
    x0, x1 = max(0, cx - w / 2), min(1, cx + w / 2)
    y0, y1 = max(0, cy - h / 2), min(1, cy + h / 2)
    if (x1 - x0) * (y1 - y0) > 0.6:
        return None
    return img.crop((int(x0 * img.width), int(y0 * img.height), int(x1 * img.width), int(y1 * img.height)))


def _trim(rows):
    """Rows as strings, without the empty ones above and below the drawing."""
    out = ["".join(r) for r in rows]
    busy = [i for i, r in enumerate(out) if r.strip()]
    return out[busy[0]:busy[-1] + 1] if busy else out


def outline(img, cols, cell_aspect):
    """-> (kind, picture, rows of characters). kind: "subject", "subject, zoomed in" or "layout"."""
    rows = lambda im: max(4, round(cols * im.height / im.width / cell_aspect))
    m = subject_mask(img, cols, rows(img))
    kind = "subject"
    if 0 < m.mean() < 0.06:
        crop = _crop_to(img, m)
        if crop is not None:
            img, kind = crop, "subject, zoomed in"
            m = subject_mask(img, cols, rows(img))
    if 0.04 <= m.mean() <= 0.7 and len(_components(m)) <= 4:
        return kind, img, edges(m.astype(int), cell_aspect), m
    return "layout", img, None, None


def run(data, sms_cols=18, mono_cols=58, description=None):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    small = img.copy()
    small.thumbnail((1024, 1024))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    d = description or describe(buf.getvalue())

    lines = ["Photo: " + d["caption"]] + ["- " + x for x in d["details"]]
    kind, view, mono_rows, mask = outline(img, mono_cols, MONO_CELL)
    if kind == "layout":  # no clear subject: the borders between its few big regions
        L, _ = layout_regions(view, mono_cols, max(4, round(mono_cols * view.height / view.width / MONO_CELL)))
        mono_rows = edges(L, MONO_CELL)
        L, light = layout_regions(view, sms_cols, max(3, round(sms_cols * view.height / view.width / SMS_CELL)))
        tone = {j: SMS_TONES[i] for i, j in enumerate(np.argsort(-light))}  # lightest region blank, darkest #
        sms_rows = [[tone[v] for v in row] for row in L]
    else:  # the subject as a solid shape: at 18 columns a filled silhouette reads better than its edge
        # Shrunk from the WhatsApp mask: re-detecting at 18 columns can lose a small subject entirely.
        rows = max(3, round(sms_cols * view.height / view.width / SMS_CELL))
        m = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize((sms_cols, rows), Image.BOX)) > 60
        sms_rows = [["#" if v else " " for v in row] for row in m]
    what = {"subject": "outline of the main subject", "subject, zoomed in": "outline of the main subject, zoomed in",
            "layout": "outlines of the main shapes"}[kind]
    names = [x["name"] for x in d["subjects"]][:3]
    key = "(%s%s)" % (what, ": " + ", ".join(names) if names and kind != "layout" else "")
    sms = "\n".join(lines + [""] + [r.rstrip() or BLANK for r in _trim(sms_rows)] + [key])
    mono = "\n".join([r.rstrip() for r in _trim(mono_rows)] + [key])
    return {"caption": d["caption"], "details": d["details"], "subjects": d["subjects"], "outline": kind,
            "sms": sms, "text": "\n".join(lines), "mono": mono,
            "mono_bytes": len(mono.encode()), "mono_gzip": len(gzip.compress(mono.encode())),
            "photo_bytes": len(data), "width": img.width, "height": img.height, "small": imagemap.shrink(img)}
