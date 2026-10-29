"""Server-chosen zooms (evac/zooms.py) against the old way (every 2x2 and 4x4 tile, best first) and against pictures.

    .venv/bin/python evals/focus_zooms.py --only Dog,Mt_Fuji

Per photo, everything at the same number of bytes as the zooms plan sent (base drawing + its zooms, UTF-8):
  zooms    the plan: base drawing + the zooms it kept, decoded to grey (no model)
  tiles    the old way: the faithful base drawing + the 2x2/4x4 tiles in greedy order, as many as fit the bytes
  picture  the best greyscale JPEG/WebP of the whole photo that fits the bytes
  crop     a subject photo only: the best picture of just the subject's box that fits the bytes
Scored with SSIM against the greyscale photo at 232 px wide: over the whole photo, and over the subject's box (where a
subject plan spends its bytes). Output: out/eval/<photo>/zooms/ (texts, sheet.jpg) and printed numbers.
"""
import argparse
import io
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compress_compare as C  # noqa: E402
import photo_eval as E  # noqa: E402
from evac import tonal, zooms  # noqa: E402


def region(a, frac):
    H, W = a.shape
    x0, y0, x1, y1 = frac
    return a[int(y0 * H):max(int(y0 * H) + 8, int(y1 * H)), int(x0 * W):max(int(x0 * W) + 8, int(x1 * W))]


def tiles_at(full, base, budget):
    """The old way, cut off at `budget` bytes: the faithful base drawing (same segmentation) + tiles in greedy order."""
    L, gray, named, main, root = base["layers"]
    grey, sent = E.message(full, tonal.render(L, gray, named, main, E.COLS, root, emphasis=False)[0])
    canvas = E.base_canvas(grey, full.size)
    ref = zooms.lightness(full, zooms.CANVAS_W, canvas.shape[0])
    cands = []
    for n in (2, 4):
        for box in E.tiles(full, n):
            crop = full.crop(box)
            z = tonal.draw(crop, segment=False, check=False, emphasis=False)
            g, b = E.message(crop, z["plain"])
            cands.append({"grey": g, "box": box, "bytes": b})
    while cands:
        err = ((canvas - ref) ** 2).sum()
        best = None
        for i, c in enumerate(cands):
            if sent + c["bytes"] > budget:
                continue
            t = canvas.copy()
            zooms.paste(t, c["grey"], c["box"], full.size)
            g = (err - ((t - ref) ** 2).sum()) / c["bytes"]
            if g > 0 and (best is None or g > best[0]):
                best = (g, i, t)
        if best is None:
            break
        _, i, canvas = best
        sent += cands.pop(i)["bytes"]
    return canvas, sent


def picture_at(G, budget, box=None):
    """Best greyscale picture under `budget` bytes, decoded, at the photo's size. box: only that part of the photo
    is sent; the rest is the flat mean grey (the receiver has nothing else for it)."""
    src = G.crop(box) if box else G
    pts = C.ladder(src)
    p = C.best_under(pts, budget)
    if not p:
        return None, 0
    _, dec = C.encode(src, p["w"], p["q"], p["fmt"])
    if not box:
        return dec, p["bytes"]
    out = Image.new("L", G.size, int(np.asarray(G).mean()))
    out.paste(dec.resize((box[2] - box[0], box[3] - box[1])), box[:2])
    return out, p["bytes"]


def as_img(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def sheet(panels, path, w=300):
    ims = []
    for label, im in panels:
        im = im.convert("RGB")
        im = im.resize((w, round(w * im.height / im.width)))
        out = Image.new("RGB", (w, im.height + 22), "white")
        out.paste(im, (0, 22))
        ImageDraw.Draw(out).text((4, 4), label, fill="black")
        ims.append(out)
    H = max(i.height for i in ims)
    s = Image.new("RGB", (sum(i.width for i in ims) + 8 * (len(ims) - 1), H), "white")
    x = 0
    for i in ims:
        s.paste(i, (x, 0))
        x += i.width + 8
    s.save(path, quality=88)


def one(key, path, keep=zooms.KEEP):
    d = os.path.join(E.OUT, key, "zooms")
    os.makedirs(d, exist_ok=True)
    full = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full.thumbnail((2048, 2048))
    work = full.copy()
    work.thumbnail((1600, 1600))
    G = full.convert("L")
    base = tonal.draw(work, check=False)
    plan = zooms.plan(full, base, keep=keep)
    budget = plan["bytes"]
    ref = plan["ref"]
    mask = zooms.subject_mask(base, full.size)
    if mask is not None:
        ys, xs = np.nonzero(mask)
        sbox = (xs.min() / full.width, ys.min() / full.height, (xs.max() + 1) / full.width, (ys.max() + 1) / full.height)
    else:
        sbox = (0, 0, 1, 1)
    pbox = tuple(int(v) for v in (sbox[0] * full.width, sbox[1] * full.height, sbox[2] * full.width, sbox[3] * full.height))

    tiles, tbytes = tiles_at(full, base, budget)
    pic, pbytes = picture_at(G, budget)
    crop, cbytes = picture_at(G, budget, pbox) if plan["mode"] == "subject" else (None, 0)
    arr = lambda im: zooms.lightness(im, zooms.CANVAS_W, ref.shape[0])
    rows = {"zooms": (plan["canvas"], budget), "tiles": (tiles, tbytes)}
    if pic is not None:
        rows["picture"] = (arr(pic), pbytes)
    if crop is not None:
        rows["crop"] = (arr(crop), cbytes)
    res = {"name": key, "mode": plan["mode"], "parts": base["parts"], "base_bytes": len(plan["base_text"].encode()),
           "bytes": budget, "chars": plan["chars"], "sms": plan["sms"], "error_left": plan["error"],
           "zooms": [{k: z[k] for k in ("pct", "cols", "rows", "bytes", "chars", "removed")} for z in plan["zooms"]],
           "scores": {k: {"bytes": b, "ssim": E.ssim(c, ref), "ssim_subject": E.ssim(region(c, sbox), region(ref, sbox))}
                      for k, (c, b) in rows.items()}}
    base_only = np.ones_like(ref)
    zooms.paste(base_only, zooms.decode(plan["base_rows"], zooms.legend(plan["base_rows"], zooms.lightness(full, zooms.COLS, len(plan["base_rows"])))),
                (0, 0) + full.size, full.size)
    res["scores"]["base"] = {"bytes": res["base_bytes"], "ssim": E.ssim(base_only, ref),
                             "ssim_subject": E.ssim(region(base_only, sbox), region(ref, sbox))}
    open(os.path.join(d, "message.txt"), "w").write("\n\n".join([plan["base_text"]] + [z["text"] for z in plan["zooms"]]))
    json.dump(res, open(os.path.join(d, "result.json"), "w"), indent=1, default=float)

    boxes = G.convert("RGB")
    dr = ImageDraw.Draw(boxes)
    for i, z in enumerate(plan["zooms"], 1):
        x0, y0, x1, y1 = (z["pct"][0] * full.width / 100, z["pct"][1] * full.height / 100,
                          z["pct"][2] * full.width / 100, z["pct"][3] * full.height / 100)
        dr.rectangle((x0, y0, x1, y1), outline=(230, 40, 40), width=max(3, full.width // 300))
        dr.text((x0 + 8, y0 + 4), str(i), fill=(230, 40, 40))
    panels = [("photo (grey) + zoom boxes", boxes), ("base only, %d B" % res["base_bytes"], as_img(base_only)),
              ("zooms, %d B" % budget, as_img(plan["canvas"])), ("old tiles, %d B" % tbytes, as_img(tiles))]
    if pic is not None:
        panels.append(("picture, %d B" % pbytes, pic))
    if crop is not None:
        panels.append(("subject-crop picture, %d B" % cbytes, crop))
    sheet(panels, os.path.join(d, "sheet.jpg"))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="Dog,Mt_Fuji")
    ap.add_argument("--keep", type=float, default=zooms.KEEP, help="share of the base's error a zoom must remove")
    a = ap.parse_args()
    want = set(a.only.split(","))
    for key, name, path in E.dataset():
        if key in want or name in want:
            r = one(key, path, a.keep)
            print(json.dumps({k: r[k] for k in ("name", "mode", "parts", "base_bytes", "bytes", "chars", "sms")}, ensure_ascii=False))
            for z in r["zooms"]:
                print("  zoom", z)
            for k, s in r["scores"].items():
                print("  %-8s %6d B  ssim %.3f  subject %.3f" % (k, s["bytes"], s["ssim"], s["ssim_subject"]))


if __name__ == "__main__":
    main()
