"""A tree of the whole text codec for a few photos: what the server computes, what is sent, what the receiver rebuilds.

    .venv/bin/python evals/visualize_tree.py          (after photo_eval.py and compress_compare.py)  -> out/eval/tree.html

Uses the saved eval results; the SAM masks and the region map are recomputed from the saved part names (SAM's answers
are cached, so no new model calls).
"""
import base64
import html
import io
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_eval as E  # noqa: E402
from evac import tonal  # noqa: E402

EXAMPLES = ["Dog", "Eagle_and_Waterfall", "Mt_Fuji", "Teapot", "Ducks_on_a_Misty_Pond"]
PALETTE = [(230, 73, 128), (66, 133, 244), (52, 168, 83), (251, 188, 5), (156, 39, 176), (0, 172, 193), (255, 112, 67), (121, 85, 72)]


def b64(im, side=300, fmt="JPEG"):
    im = im.convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, fmt, quality=82)
    return "data:image/%s;base64,%s" % (fmt.lower(), base64.b64encode(buf.getvalue()).decode())


def part_overlay(work, L, named, root):
    """The photo with each SAM part tinted in its own colour."""
    base = np.asarray(work.convert("L").convert("RGB").resize(L.shape[::-1]), float)
    out = base * 0.55 + 255 * 0.15
    for i, (lab, _) in enumerate(sorted(named.items())):
        m = np.isin(L, [l for l in np.unique(L) if root.get(l) == lab])
        out[m] = out[m] * 0.35 + np.array(PALETTE[i % len(PALETTE)]) * 0.65
    return Image.fromarray(out.clip(0, 255).astype(np.uint8)).resize(work.size, Image.NEAREST)


def region_map(L, work):
    """Every region after the recursive splits, in a random colour."""
    rng = np.random.default_rng(1)
    labels = np.unique(L)
    colours = {l: rng.integers(40, 230, 3) for l in labels}
    out = np.zeros(L.shape + (3,), np.uint8)
    for l in labels:
        out[L == l] = colours[l]
    return Image.fromarray(out).resize(work.size, Image.NEAREST)


def zoom_map(full, chosen, part_boxes):
    """The photo in grey with every zoom the greedy order sent, numbered in sending order."""
    im = full.convert("L").convert("RGB")
    im.thumbnail((700, 700))
    k = im.width / full.width
    d = ImageDraw.Draw(im)
    grids = {2: E.tiles(full, 2), 4: E.tiles(full, 4)}
    for order, lab in enumerate(chosen, 1):
        if lab.startswith("part"):
            box, col = part_boxes.get(int(lab.split()[1])), (230, 73, 128)
        else:
            n, i = int(lab.split()[1].split("x")[0]), int(lab.split("#")[1])
            box, col = grids[n][i], (66, 133, 244) if n == 2 else (251, 188, 5)
        if not box:
            continue
        x0, y0, x1, y1 = (v * k for v in box)
        d.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], outline=col, width=3)
        d.rectangle([x0 + 2, y0 + 2, x0 + 22, y0 + 18], fill=col)
        d.text((x0 + 5, y0 + 3), str(order), fill="white")
    return im


def example(key, path):
    d = os.path.join(E.OUT, key)
    r = json.load(open(os.path.join(d, "result.json")))
    c = json.load(open(os.path.join(d, "compress.json")))
    full = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full.thumbnail((2048, 2048))
    work = full.copy()
    work.thumbnail((1600, 1600))
    names = r["parts"]
    L, gray, named, root = tonal.regions(work, names)
    main = next((l for l, n in named.items() if n == names[0]), None)
    plain, _, key_map, _ = tonal.render(L, gray, named, main, E.COLS, root, emphasis=False)
    leg = E.legend(plain, E.lightness(work, E.COLS, len(plain)))
    k = full.width / work.width
    part_boxes = {}
    for n, name in key_map.items():
        lab = next(l for l, nm in named.items() if nm == name)
        ys, xs = np.nonzero(np.isin(L, [l for l in np.unique(L) if root.get(l) == lab]))
        kk = work.width / L.shape[1]
        box = (int(xs.min() * kk * k), int(ys.min() * kk * k), int((xs.max() + 1) * kk * k), int((ys.max() + 1) * kk * k))
        part_boxes[n] = tonal.zoom_box(full, box)
    chosen = [g["zoom"] for g in r["recompose"]["greedy"][1:]]
    tiles2 = sum(1 for z in chosen if "2x2" in z)
    tiles4 = sum(1 for z in chosen if "4x4" in z)
    parts = sum(1 for z in chosen if z.startswith("part"))
    first_zoom = None
    for z in chosen:
        if "4x4" in z:
            n, i = 4, int(z.split("#")[1])
            crop = full.crop(E.tiles(full, 4)[i])
            first_zoom = (z, tonal.draw(crop, segment=False, check=False, emphasis=False)["plain"],
                          E.legend_bytes(E.legend(tonal.draw(crop, segment=False, check=False, emphasis=False)["plain"], E.lightness(crop, E.COLS, 1))))
            break
    pay, am, d0 = c["payload"], c["ascii_muse"], c["ascii_d0"]
    match = c["match"]["ssim256"]
    tones = "tones " + " ".join("%s%d" % ("␣" if ch == " " else ch, round(100 * v)) for ch, v in leg.items())
    D0_base = Image.open(os.path.join(d, "d0_faithful.png"))
    D0_all = Image.open(os.path.join(d, "d0_greedy.png"))
    muse = Image.open(os.path.join(d, "gen_zooms_key.jpg"))
    return {
        "key": key, "caption": r.get("caption", ""), "names": names, "regions": int(len(np.unique(L))),
        "photo": b64(full), "grey": b64(full.convert("L")), "parts_img": b64(part_overlay(work, L, named, root)),
        "region_img": b64(region_map(L, work)), "drawing": "\n".join(plain), "tones": tones,
        "base_bytes": E.size(plain)[0] + E.legend_bytes(leg), "zoom_img": b64(zoom_map(full, chosen, part_boxes), 340),
        "n_zooms": len(chosen), "tiles2": tiles2, "tiles4": tiles4, "parts": parts,
        "zoom_text": "\n".join(first_zoom[1]) if first_zoom else "", "zoom_label": first_zoom[0] if first_zoom else "",
        "key_line": " · ".join("%s %s" % (n, v) for n, v in sorted(r["key"].items(), key=lambda kv: int(kv[0]))),
        "raw": pay["raw"], "gzip": pay["gzip"],
        "d0_base": b64(D0_base.resize(full.size)), "d0_base_ssim": r["recompose"]["faithful"]["ssim"],
        "d0_all": b64(D0_all.resize(full.size)), "d0_all_ssim": d0["ssim256"], "muse": b64(muse), "muse_grey": b64(muse.convert("L")),
        "muse_ssim": am["ssim256"], "match": match,
        "picture": b64(Image.open(os.path.join(d, "cmp_image_2500.jpg"))) if os.path.exists(os.path.join(d, "cmp_image_2500.jpg")) else "",
        "picture_ssim": c["budgets"].get("2500", {}).get("ssim256"), "picture_bytes": c["budgets"].get("2500", {}).get("bytes"),
    }


def node(title, body, sub="", cls=""):
    return '<div class="node %s"><div class="nt">%s</div>%s%s</div>' % (cls, title, body, '<div class="ns">%s</div>' % sub if sub else "")


def img(src, alt=""):
    return '<img src="%s" alt="%s">' % (src, html.escape(alt))


def section(x):
    h = html.escape
    server = "".join([
        node("1 · Muse lists the parts", "<ol>%s</ol>" % "".join("<li>%s</li>" % h(n) for n in x["names"]), "main subject first, backgrounds included"),
        node("2 · SAM segments each part", img(x["parts_img"], "parts"), "one colour per part, sharpened by colour contrast"),
        node("3 · Regions, split by brightness", img(x["region_img"], "regions"), "%d regions after recursive splits and merges" % x["regions"]),
        node("4 · Base drawing, 58 columns", '<pre class="art">%s</pre><code>%s</code>' % (h(x["drawing"]), h(x["tones"])),
             "one tone per region; the legend says which grey each glyph is · %s bytes" % format(x["base_bytes"], ",")),
        node("5 · Greedy zooms", img(x["zoom_img"], "zooms") + ('<pre class="art small">%s</pre>' % h(x["zoom_text"]) if x["zoom_text"] else ""),
             "%d zooms in sending order: %d of 2×2 (blue), %d of 4×4 (yellow), %d parts (pink)%s" % (
                 x["n_zooms"], x["tiles2"], x["tiles4"], x["parts"], " · below: %s, drawn 58 columns wide" % h(x["zoom_label"]) if x["zoom_label"] else "")),
    ])
    payload = node("The text that is sent", "<p class='big'>%s B raw · %s B gzipped</p><code>%s</code>" % (
        format(x["raw"], ","), format(x["gzip"], ","), h(x["key_line"])),
        "base drawing + %d zooms (each with a header and tone legend) + the key" % x["n_zooms"], "pay")
    receiver = "".join([
        node("A · Decode the base drawing", img(x["d0_base"], "base decoded"), "glyph → legend grey; outlines filled from neighbours · SSIM %.2f" % x["d0_base_ssim"]),
        node("B · Paste every zoom", img(x["d0_all"], "all zooms decoded"), "each zoom decoded and pasted over its box · SSIM <b>%.2f</b>" % x["d0_all_ssim"], "good"),
        node("C · Muse Image recreates it", img(x["muse"], "muse"), "input: B + “It shows: …” the key"),
        node("D · Scored in greyscale", img(x["muse_grey"], "muse grey"), "against the greyscale photo · SSIM <b>%.2f</b>" % x["muse_ssim"]),
    ])
    compare = "".join([
        node("Ground truth", img(x["grey"], "grey photo"), "the photo in greyscale (colour is never sent)"),
        node("Just send a picture", img(x["picture"], "picture") if x["picture"] else "", "%s B greyscale picture · SSIM <b>%.2f</b> · the Muse recreation's SSIM is matched by a %s B picture" % (
            format(int(x["picture_bytes"] or 0), ","), x["picture_ssim"] or 0, format(int(x["match"] or 0), ","))),
    ])
    return ('<section id="%s"><h2>%s</h2><p class="cap">%s</p><div class="tree">'
            '<div class="root">%s<div class="ns">the photo</div></div>'
            '<div class="branch"><div class="bl">Server (has the photo)</div><div class="row">%s</div></div>'
            '<div class="branch mid"><div class="row one">%s</div></div>'
            '<div class="branch"><div class="bl">Receiver (has only the text)</div><div class="row">%s</div></div>'
            '<div class="branch"><div class="bl">Compared with</div><div class="row">%s</div></div>'
            '</div></section>') % (x["key"], h(x["key"].replace("_", " ")), h(x["caption"]), img(x["photo"], "photo"), server, payload, receiver, compare)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Text codec tree</title><style>
:root { --bg:#f7f7f5; --fg:#1d1d1f; --quiet:#6b6b70; --line:#c9c9c2; --card:#fff; --a:#2f6fdf; --good:#2b8a3e; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141416; --fg:#ececf0; --quiet:#9a9aa3; --line:#3a3a42; --card:#1d1d21; --a:#7aa7ff; --good:#69db7c; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:0; padding:24px 16px; } main { max-width:1500px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 4px; } h2 { font-size:20px; margin:0; } .cap, .ns, .intro { color:var(--quiet); } .ns { font-size:12px; margin-top:6px; }
nav a { margin-right:12px; color:var(--a); } section { margin:28px 0 40px; }
.tree { display:flex; flex-direction:column; align-items:stretch; gap:0; margin-top:10px; }
.root { align-self:center; text-align:center; } .root img { max-height:200px; border-radius:8px; }
.branch { position:relative; border-left:2px solid var(--line); margin-left:22px; padding:18px 0 4px 22px; }
.branch::before { content:""; position:absolute; left:-2px; top:0; height:18px; border-left:2px solid var(--line); }
.bl { font-weight:650; margin-bottom:8px; } .row { display:flex; flex-wrap:wrap; gap:14px 18px; align-items:flex-start; padding-bottom:6px; }
.row .node { position:relative; } .row .node + .node::before { content:"→"; position:absolute; left:-11px; top:44%%; color:var(--quiet); font-size:14px; }
.node { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px; flex:0 0 auto; max-width:440px; }
.node.pay { border-color:var(--a); } .node.good { border-color:var(--good); } .nt { font-weight:650; margin-bottom:6px; }
.node img { display:block; max-width:300px; max-height:220px; border-radius:6px; } .node ol { margin:0; padding-left:20px; }
pre.art { font:5.2px/6.1px Menlo, monospace; background:#fff; color:#000; padding:6px; border-radius:6px; margin:0; overflow:auto; max-width:420px; }
pre.art.small { margin-top:8px; } code { display:block; font:11px Menlo, monospace; margin-top:6px; white-space:pre-wrap; word-break:break-word; max-width:420px; }
.big { font-size:18px; font-weight:650; margin:0 0 4px; } .one { justify-content:flex-start; }
</style></head><body><main>
<h1>From photo to text and back</h1>
<p class="intro">Five photos through the whole text codec. Server: Muse lists the parts, SAM segments them, regions are split by brightness,
a 58-column drawing with a tone legend is made, and zooms are chosen greedily by error removed per byte. Receiver: decode every drawing
to grey, paste the zooms, optionally ask Muse Image to recreate the photo. Scores are SSIM against the greyscale photo at 256 px.</p>
<nav>%(nav)s</nav>%(sections)s</main></body></html>"""


def main():
    items = {k: p for k, _, p in E.dataset()}
    xs = [example(k, items[k]) for k in EXAMPLES]
    page = PAGE % {"nav": "".join('<a href="#%s">%s</a>' % (x["key"], html.escape(x["key"].replace("_", " "))) for x in xs),
                   "sections": "".join(section(x) for x in xs)}
    out = os.path.join(E.OUT, "tree.html")
    open(out, "w").write(page)
    print(out, len(page) // 1000, "KB")


if __name__ == "__main__":
    main()
