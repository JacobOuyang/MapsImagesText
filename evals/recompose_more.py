"""Second pass over a finished photo_eval run: more recomposition inputs, and analyses that need no new drawings.

    .venv/bin/python evals/recompose_more.py

1. Muse Image from DECODED drawings. The first run showed that handing the image model the text drawing itself makes
   it copy the glyph texture (shaded steps become a stepped pyramid, a dotted background a dotted cloth). Decoding
   the drawing to grey first (D0) removes that. New inputs: the decoded 58-column drawing + key, the decoded drawing +
   the caption, every zoom decoded + the caption.
2. One subject score for all seven inputs: SAM's box for the subject on the photo against SAM's box on the recreation.
3. Ceilings with adaptive tones: the photo averaged into a 58/116/232-column grid and snapped to its own best 5 or
   10 greys (what a drawing with a tone legend could reach), to tell resolution limits from tone limits.
4. JPEG at the same byte budgets as the text, for scale.
5. Subject vs background ink (figure-ground ratio) for the emphasised and faithful drawings.
"""
import io
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_eval as E  # noqa: E402

GEN_NEW = {
    "base_decoded_key": ("d0_faithful.png", "key"),
    "base_decoded_caption": ("d0_faithful.png", "caption"),
    "zooms_decoded_caption": ("d0_greedy.png", "caption"),
}
REBUILD = ("The input is a rough greyscale reconstruction of a photograph. Recreate the original photograph as a "
           "realistic, detailed photo with the same composition. ")


def original(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((2048, 2048))
    return im


def adaptive_ceiling(img, cols, levels, ref):
    rows = max(4, round(cols * img.height / img.width / E.MONO_CELL))
    g = E.lightness(img, cols, rows)
    qs = np.quantile(g, np.linspace(0, 1, levels + 1))
    centres = np.array([g[(g >= a) & (g <= b)].mean() if ((g >= a) & (g <= b)).any() else (a + b) / 2 for a, b in zip(qs, qs[1:])])
    snapped = centres[np.abs(g[..., None] - centres[None, None]).argmin(-1)]
    up = np.asarray(Image.fromarray((snapped * 255).astype(np.uint8)).resize(ref.shape[::-1], Image.BILINEAR), float) / 255
    return E.ssim(up, ref)


def jpeg_at(img, budget, ref):
    """The best SSIM a JPEG of the photo reaches within `budget` bytes (any width up to the analysis width)."""
    best = (None, 0)
    for w in (24, 32, 48, 64, 96, 128, 160, 232):
        small = img.resize((w, max(1, round(w * img.height / img.width))), Image.LANCZOS).convert("L")
        for q in (95, 80, 60, 40, 25, 10):
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=q)
            if buf.tell() <= budget:
                dec = Image.open(io.BytesIO(buf.getvalue())).resize(ref.shape[::-1], Image.BILINEAR)
                s = E.ssim(np.asarray(dec, float) / 255, ref)
                if s > best[1]:
                    best = (buf.tell(), s)
                break
    return best[1]


def one(item):
    key, name, path = item
    d = os.path.join(E.OUT, key)
    rp = os.path.join(d, "result.json")
    if not os.path.exists(rp):
        return key, "no result"
    r = json.load(open(rp))
    if "generate2" in r and "analysis" in r:
        return key, "cached"
    full = original(path)
    H = round(E.ANALYSIS_W * full.height / full.width)
    ref = np.asarray(full.convert("L").resize((E.ANALYSIS_W, H), Image.BOX), float) / 255
    main = r.get("main") or r.get("object")
    names = ", ".join(v for _, v in sorted(r.get("key", {}).items(), key=lambda kv: int(kv[0])))

    # 1 + 2: recreations, all scored against SAM's box on the photo.
    truth = E.subject_box(full, main) if main else None
    gen2 = {}
    for tag in ["caption_only", "drawing", "drawing_key", "zooms_key"] + list(GEN_NEW):
        out = os.path.join(d, "gen_%s.jpg" % tag)
        try:
            if tag in GEN_NEW and not os.path.exists(out):
                src, extra = GEN_NEW[tag]
                inp = Image.open(os.path.join(d, src)).convert("RGB").resize(full.size)
                prompt = REBUILD + ("It shows: %s." % names if extra == "key" else "It shows: %s" % r.get("caption", ""))
                E.generate(prompt, inp).resize(full.size).save(out, quality=85)
            made = Image.open(out).convert("RGB").resize(full.size)
            m = {"layout_corr": E.layout_corr(full, made)}
            mb = E.subject_box(made, main) if main else None
            m["subject_found"] = mb is not None
            m["subject_iou"] = E.box_iou(truth, mb) if (truth and mb) else 0.0
            if tag in GEN_NEW:
                m.update({k: v for k, v in E.compare(full, made, None, None).items() if k.startswith("judge")})
            else:
                m.update({k: v for k, v in r.get("generate", {}).get(tag, {}).items() if k.startswith("judge")})
            gen2[tag] = m
        except Exception as ex:
            traceback.print_exc()
            gen2[tag] = {"error": "%s: %s" % (type(ex).__name__, ex)}
    r["generate2"] = gen2
    r["truth_found"] = truth is not None

    # 3-5: analyses.
    a = {"adaptive": {}, "jpeg": {}}
    for cols in (58, 116, 232):
        for levels in (5, 10):
            a["adaptive"]["%d_%d" % (cols, levels)] = adaptive_ceiling(full, cols, levels, ref)
    rec = r["recompose"]
    for tag, budget in (("base", rec["faithful"]["bytes"]), ("tiles2", rec["tiles2"]["bytes"]), ("tiles4", rec["tiles4"]["bytes"])):
        a["jpeg"][tag] = {"bytes": budget, "ssim": jpeg_at(full, budget, ref)}
    for tag in ("faithful", "high"):
        s_ink, b_ink = r[tag].get("subject_ink"), r[tag].get("background_ink")
        a["figure_ground_" + tag] = (s_ink / b_ink) if (s_ink is not None and b_ink) else None
    r["analysis"] = a
    json.dump(r, open(rp, "w"), indent=1, default=float)
    return key, "ok"


def main():
    items = E.dataset()
    with ThreadPoolExecutor(3) as pool:
        for key, status in pool.map(one, items):
            print("%-28s %s" % (key, status), flush=True)


if __name__ == "__main__":
    main()
