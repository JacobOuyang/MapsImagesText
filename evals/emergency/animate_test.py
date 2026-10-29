"""Test: animate one key frame into the next step by moving the part that moves, drawing only what the action needs.

    .venv/bin/python evals/emergency/animate_test.py      -> out/eval/animate.html (+ printed frames)

Two cases:
  gas valve  the handle turns a quarter turn, from along the pipe (on) to across it (off). Object only, no hand.
  CPR        the stacked hands and straight arms push the chest down. Only the chest, hands and arms are drawn.
For each, Muse Spark names what to keep (the part that moves and what it acts on) and how the part moves, as numbers
(rotate N degrees about a pivot part, or move by a fraction of its size); SAM 3.1 finds the parts; the frames are the
same masks with the moving one rotated or moved. Nothing is generated, so nothing can drift. The drawing: what is
acted on filled ▓, the moving part as an outline on top (see-through, so it never hides it), an arrow for the motion.
For CPR, the same step is also made the other way (Muse Image edits the key frame) to compare, with a check that
nothing outside the moving part changed.
"""
import base64
import html
import io
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402
import visualize_tree as V  # noqa: E402
from evac import imagesearch, llm, museimage, steps, zooms  # noqa: E402
from evac.photo import MONO_CELL, contour  # noqa: E402

COLS, MAX_ROWS = 40, 20
MOTION = ("A text drawing will show one action from this picture: %s. Draw as little as possible: only the part that "
          "moves and the thing it acts on (no whole bodies, no faces, no extra hands). Name them as short noun phrases "
          "an image segmentation model can find in the picture (the moving parts may be several, e.g. hands and arms), "
          "and say how they move, in numbers. Reply "
          'as JSON: {"moving": [""], "acted_on": [""], "motion": {"type": "rotate" or "move", "degrees": number '
          '(counter-clockwise, for rotate), "pivot": "the part it turns about (for rotate)", "dx": number, "dy": number '
          '(for move: a fraction of the moving part\'s width and height; down is positive)}, "arrow": one of ↑ ↓ ← →, '
          '"frames": 3}')
h = html.escape


def best_mask(img, name):
    """SAM's best match for the name, else for its last word ('stacked hands' -> 'hands')."""
    for q in dict.fromkeys([name, name.split()[-1]] if name else []):
        c = steps._candidates(img, [q])
        if c:
            return max(c, key=lambda x: x[1])[2]
    return None


def rotate(m, degrees, pivot):
    im = Image.fromarray(m.astype(np.uint8) * 255)
    return np.asarray(im.rotate(degrees, resample=Image.NEAREST, center=pivot)) > 127


def move(m, dx, dy):
    out = np.zeros_like(m)
    H, W = m.shape
    ys, xs = np.nonzero(m)
    ys2, xs2 = ys + int(round(dy)), xs + int(round(dx))
    ok = (ys2 >= 0) & (ys2 < H) & (xs2 >= 0) & (xs2 < W)
    out[ys2[ok], xs2[ok]] = True
    return out


def draw(box, acted, moving, arrow=None, arrow_at=None):
    """acted: masks filled ▓; moving: mask outlined on top; arrow glyph at an image point."""
    x0, y0, x1, y1 = box
    W, H = x1 - x0, y1 - y0
    cols = max(12, min(COLS, round(MAX_ROWS * W / H * MONO_CELL)))
    rows = max(4, round(cols * H / W / MONO_CELL))
    grid = [[" "] * cols for _ in range(rows)]
    crop = lambda m: m[y0:y1, x0:x1]
    for m in acted:
        cover = np.asarray(Image.fromarray(crop(m).astype(np.uint8) * 255).resize((cols, rows), Image.BOX)) / 255
        for r, c in zip(*np.nonzero(cover >= 0.35)):
            grid[r][c] = "▓"
        for r, line in enumerate(contour(crop(m), cols, rows)):
            for c, ch in enumerate(line):
                if ch != " " and grid[r][c] == " ":
                    grid[r][c] = ch
    if moving is not None:
        for r, line in enumerate(contour(crop(moving), cols, rows)):
            for c, ch in enumerate(line):
                if ch != " ":
                    grid[r][c] = ch
    if arrow and arrow_at:
        c = int((arrow_at[0] - x0) / W * cols)
        r = int((arrow_at[1] - y0) / H * rows)
        if 0 <= r < rows and 0 <= c < cols:
            grid[r][c] = arrow
    return ["".join(r).rstrip() for r in grid]


def bbox(masks, size, margin=0.12):
    ys, xs = [], []
    for m in masks:
        yy, xx = np.nonzero(m)
        if len(yy):
            ys += [yy.min(), yy.max()]
            xs += [xx.min(), xx.max()]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    return (max(0, int(min(xs) - margin * w)), max(0, int(min(ys) - margin * h)),
            min(size[0], int(max(xs) + margin * w)), min(size[1], int(max(ys) + margin * h)))


def plan(img, action):
    content = [{"type": "text", "text": MOTION % action}, {"type": "image_url", "image_url": {"url": steps._url(img)}}]
    return llm.chat_json([{"role": "user", "content": content}])


def animate(img, action, reverse=False):
    """-> (plan, frames [rows], box). reverse: the key frame is the end state (the valve photo shows it closed)."""
    p = plan(img, action)
    mv = p.get("motion") or {}
    names = p.get("moving") or []
    names = names if isinstance(names, list) else [names]
    found = [m for m in (best_mask(img, n) for n in names) if m is not None]
    acted = [m for m in (best_mask(img, a) for a in p.get("acted_on") or []) if m is not None]
    if not found:
        return p, [], None
    moving = np.logical_or.reduce(found)
    if not acted:  # SAM missed 'chest' in a drawing: take the person the moving part presses on (the one under it)
        ys, xs = np.nonzero(moving)
        below = np.zeros_like(moving)
        below[ys.max() - (ys.max() - ys.min()) // 3: ys.max() + (ys.max() - ys.min()), xs.min(): xs.max() + 1] = True
        people = [c[2] for c in steps._candidates(img, ["person"])]
        people = [m for m in people if (m & below & ~moving).sum() > 0.05 * below.sum()]
        if people:
            acted = [max(people, key=lambda m: (m & below).sum())]
    acted = [a & ~moving for a in acted]
    n = max(2, min(4, int(p.get("frames") or 3)))
    ys, xs = np.nonzero(moving)
    mw, mh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    if mv.get("type") == "rotate":
        piv = best_mask(img, mv.get("pivot"))
        if piv is not None:
            py, px = np.nonzero(piv & ~moving) if (piv & ~moving).any() else np.nonzero(piv)
            pivot = (float(px.mean()), float(py.mean()))
        else:  # a handle turns about its middle (where it sits on the valve)
            pivot = (float(xs.mean()), float(ys.mean()))
        total = float(mv.get("degrees") or 90)
        steps_ = [total * k / (n - 1) for k in range(n)]
        if reverse:  # start from the other end: the picture shows where the motion ends
            steps_ = [d - total for d in steps_]
        masks = [rotate(moving, d, pivot) for d in steps_]
    else:
        dx, dy = float(mv.get("dx") or 0) * mw, float(mv.get("dy") or 0.1) * mh
        masks = [move(moving, dx * k / (n - 1), dy * k / (n - 1)) for k in range(n)]
    box = bbox(masks, img.size, margin=0.6)  # the moving part and what's right around it, not whole bodies
    frames = []
    for k, m in enumerate(masks):
        yy, xx = np.nonzero(m)
        at = (float(xx.max()) + 0.02 * img.width, float(yy.mean())) if len(xx) else None
        frames.append(draw(box, acted, m, p.get("arrow") if k < n - 1 else None, at))
    return p, frames, box


def edit_compare(img, p, frames_box):
    """The same step as a Muse Image edit of the key frame; drawn the same way; how much changed outside the part."""
    instr = ("The same illustration, same people, same style, same framing and background. Change only this: the "
             "stacked hands and straight arms push the chest down about 5 cm. Nothing else changes.")
    side = max(img.size)
    sq = Image.new("RGB", (side, side), "white")
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    made = museimage.generate(instr, sq).resize((side, side))
    made = made.crop(((side - img.width) // 2, (side - img.height) // 2, (side - img.width) // 2 + img.width,
                      (side - img.height) // 2 + img.height))
    mv_names = p.get("moving") or []
    mv_names = mv_names if isinstance(mv_names, list) else [mv_names]
    found0 = [m for m in (best_mask(img, n) for n in mv_names) if m is not None]
    moving0 = np.logical_or.reduce(found0) if found0 else None
    keep = ~zooms_dilate(moving0, 25) if moving0 is not None else np.ones(img.size[::-1], bool)
    a = np.asarray(img.convert("L"), float) / 255
    b = np.asarray(made.convert("L"), float) / 255
    sm = zooms.ssim_map(a, b)
    same = float(sm[keep].mean())
    found1 = [m for m in (best_mask(made, n) for n in mv_names) if m is not None]
    moving1 = np.logical_or.reduce(found1) if found1 else None
    acted1 = [m for m in (best_mask(made, x) for x in p.get("acted_on") or []) if m is not None]
    rows = draw(frames_box, acted1, moving1) if moving1 is not None else ["(SAM didn't find the part in the edit)"]
    return made, same, rows


def zooms_dilate(m, r):
    im = Image.fromarray(m.astype(np.uint8) * 255).resize((m.shape[1] // 4, m.shape[0] // 4), Image.BOX)
    from PIL import ImageFilter
    im = im.filter(ImageFilter.MaxFilter(2 * (r // 4) + 1))
    return np.asarray(im.resize((m.shape[1], m.shape[0]))) > 0


def b64(im, side=320):
    im = im.convert("RGB").copy()
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    cases = []
    gas = Image.open(os.path.join(HERE, "..", "..", "sim", "samples", "emergency", "gas_shutoff.jpg")).convert("RGB")
    p, fr, box = animate(gas, "turn the gas valve's yellow handle a quarter turn from along the pipe (on) to across the pipe (off)", reverse=True)
    cases.append(("Gas valve: a quarter turn to off", gas, p, fr, None))
    cpr_res = [x for x in imagesearch.commons("CPR Adult Chest Compression 2", 5) if x["title"].endswith("Compression 2")][0]
    cpr = imagesearch.fetch(cpr_res)
    p2, fr2, box2 = animate(cpr, "push the chest down 5 to 6 cm with the stacked hands and straight arms")
    edit = edit_compare(cpr, p2, box2) if box2 else None
    cases.append(("CPR: push down (key frame: %s)" % imagesearch.credit(cpr_res), cpr, p2, fr2, edit))
    out = []
    for title, img, p, frames, edit in cases:
        print("=====", title, json.dumps(p, ensure_ascii=False))
        for f in frames:
            print("\n".join(f))
            print("-" * 40)
        flip = '<pre class="art flip" data-frames="%s">%s</pre>' % (h(json.dumps(["\n".join(f) for f in frames])), h("\n".join(frames[0]))) if frames else "<p>no frames</p>"
        nodes = [V.node("Key frame", V.img(b64(img)), "the picture the animation starts from"),
                 V.node("What Muse keeps and how it moves", "<code>%s</code>" % h(json.dumps(p, ensure_ascii=False, indent=1)), "SAM finds these; nothing else is drawn"),
                 V.node("Animated (moved cut-out)", flip, "%d frames, played in place" % len(frames), "good"),
                 V.node("Every frame", "".join('<pre class="art small2">%s</pre>' % h("\n".join(f)) for f in frames), "")]
        if edit:
            made, same, rows = edit
            print("Muse Image edit: unchanged outside the moving part (SSIM) = %.2f" % same)
            print("\n".join(rows))
            nodes.append(V.node("For comparison: Muse Image edit", V.img(b64(made)) + '<pre class="art">%s</pre>' % h("\n".join(rows)),
                                "the same step generated; outside the moving part it matches the key frame at SSIM %.2f" % same))
        out.append("<section><h2>%s</h2><div class='row'>%s</div></section>" % (h(title), "".join(nodes)))
    style = V.PAGE.split("<style>")[1].split("</style>")[0].replace("%%", "%")
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Animate a key frame</title><style>%s pre.flip{font:11px/13px Menlo,monospace!important;max-width:none!important}"
            " pre.small2{font:7px/8px Menlo,monospace;display:inline-block;margin:4px;max-width:none}</style></head><body><main>"
            "<h1>From one key frame to the next step</h1><p class='intro'>Only the part that moves and what it acts on are drawn; "
            "the moving part is outlined on top of what it acts on (filled ▓), and the next frames move it.</p>%s</main>"
            "<script>document.querySelectorAll('pre.flip').forEach(p=>{const f=JSON.parse(p.dataset.frames);let i=0;"
            "if(f.length>1)setInterval(()=>{i=(i+1)%%f.length;p.textContent=f[i];},900);});</script></body></html>") % (style, "".join(out))
    open(os.path.join(E.OUT, "animate.html"), "w").write(page)
    print(os.path.join(E.OUT, "animate.html"))


if __name__ == "__main__":
    main()
