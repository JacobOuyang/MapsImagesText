"""How good are the photo drawings, and how much of a photo can be recomposed from them?

    .venv/bin/python evals/photo_eval.py                 # every simulator sample photo -> out/eval/
    .venv/bin/python evals/photo_eval.py --only Dog,Teapot
    .venv/bin/python evals/photo_eval.py --report        # rebuild the report from saved results

Part 1, the drawing, measured against the photo (cell by cell, at the drawing's own 58-column grid):
  tone      every glyph's ink is measured in Menlo, so a drawing becomes a grey image; Pearson r and SSIM against the
            photo's brightness in the same cells
  edges     cells where the photo's brightness changes most vs cells where the drawing's ink changes or a line is
            drawn; precision, recall, F1 with one cell of tolerance
  subject   IoU of the drawing's subject cells with SAM's subject mask on the same grid; how much darker or lighter the
            subject is than the background around it (contrast); how much ink the background carries
  meaning   Muse sees only the rendered drawing (no caption, no key) and names the subject and the scene; a judge
            compares that with Muse's description of the photo: subject match yes/no, scene 0-10
  size      UTF-8 bytes and gzip bytes

Part 2, recomposition: only the text drawings (and zooms) reach the receiver; rebuild the photo from them.
  D0 (no model)  each drawing turned back into grey (glyph ink) and pasted where it belongs; compared with the photo
                 at 232 px wide (SSIM, MAE). Zoom strategies: the numbered parts (what ZOOM n sends), a full 2x2 and
                 4x4 tiling (every zoom you could ask for), and greedy: the server, which has the photo, sends the
                 zoom that removes the most error per byte first. Ceilings: the photo itself averaged into a 58-,
                 116- or 232-column grid, with continuous tones and with only the 5 shading glyphs.
  D1 (Muse Image) /images/generations from the caption alone, /images/edits from the drawing alone, the drawing +
                 its key, and the D0 rebuild from every zoom + the key. Scored on layout (correlation of 16-column
                 grey thumbnails, vs the chance level between unrelated photos), the subject's box (SAM on both,
                 IoU) and a Muse judge (0-10: subject, layout, overall).
"""
import argparse
import base64
import gzip
import io
import json
import math
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
from evac import llm, museimage, photo, sam, tonal  # noqa: E402
from evac.photo import MONO_CELL  # noqa: E402
from evac.zooms import (FONT_PATH, SHADE_CHARS, decode, ink, ink_map, legend, legend_bytes,  # noqa: E402,F401
                        lightness, paste, ssim)

OUT = os.path.join(ROOT, "out", "eval")
LINES = set("─│╭╮╯╰╳━┃┏┓┗┛╋")
ANALYSIS_W = 232  # recomposition is scored at this width: 4 x 58, the detail a 4x4 tiling of zooms can reach
COLS = 58


# ---------- image metrics ----------

def pearson(a, b):
    a, b = a.ravel() - a.mean(), b.ravel() - b.mean()
    d = math.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else 0.0


def dilate(m):
    p = np.pad(m, 1)
    return np.logical_or.reduce([p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])


def edge_scores(rows, light):
    """F1 of the drawing's edges against the photo's, one cell of tolerance."""
    inkm = ink_map(rows, light.shape[1])
    gy, gx = np.gradient(light)
    g = np.hypot(gx, gy)
    photo_e = g >= np.quantile(g, 0.85)
    d = np.zeros_like(photo_e)
    d[:, :-1] |= np.abs(np.diff(inkm, axis=1)) > 0.12
    d[:-1, :] |= np.abs(np.diff(inkm, axis=0)) > 0.12
    for r, line in enumerate(rows):
        for c, ch in enumerate(line[:light.shape[1]]):
            if ch in LINES:
                d[r, c] = True
    if not d.any() or not photo_e.any():
        return 0.0, 0.0, 0.0
    precision = (d & dilate(photo_e)).sum() / d.sum()
    recall = (photo_e & dilate(d)).sum() / photo_e.sum()
    return float(precision), float(recall), float(2 * precision * recall / (precision + recall + 1e-9))


def subject_scores(rows, subject_cells, subject_truth):
    """IoU of the drawing's subject cells with SAM's subject on the grid; subject vs nearby background ink."""
    inkm = ink_map(rows, subject_cells.shape[1])[:subject_cells.shape[0]]
    iou = float((subject_cells & subject_truth).sum() / max(1, (subject_cells | subject_truth).sum()))
    ring = subject_cells.copy()
    for _ in range(3):
        ring = dilate(ring)
    ring &= ~subject_cells
    s_ink = float(inkm[subject_cells].mean()) if subject_cells.any() else 0.0
    r_ink = float(inkm[ring].mean()) if ring.any() else 0.0
    bg_ink = float(inkm[~subject_cells].mean()) if (~subject_cells).any() else 0.0
    return {"subject_iou": iou, "contrast": abs(s_ink - r_ink), "subject_ink": s_ink, "background_ink": bg_ink}


def size(rows):
    t = "\n".join(rows).encode()
    return len(t), len(gzip.compress(t))


# ---------- Muse as a blind viewer and as a judge ----------

def drawing_png(rows, font_px=14):
    font = ImageFont.truetype(FONT_PATH, font_px)
    cw = font.getlength("M")
    lh = int(round(cw * MONO_CELL))
    im = Image.new("RGB", (int(max(COLS, *(len(r) for r in rows)) * cw) + 20, len(rows) * lh + 20), "white")
    d = ImageDraw.Draw(im)
    for i, r in enumerate(rows):
        d.text((10, 10 + i * lh), r, font=font, fill="black")
    return im


def data_url(im, fmt="PNG"):
    buf = io.BytesIO()
    im.save(buf, fmt)
    return "data:image/%s;base64,%s" % (fmt.lower(), base64.b64encode(buf.getvalue()).decode())


BLIND = ("This is a drawing made of text characters from a photograph: darker characters stand for darker areas, and a "
         "heavy outline (if any) marks the main subject. Looking only at the drawing, what is the photo's main subject, "
         'and what does the whole photo show? Reply as JSON: {"subject": a few words, "scene": one sentence}')
JUDGE = ("A photo was described as: %r (main subject: %r). Someone who saw only a rough text drawing of it guessed: "
         "subject %r, scene %r. Is the guessed subject the same kind of thing? A synonym, a broader or narrower name counts "
         "('bird' for 'eagle', 'dog' for 'golden retriever'); ignore colour, pattern and other adjectives. "
         "How well does the guessed scene match the description (0 = nothing in common, 10 = same subject, setting and "
         'layout)? Reply as JSON: {"subject_match": true or false, "scene_score": 0-10}')
COMPARE = ("Image A is an original photo. Image B is an attempt to recreate it by someone who never saw it, working only "
           "from a low-detail text drawing. Score B against A from 0 to 10 on: the same main subject, the same layout "
           '(where things are, light and dark areas), and overall. Reply as JSON: {"subject": 0-10, "layout": 0-10, "overall": 0-10}')


def blind_read(rows, caption, main):
    try:
        seen = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": BLIND},
                                                           {"type": "image_url", "image_url": {"url": data_url(drawing_png(rows))}}]}])
        verdict = llm.chat_json([{"role": "user", "content": JUDGE % (caption, main, seen.get("subject"), seen.get("scene"))}])
        return {"subject": str(seen.get("subject")), "scene": str(seen.get("scene")),
                "subject_match": bool(verdict.get("subject_match")), "scene_score": float(verdict.get("scene_score") or 0)}
    except Exception as ex:
        return {"error": "%s: %s" % (type(ex).__name__, ex)}


# ---------- recomposition ----------
# Every drawing sets its own tone scale (its lightest half is blank), so the same ░ is a different grey in different
# zooms. A receiver can only rebuild consistent greys if each drawing says what its glyphs stand for: a one-line legend
# such as "tones ·82 ░64 ▒41 ▓20" (percent lightness), ~40 bytes, counted in the bytes sent. Outline glyphs carry no
# tone: those cells are filled from their neighbours. legend, decode and paste live in evac/zooms.py (the server uses
# them to choose zooms).

def base_canvas(grey, img_size):
    H = round(ANALYSIS_W * img_size[1] / img_size[0])
    c = np.ones((H, ANALYSIS_W))
    paste(c, grey, (0, 0, img_size[0], img_size[1]), img_size)
    return c


def message(img, rows):
    """A drawing as the receiver gets it for recomposition: decoded greys, and its bytes with the legend."""
    light = lightness(img, COLS, max(1, len(rows)))  # drawings are always COLS wide; trailing blanks are trimmed
    leg = legend(rows, light)
    return decode(rows, leg, light.shape[1]), size(rows)[0] + legend_bytes(leg)


def ceiling(img, cols, glyphs=False):
    """The best a cols-wide grid of cells can do: the photo's own lightness per cell (optionally snapped to the 5
    shading glyphs' ink), blown back up to the analysis size."""
    rows = max(4, round(cols * img.height / img.width / MONO_CELL))
    g = lightness(img, cols, rows)
    if glyphs:
        levels = np.array([1 - ink(ch) if ch != " " else 1.0 for ch in " ·░▒▓"])
        g = levels[np.abs(g[..., None] - levels[None, None]).argmin(-1)]
    H = round(ANALYSIS_W * img.height / img.width)
    return np.asarray(Image.fromarray((g * 255).astype(np.uint8)).resize((ANALYSIS_W, H), Image.BILINEAR), float) / 255


def tiles(img, n):
    w, h = img.width / n, img.height / n
    return [(int(i * w), int(j * h), int((i + 1) * w), int((j + 1) * h)) for j in range(n) for i in range(n)]


def greedy(base, candidates, ref, img_size):
    """Order zooms so that each next one removes the most squared error per byte. -> [(bytes, ssim, mae, label)]."""
    canvas = base.copy()
    sent = 0
    curve = [(0, ssim(canvas, ref), float(np.abs(canvas - ref).mean()), "base")]
    left = list(candidates)
    while left:
        err = ((canvas - ref) ** 2).sum()
        best, gain = None, -1
        for cand in left:
            trial = canvas.copy()
            paste(trial, cand["grey"], cand["box"], img_size)
            g = (err - ((trial - ref) ** 2).sum()) / max(1, cand["bytes"])
            if g > gain:
                best, gain, best_canvas = cand, g, trial
        if gain <= 0:
            break
        canvas = best_canvas
        sent += best["bytes"]
        left.remove(best)
        curve.append((sent, ssim(canvas, ref), float(np.abs(canvas - ref).mean()), best["label"]))
    return curve, canvas


# ---------- Muse Image ----------

def generate(prompt, image=None):
    """Muse Image: text-to-image, or image-conditioned (edits) when `image` is given. -> PIL image."""
    return museimage.generate(prompt, image)


def layout_corr(a, b):
    """Correlation of 16-column grey thumbnails at the original's shape: where light and dark are."""
    cols, rows = 16, max(4, round(16 * a.height / a.width))
    return pearson(lightness(a, cols, rows), lightness(b.resize(a.size), cols, rows))


def box_iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def subject_box(img, name):
    """SAM's box for `name` in `img`, as fractions of the image (best match), or None."""
    try:
        found = sam.segment(img, name)
    except Exception:
        return None
    if not found:
        return None
    x0, y0, x1, y1 = found[0]["box"]
    return (x0 / img.width, y0 / img.height, x1 / img.width, y1 / img.height)


def compare(original, made, main, truth_box):
    out = {"layout_corr": layout_corr(original, made)}
    mb = subject_box(made.resize(original.size), main) if main else None
    out["subject_iou"] = box_iou(truth_box, mb) if (truth_box and mb) else 0.0
    out["subject_found"] = mb is not None
    try:
        s = llm.chat_json([{"role": "user", "content": [
            {"type": "text", "text": COMPARE + " Image A comes first, then image B."},
            {"type": "image_url", "image_url": {"url": data_url(_thumb(original), "JPEG")}},
            {"type": "image_url", "image_url": {"url": data_url(_thumb(made), "JPEG")}}]}])
        out.update({"judge_" + k: float(s.get(k) or 0) for k in ("subject", "layout", "overall")})
    except Exception as ex:
        out["judge_error"] = str(ex)
    return out


def _thumb(im, side=768):
    im = im.copy()
    im.thumbnail((side, side))
    return im


# ---------- one photo ----------

def evaluate(name, path, generative=True):
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    res = {"name": name}
    full = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full.thumbnail((2048, 2048))
    work = full.copy()
    work.thumbnail((1600, 1600))
    buf = io.BytesIO()
    full.save(buf, "JPEG", quality=90)
    data = buf.getvalue()
    res["photo_bytes"] = len(data)
    H = round(ANALYSIS_W * full.height / full.width)
    ref = np.asarray(full.convert("L").resize((ANALYSIS_W, H), Image.BOX), float) / 255

    # Low level: the outline (what photo.run sends), and the description of the photo every judge compares with.
    t = time.time()
    low = photo.run(data)
    res["low_seconds"] = round(time.time() - t, 1)
    caption, main_word = low["caption"], low.get("object") or ""
    res.update(caption=caption, object=main_word)
    low_rows = low["mono"].split("\n")
    low_rows = low_rows[:-3] if low.get("check") else low_rows[:-1]
    low_rows = ["".join(" " if ch.isdigit() else ch for ch in r) for r in low_rows]
    while low_rows and not low_rows[-1].strip():
        low_rows.pop()
    open(os.path.join(d, "low.txt"), "w").write("\n".join(low_rows))

    # High level: emphasised (what is sent) and faithful (same segmentation, one tone scale).
    t = time.time()
    hi = tonal.draw(work, check=False)
    res["high_seconds"] = round(time.time() - t, 1)
    L, gray, named, main, root = hi["layers"]
    faithful, _, _, faithful_subject = tonal.render(L, gray, named, main, COLS, root, emphasis=False)
    rows = len(hi["plain"])
    light = lightness(work, COLS, rows)
    labels = np.unique(L)
    subj = np.isin(L, [l for l in labels if main is not None and root.get(l) == main])
    truth = np.asarray(Image.fromarray(subj.astype(np.uint8) * 255).resize((COLS, rows), Image.BOX)) / 255 >= 0.5
    res.update(parts=hi["parts"], main=hi["main"], regions=hi["regions"], key=hi["key"])
    for tag, drawing, cells in (("high", hi["plain"], hi["subject_cells"]), ("faithful", faithful, faithful_subject)):
        inkm = ink_map(drawing, COLS)[:rows]
        m = {"tone_r": pearson(1 - inkm, light), "tone_ssim": ssim(1 - inkm, light)}
        m["edge_precision"], m["edge_recall"], m["edge_f1"] = edge_scores(drawing, light)
        m.update(subject_scores(drawing, cells, truth))
        m["bytes"], m["gzip"] = size(drawing)
        m.update({"read_" + k: v for k, v in blind_read(drawing, caption, main_word or hi["main"]).items()})
        res[tag] = m
        open(os.path.join(d, tag + ".txt"), "w").write("\n".join(drawing))
    lr = max(1, len(low_rows))
    m = {}
    m["bytes"], m["gzip"] = size(low_rows)
    m.update({"read_" + k: v for k, v in blind_read(low_rows, caption, main_word).items()})
    res["low"] = m

    # Recomposition D0: drawings -> grey, zooms pasted in.
    rec = {"ceiling": {}, "flat": {"ssim": ssim(np.full_like(ref, ref.mean()), ref), "mae": float(np.abs(ref - ref.mean()).mean())}}
    for cols in (58, 116, 232):
        for glyphs in (False, True):
            c = ceiling(full, cols, glyphs)
            rec["ceiling"]["%d%s" % (cols, "_glyphs" if glyphs else "")] = {"ssim": ssim(c, ref), "mae": float(np.abs(c - ref).mean())}
    canvases = {}
    for tag, drawing in (("high", hi["plain"]), ("faithful", faithful)):
        grey, sent = message(work, drawing)
        canvases[tag] = base_canvas(grey, full.size)
        rec[tag] = {"ssim": ssim(canvases[tag], ref), "mae": float(np.abs(canvases[tag] - ref).mean()), "bytes": sent}
    ink_canvas = base_canvas(1 - ink_map(hi["plain"], COLS), full.size)  # the emphasised drawing as a viewer sees it, no legend
    rec["high_as_seen"] = {"ssim": ssim(ink_canvas, ref), "mae": float(np.abs(ink_canvas - ref).mean())}
    cands = []
    k = full.width / work.width
    for n, box in sorted(hi["boxes"].items())[:3]:  # the numbered parts: what ZOOM n sends (one tone scale + legend)
        try:
            fbox = tuple(int(v * k) for v in box)
            z, crop = tonal.zoom(full, fbox, check=False, emphasis=False)
            grey, sent = message(crop, z["plain"])
            cands.append({"label": "part %d" % n, "grey": grey, "box": tonal.zoom_box(full, fbox), "bytes": sent, "kind": "part"})
            open(os.path.join(d, "zoom_part_%d.txt" % n), "w").write("\n".join(z["plain"]))
        except Exception:
            traceback.print_exc()
    for n in (2, 4):  # every zoom you could ask for: a full tiling (brightness only, no model calls)
        for i, box in enumerate(tiles(full, n)):
            crop = full.crop(box)
            z = tonal.draw(crop, segment=False, check=False, emphasis=False)
            grey, sent = message(crop, z["plain"])
            cands.append({"label": "tile %dx%d #%d" % (n, n, i), "grey": grey, "box": box, "bytes": sent, "kind": "tile%d" % n})
    for tag, kinds in (("parts", {"part"}), ("tiles2", {"tile2"}), ("tiles4", {"tile2", "tile4"})):
        c = canvases["faithful"].copy()
        sent = rec["faithful"]["bytes"]
        for cand in cands:
            if cand["kind"] in kinds:
                paste(c, cand["grey"], cand["box"], full.size)
                sent += cand["bytes"]
        rec[tag] = {"ssim": ssim(c, ref), "mae": float(np.abs(c - ref).mean()), "bytes": sent}
        canvases[tag] = c
    curve, canvases["greedy"] = greedy(canvases["faithful"], cands, ref, full.size)
    rec["greedy"] = [{"bytes": b + rec["faithful"]["bytes"], "ssim": s, "mae": e, "zoom": lab} for b, s, e, lab in curve]
    res["recompose"] = rec
    for tag, c in canvases.items():
        Image.fromarray((c * 255).astype(np.uint8)).save(os.path.join(d, "d0_%s.png" % tag))

    # Recomposition D1: Muse Image.
    if generative:
        truth_box = None
        if subj.any():
            ys, xs = np.nonzero(subj)
            truth_box = (xs.min() / subj.shape[1], ys.min() / subj.shape[0], (xs.max() + 1) / subj.shape[1], (ys.max() + 1) / subj.shape[0])
        main_name = hi["main"] or main_word
        names = ", ".join(v for _, v in sorted(hi["key"].items()))
        ask = ("The input is a drawing made of text characters from a photograph: darker characters are darker areas and a "
               "heavy outline marks the main subject. Recreate the original photograph as a realistic photo, keeping the "
               "same composition, positions and light and dark areas.")
        jobs = {
            "caption_only": ("A photograph. " + caption, None),
            "drawing": (ask, drawing_png(hi["plain"])),
            "drawing_key": (ask + " It shows: %s." % names, drawing_png(hi["plain"])),
            "zooms_key": ("The input is a rough greyscale reconstruction of a photograph. Recreate the original photograph as "
                          "a realistic, detailed photo with the same composition. It shows: %s." % names,
                          Image.fromarray((canvases["greedy"] * 255).astype(np.uint8)).resize((full.width, full.height))),
        }
        res["generate"] = {}
        for tag, (prompt, image) in jobs.items():
            try:
                made = generate(prompt, image)
                made_fit = made.resize(full.size)
                made_fit.save(os.path.join(d, "gen_%s.jpg" % tag), quality=85)
                res["generate"][tag] = compare(full, made_fit, main_name, truth_box)
            except Exception as ex:
                res["generate"][tag] = {"error": "%s: %s" % (type(ex).__name__, ex)}
    full_small = full.copy()
    full_small.thumbnail((640, 640))
    full_small.save(os.path.join(d, "photo.jpg"), quality=85)
    json.dump(res, open(os.path.join(d, "result.json"), "w"), indent=1, default=float)
    return res


def dataset():
    from server import sample_objects, sample_path, sample_photos
    names = list(sample_objects()) + list(sample_photos())
    return [(n.replace(" ", "_").replace("&", "and").replace(".", ""), n, sample_path(n)) for n in names]


def chance_layout(names):
    """Layout correlation between unrelated photos: the floor any recomposition must beat."""
    ims = []
    for n in names:
        p = os.path.join(OUT, n, "photo.jpg")
        if os.path.exists(p):
            ims.append(Image.open(p).convert("RGB"))
    vals = [layout_corr(a, b) for i, a in enumerate(ims) for j, b in enumerate(ims) if i < j]
    return float(np.mean(vals)) if vals else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated sample names")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--no-generate", action="store_true")
    ap.add_argument("--report", action="store_true", help="only rebuild the report")
    a = ap.parse_args()
    items = dataset()
    if a.only:
        want = set(a.only.split(","))
        items = [it for it in items if it[1] in want or it[0] in want]
    if not a.report:
        os.makedirs(OUT, exist_ok=True)

        def one(it):
            key, name, path = it
            if os.path.exists(os.path.join(OUT, key, "result.json")):
                return key, "cached"
            t = time.time()
            try:
                evaluate(key, path, generative=not a.no_generate)
                return key, "ok %.0fs" % (time.time() - t)
            except Exception as ex:
                traceback.print_exc()
                return key, "FAILED %s: %s" % (type(ex).__name__, ex)

        with ThreadPoolExecutor(a.workers) as pool:
            for key, status in pool.map(one, items):
                print("%-28s %s" % (key, status), flush=True)
    from report import build
    from server import GENERATED, OBJECTS
    generated = {f[:-4] for f in os.listdir(GENERATED) if f.endswith(".jpg")}
    objects = {o.split("/")[1] for o in OBJECTS}
    kind = {k: "generated object" if n in generated else "photo of an object" if n in objects else "scene" for k, n, _ in items}
    build(OUT, [k for k, _, _ in items], chance_layout([k for k, _, _ in items]), kind)


if __name__ == "__main__":
    main()
