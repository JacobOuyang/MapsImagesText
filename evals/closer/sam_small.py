"""Can SAM 3.1 find a small thing in the 128 px grey picture the server receives, if the picture is enlarged first?

    .venv/bin/python evals/closer/sam_small.py            # ~3 minutes; SAM and Muse Image answers are cached

For each photo and noun phrase, the ground truth is SAM on the full-size colour original (which only the phone has).
Each method turns the 128 px grey WebP (exactly what arrives by text) into what SAM is given:

    raw        the 128 px picture as it is
    x4         enlarged to 512 px (Lanczos)
    x8sharp    enlarged to 1024 px, sharpened, contrast stretched
    tiles      3 x 3 overlapping tiles (half the picture each way), each enlarged to 512 px; the boxes are mapped
               back and the most confident one kept: the thing fills far more of each tile than of the picture
    restored   Muse Image asked to restore the picture at full size without moving or adding anything (generative:
               it may invent detail, so only usable to aim a closer look, never to judge what something is)

A method finds the thing when its best box has its centre inside the ground truth's box. Results:
out/eval/closer/sam_small.json and evals/closer/RESULTS.md.
"""
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from PIL import Image, ImageFilter, ImageOps  # noqa: E402

from evac import museimage, sam, smsimage  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
OUT = os.path.join(ROOT, "out", "eval", "closer")
CASES = [  # (photo, the thing to find): small things first, then cluttered scenes
    ("sim/samples/spiders/black_widow.jpg", "spider"),
    ("sim/samples/spiders/false_widow.jpg", "spider"),
    ("sim/samples/redhook/downed_line.jpg", "fallen tree"),
    ("sim/samples/redhook/downed_line.jpg", "power line"),
    ("sim/samples/redhook/flooded_corner.jpg", "street sign"),
    ("sim/samples/Bicycle.jpg", "basket"),
    ("sim/samples/Teapot.jpg", "mug"),
    ("sim/samples/Teapot.jpg", "lemon"),
]
RESTORE = ("Restore this small, blurry, black-and-white photo at full size and in colour. Keep every object exactly where "
           "it is, with the same framing and layout. Do not add, remove or move anything.")


def small_picture(path):
    return Image.open(io.BytesIO(smsimage.webp(Image.open(path).convert("RGB")))).convert("RGB")


def best(img, prompt):
    """SAM's most confident match as (box as fractions of img, score), or None."""
    try:
        found = sam.segment(img, prompt)
    except Exception:
        return None
    if not found:
        return None
    x1, y1, x2, y2 = found[0]["box"]
    return [x1 / img.width, y1 / img.height, x2 / img.width, y2 / img.height], found[0]["score"]


def tiles(small, prompt, grid=3, side=512):
    """SAM on overlapping tiles of the small picture, each enlarged: the best box, mapped back to the whole picture."""
    W, H = small.size
    tw, th = W / 2, H / 2  # each tile is half the picture each way; 3 x 3 of them overlap by half
    jobs = []
    for i in range(grid):
        for j in range(grid):
            x0, y0 = i * (W - tw) / (grid - 1), j * (H - th) / (grid - 1)
            tile = small.crop((round(x0), round(y0), round(x0 + tw), round(y0 + th))).resize((side, side), Image.LANCZOS)
            jobs.append((x0, y0, tile))
    with ThreadPoolExecutor(9) as pool:
        found = list(pool.map(lambda job: best(job[2], prompt), jobs))
    hits = [((x0 + b[0] * tw) / W, (y0 + b[1] * th) / H, (x0 + b[2] * tw) / W, (y0 + b[3] * th) / H, s)
            for (x0, y0, _), f in zip(jobs, found) if f for b, s in [f]]
    if not hits:
        return None
    h = max(hits, key=lambda h: h[4])
    return list(h[:4]), h[4]


def method_inputs(small, path):
    big = small.resize((1024, 1024 * small.height // small.width), Image.LANCZOS)
    sharp = ImageOps.autocontrast(big.filter(ImageFilter.UnsharpMask(radius=4, percent=180, threshold=2)), cutoff=1)
    return {"raw": small, "x4": small.resize((512, 512 * small.height // small.width), Image.LANCZOS), "x8sharp": sharp}


def hit(box, truth):
    if not box or not truth:
        return False
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return truth[0] <= cx <= truth[2] and truth[1] <= cy <= truth[3]


def main():
    os.makedirs(OUT, exist_ok=True)
    restored = {}
    with ThreadPoolExecutor(2) as pool:  # Muse Image, once per photo (cached)
        paths = sorted({p for p, _ in CASES})
        for p, im in zip(paths, pool.map(lambda p: museimage.generate(RESTORE, image=small_picture(os.path.join(ROOT, p))), paths)):
            restored[p] = im
            im.save(os.path.join(OUT, os.path.basename(p).replace(".jpg", "_restored.jpg")), quality=88)
    rows = []
    for path, thing in CASES:
        full = Image.open(os.path.join(ROOT, path)).convert("RGB")
        small = small_picture(os.path.join(ROOT, path))
        truth = best(full, thing)
        row = {"photo": os.path.basename(path)[:-4], "thing": thing, "truth": truth and [round(v, 3) for v in truth[0]],
               "truth_score": truth and round(truth[1], 2), "size_px": truth and round(max(truth[0][2] - truth[0][0], truth[0][3] - truth[0][1]) * 128)}
        t = time.time()
        inputs = method_inputs(small, path)
        results = {m: best(img, thing) for m, img in inputs.items()}
        results["tiles"] = tiles(small, thing)
        results["restored"] = best(restored[path], thing)
        for m, r in results.items():
            row[m] = {"box": r and [round(v, 3) for v in r[0]], "score": r and round(r[1], 2), "hit": hit(r and r[0], truth and truth[0])}
        rows.append(row)
        print("%-15s %-12s truth %s (%s px at 128)  " % (row["photo"], thing, row["truth_score"], row["size_px"]) +
              "  ".join("%s:%s" % (m, ("HIT %.2f" % row[m]["score"]) if row[m]["hit"] else ("miss %.2f" % row[m]["score"] if row[m]["score"] else "none"))
                        for m in ["raw", "x4", "x8sharp", "tiles", "restored"]) + "  (%.0f s)" % (time.time() - t), flush=True)
    json.dump(rows, open(os.path.join(OUT, "sam_small.json"), "w"), indent=1)
    methods = ["raw", "x4", "x8sharp", "tiles", "restored"]
    md = ["# SAM 3.1 on the 128 px picture the server receives", "",
          "Generated by `evals/closer/sam_small.py`. Ground truth: SAM on the full-size colour original. A hit: the best "
          "box's centre is inside the truth's box. `size` is the thing's longest side in the 128 px picture.", "",
          "| Photo | Thing | size | " + " | ".join(methods) + " |", "|---|---|---|" + "---|" * len(methods)]
    for r in rows:
        md.append("| %s | %s | %s px | " % (r["photo"], r["thing"], r["size_px"]) +
                  " | ".join(("**hit** %.2f" % r[m]["score"]) if r[m]["hit"] else ("miss %.2f" % r[m]["score"] if r[m]["score"] else "—") for m in methods) + " |")
    md.append("| **found** | | | " + " | ".join("%d / %d" % (sum(r[m]["hit"] for r in rows), len(rows)) for m in methods) + " |")
    md += ["", "What this means: enlarging can't put back pixels that aren't there. Things under ~20 px in the 128 px picture "
           "(the spiders, a lemon, a street sign) were never found, whatever the enlarging; things from ~25 px (a basket, a mug) "
           "were, even raw. Muse Image's restoration found big things but erased the black widow altogether. So SAM is used on "
           "the close-ups the phone sends back (evac/closer.py), where the thing fills the frame, and Muse aims the first "
           "close-up from context."]
    open(os.path.join(os.path.dirname(__file__), "RESULTS.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
