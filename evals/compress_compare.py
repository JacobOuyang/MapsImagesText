"""Is "all zooms + key" a compressed image? Its bytes and quality against simply sending a picture.

    .venv/bin/python evals/compress_compare.py      (after photo_eval.py and recompose_more.py)

Colour is never sent, so the ground truth is the photo in greyscale and everything is compared in greyscale.

Text side: the exact payload a receiver would get. The base drawing and its tone legend; every zoom the greedy
order chose, each with a one-line header saying where it goes and its own legend; and the key. Counted as raw UTF-8
and gzipped, both as one stream and message by message. Its reconstruction is Muse Image's recreation from the
decoded zooms + key ("all zooms + key" in photo_eval.py).

Image side: the greyscale photo as JPEG and WebP over a ladder of widths and qualities, decoded normally. And, for
a fair fight, a tiny JPEG (600 B and 2.5 KB) given to Muse Image with the same prompt and key, so both sides get the
same generative decoder.

Scores are deterministic pixel similarity to the greyscale original only: SSIM at 256 and 64 px wide, PSNR and mean
absolute error at 256 px. No model judges anything. From those: the smallest image packet that matches the text's
reconstruction, and what an image packet of the text's size achieves.
"""
import gzip
import html
import io
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_eval as E  # noqa: E402
from evac import llm, tonal  # noqa: E402

BUDGETS = [300, 600, 1200, 2500, 5000, 10000, 20000]
WIDTHS = [16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768]
QUALITIES = [5, 10, 20, 30, 45, 60, 75, 90]
MUSE_BUDGETS = [600, 2500]
REBUILD = ("The input is a rough greyscale reconstruction of a photograph. Recreate the original photograph as a "
           "realistic, detailed photo with the same composition. It shows: %s.")
SIMILAR = ("Both images are in greyscale. Image A is an original photo; image B is a version of it that was sent over a "
           "very slow connection. How faithfully does B show the same picture as A: the same subject, the same layout, "
           "the same shapes and details? Ignore that both are greyscale. 0 = a different picture, 5 = same scene but "
           "clearly different or very blurry, 10 = indistinguishable. Image A comes first. Reply as JSON: {\"score\": 0-10}")


def grey(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((2048, 2048))
    return im


def arr(im, w):
    return np.asarray(im.convert("L").resize((w, max(1, round(w * im.height / im.width))), Image.BOX), float) / 255


def scores(G, X):
    """Deterministic pixel similarity to the greyscale original: SSIM at 256 and 64 px wide, PSNR and mean absolute
    error at 256 px (0-1 grey)."""
    X = X.convert("L").resize(G.size, Image.BILINEAR)
    a, b = arr(G, 256), arr(X, 256)
    mse = float(((a - b) ** 2).mean())
    return {"ssim256": E.ssim(a, b), "ssim64": E.ssim(arr(G, 64), arr(X, 64)),
            "psnr": 10 * np.log10(1 / mse) if mse > 0 else 99.0, "mae": float(np.abs(a - b).mean())}


def encode(G, w, q, fmt):
    small = G.convert("L").resize((w, max(1, round(w * G.height / G.width))), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "JPEG":
        small.save(buf, "JPEG", quality=q, optimize=True)
    else:
        small.convert("RGB").save(buf, "WEBP", quality=q, method=6)
    data = buf.getvalue()
    return data, Image.open(io.BytesIO(data)).convert("L").resize(G.size, Image.BILINEAR)


def ladder(G):
    pts = []
    for fmt in ("JPEG", "WEBP"):
        for w in WIDTHS:
            if w > G.width:
                continue
            for q in QUALITIES:
                data, dec = encode(G, w, q, fmt)
                pts.append({"fmt": fmt, "w": w, "q": q, "bytes": len(data), **scores(G, dec)})
    return pts


def best_under(pts, budget, metric="ssim256"):
    ok = [p for p in pts if p["bytes"] <= budget]
    return max(ok, key=lambda p: p[metric]) if ok else None


def bytes_to_match(pts, metric, target):
    ok = [p["bytes"] for p in pts if p[metric] >= target]
    return min(ok) if ok else None


def message_text(rows, leg, head=None):
    tones = "tones " + " ".join("%s%d" % (ch, round(100 * v)) for ch, v in leg.items())
    return "\n".join(([head] if head else []) + list(rows) + [tones])


def payload(key, r, full):
    """The exact text the receiver gets for "all zooms + key", in the order the greedy choice sent it."""
    d = os.path.join(E.OUT, key)
    work = full.copy()
    work.thumbnail((1600, 1600))
    base = open(os.path.join(d, "faithful.txt")).read().split("\n")
    msgs = [message_text(base, E.legend(base, E.lightness(work, E.COLS, len(base))))]
    grids = {2: E.tiles(full, 2), 4: E.tiles(full, 4)}
    for step in r["recompose"]["greedy"][1:]:
        lab = step["zoom"]
        if lab.startswith("part"):
            n = int(lab.split()[1])
            rows = open(os.path.join(d, "zoom_part_%d.txt" % n)).read().split("\n")
            leg = {ch: 0.5 for ch in E.SHADE_CHARS if ch == " " or any(ch in x for x in rows)}  # same length as the real one
            msgs.append(message_text(rows, leg, "ZOOM %d" % n))
        else:  # "tile 4x4 #7"
            n, i = int(lab.split()[1].split("x")[0]), int(lab.split("#")[1])
            crop = full.crop(grids[n][i])
            rows = tonal.draw(crop, segment=False, check=False, emphasis=False)["plain"]
            msgs.append(message_text(rows, E.legend(rows, E.lightness(crop, E.COLS, len(rows))), "ZOOM %dx%d %d" % (n, n, i)))
    msgs.append(" · ".join("%s %s" % (n, v) for n, v in sorted(r["key"].items(), key=lambda kv: int(kv[0]))))
    text = "\n\n".join(msgs)
    return {"raw": len(text.encode()), "gzip": len(gzip.compress(text.encode(), 9)),
            "gzip_per_message": sum(len(gzip.compress(m.encode(), 9)) for m in msgs), "messages": len(msgs),
            "zooms": len(msgs) - 2, "chars": len(text)}


def one(item):
    key, name, path = item
    d = os.path.join(E.OUT, key)
    rp = os.path.join(d, "result.json")
    cp = os.path.join(d, "compress.json")
    if not os.path.exists(rp):
        return key, "no result"
    if os.path.exists(cp):
        return key, "cached"
    r = json.load(open(rp))
    full = grey(path)
    G = full.convert("L")
    names = ", ".join(v for _, v in sorted(r["key"].items(), key=lambda kv: int(kv[0])))
    out = {"name": key, "payload": payload(key, r, full)}

    R = Image.open(os.path.join(d, "gen_zooms_key.jpg")).convert("L").resize(G.size)
    D0 = Image.open(os.path.join(d, "d0_greedy.png")).convert("L").resize(G.size, Image.BILINEAR)
    out["ascii_muse"] = scores(G, R)
    out["ascii_d0"] = scores(G, D0)
    R.save(os.path.join(d, "cmp_ascii_muse_grey.jpg"), quality=85)

    pts = ladder(G)
    out["ladder"] = pts
    out["budgets"] = {}
    for b in BUDGETS:
        p = best_under(pts, b)
        if p:
            _, dec = encode(G, p["w"], p["q"], p["fmt"])
            out["budgets"][str(b)] = p
            dec.save(os.path.join(d, "cmp_image_%d.jpg" % b), quality=92)
    for b in MUSE_BUDGETS:
        p = best_under(pts, b)
        if not p:
            continue
        try:
            _, dec = encode(G, p["w"], p["q"], p["fmt"])
            made = E.generate(REBUILD % names, dec.convert("RGB")).convert("L").resize(G.size)
            made.save(os.path.join(d, "cmp_image_muse_%d.jpg" % b), quality=85)
            out["image_muse_%d" % b] = dict(scores(G, made), bytes=p["bytes"], fmt=p["fmt"], w=p["w"], q=p["q"])
        except Exception as ex:
            out["image_muse_%d" % b] = {"error": "%s: %s" % (type(ex).__name__, ex)}
    # The smallest picture that matches the text's reconstruction, metric by metric.
    am = out["ascii_muse"]
    out["match"] = {m: bytes_to_match(pts, m, am[m]) for m in ("ssim256", "ssim64", "psnr")}
    out["match_d0"] = {m: bytes_to_match(pts, m, out["ascii_d0"][m]) for m in ("ssim256", "ssim64", "psnr")}
    out["at_text_size"] = {k: best_under(pts, out["payload"][k]) for k in ("raw", "gzip")}
    json.dump(out, open(cp, "w"), indent=1, default=float)
    return key, "ok"


def med(vals):
    vals = [v for v in vals if v is not None]
    return float(np.median(vals)) if vals else None


METRICS = ("ssim256", "ssim64", "psnr", "mae")


def summarize(results):
    s = {"photos": len(results)}
    s["payload"] = {k: med([r["payload"][k] for r in results]) for k in ("raw", "gzip", "gzip_per_message", "zooms", "messages")}
    for tag in ("ascii_muse", "ascii_d0"):
        s[tag] = {m: med([r[tag][m] for r in results]) for m in METRICS}
    s["budgets"] = {b: {m: med([r["budgets"].get(str(b), {}).get(m) for r in results]) for m in METRICS + ("bytes",)} for b in BUDGETS}
    for b in MUSE_BUDGETS:
        k = "image_muse_%d" % b
        rows = [r[k] for r in results if isinstance(r.get(k), dict) and "error" not in r[k]]
        s[k] = dict({m: med([x[m] for x in rows]) for m in METRICS + ("bytes",)}, n=len(rows))
    for tag in ("match", "match_d0"):
        s[tag] = {}
        for m in ("ssim256", "ssim64", "psnr"):
            vals = [r[tag][m] for r in results]
            s[tag][m] = {"median_bytes": med(vals), "unmatched": sum(v is None for v in vals)}
    s["at_text_size"] = {k: {m: med([(r["at_text_size"][k] or {}).get(m) for r in results]) for m in METRICS + ("bytes",)} for k in ("raw", "gzip")}
    s["ratio"] = {"raw": med([r["payload"]["raw"] / r["match"]["ssim256"] for r in results if r["match"]["ssim256"]]),
                  "gzip": med([r["payload"]["gzip"] / r["match"]["ssim256"] for r in results if r["match"]["ssim256"]])}
    s["photos_where_text_beats_same_size_picture"] = {
        k: sum(1 for r in results if r["at_text_size"][k] and r["ascii_muse"]["ssim256"] > r["at_text_size"][k]["ssim256"]) for k in ("raw", "gzip")}
    return s


FONT = "/System/Library/Fonts/Menlo.ttc"


def sheet(results, names, path):
    font = ImageFont.truetype(FONT, 13)
    rows = []
    for key in names:
        r = next((x for x in results if x["name"] == key), None)
        if not r:
            continue
        d = os.path.join(E.OUT, key)
        G = Image.open(os.path.join(d, "photo.jpg")).convert("L")
        mb = r["match"]["ssim256"]
        match_file = None
        if mb:  # re-encode the matching picture for display
            p = min((x for x in r["ladder"] if x["bytes"] == mb), key=lambda x: x["bytes"])
            src = grey(next(path for k, _, path in E.dataset() if k == key))
            _, dec = encode(src.convert("L"), p["w"], p["q"], p["fmt"])
            match_file = os.path.join(d, "cmp_image_match.jpg")
            dec.save(match_file, quality=92)
        am = r["ascii_muse"]
        cells = [(os.path.join(d, "photo.jpg"), "original, greyscale"),
                 (os.path.join(d, "cmp_ascii_muse_grey.jpg"), "text: %.1f KB (%.1f gz) SSIM %.2f" % (r["payload"]["raw"] / 1000, r["payload"]["gzip"] / 1000, am["ssim256"])),
                 (os.path.join(d, "d0_greedy.png"), "text decoded, no model: SSIM %.2f" % r["ascii_d0"]["ssim256"]),
                 (match_file, "picture, same SSIM: %s" % ("%d B" % mb if mb else "–")),
                 (os.path.join(d, "cmp_image_2500.jpg"), "picture, 2.5 KB: SSIM %.2f" % r["budgets"].get("2500", {}).get("ssim256", 0))]
        tiles = []
        for f, lab in cells:
            c = Image.new("RGB", (250, 222), "white")
            if f and os.path.exists(f):
                im = Image.open(f).convert("L").resize(G.size).convert("RGB")
                im.thumbnail((240, 180))
                c.paste(im, (5, 5))
            ImageDraw.Draw(c).text((5, 190), lab[:38], font=font, fill="black")
            tiles.append(c)
        row = Image.new("RGB", (250 * len(tiles), 222), "white")
        for i, t in enumerate(tiles):
            row.paste(t, (250 * i, 0))
        rows.append(row)
    if rows:
        out = Image.new("RGB", (rows[0].width, sum(x.height for x in rows)), "white")
        y = 0
        for x in rows:
            out.paste(x, (0, y))
            y += x.height
        out.save(path, quality=85)


def svg_chart(s):
    W, H, pad = 680, 320, 52
    lo = min(v for v in [s["budgets"][b]["ssim256"] for b in BUDGETS] + [s["ascii_muse"]["ssim256"], s["ascii_d0"]["ssim256"]] if v is not None) - 0.05
    hi = max(v for v in [s["budgets"][b]["ssim256"] for b in BUDGETS] if v is not None) + 0.03
    x0, x1 = np.log10(200), np.log10(max(150000, (s["payload"]["raw"] or 1) * 1.3))
    X = lambda b: pad + (np.log10(b) - x0) / (x1 - x0) * (W - 2 * pad)
    Y = lambda v: H - pad - (v - lo) / (hi - lo) * (H - 2 * pad)
    p = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="SSIM against bytes">' % (W, H)]
    for v in np.arange(np.ceil(lo * 10) / 10, hi, 0.1):
        p.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/><text x="%d" y="%.1f" class="tick">%.1f</text>' % (pad, W - pad, Y(v), Y(v), pad - 8, Y(v) + 4, v))
    for b in (300, 1000, 3000, 10000, 30000, 100000):
        p.append('<text x="%.1f" y="%d" class="tickx">%s</text>' % (X(b), H - pad + 16, "%g KB" % (b / 1000) if b >= 1000 else "%d B" % b))
    pts = [(b, s["budgets"][b]["ssim256"]) for b in BUDGETS if s["budgets"][b]["ssim256"] is not None]
    p.append('<polyline points="%s" class="img"/>' % " ".join("%.1f,%.1f" % (X(b), Y(v)) for b, v in pts))
    for b, v in pts:
        p.append('<circle cx="%.1f" cy="%.1f" r="3.5" class="imgdot"><title>picture under %d B: SSIM %.3f</title></circle>' % (X(b), Y(v), b, v))
    for tag, v_key, cls, lab in (("raw", "ascii_muse", "txt", "text + Muse Image (raw)"), ("gzip", "ascii_muse", "txtgz", "text + Muse Image (gzipped)"),
                                 ("gzip", "ascii_d0", "d0", "text decoded, no model (gzipped)")):
        b, v = s["payload"][tag], s[v_key]["ssim256"]
        p.append('<circle cx="%.1f" cy="%.1f" r="6" class="%s"><title>%s: %.0f B, SSIM %.3f</title></circle>' % (X(b), Y(v), cls, lab, b, v))
        p.append('<text x="%.1f" y="%.1f" class="lab">%s</text>' % (X(b) + 9, Y(v) + 4, lab))
    for b in MUSE_BUDGETS:
        v = s["image_muse_%d" % b]["ssim256"]
        if v is not None:
            p.append('<rect x="%.1f" y="%.1f" width="10" height="10" class="mus"><title>%d B picture + Muse Image: SSIM %.3f</title></rect>' % (X(b) - 5, Y(v) - 5, b, v))
    p.append('<text x="%d" y="%d" class="axis">bytes sent (log scale)</text>' % (W // 2, H - 8))
    p.append('<text x="14" y="%d" class="axis" transform="rotate(-90 14 %d)">SSIM vs greyscale original, 256 px (median)</text>' % (H // 2, H // 2))
    p.append("</svg>")
    return "".join(p)


def build(results):
    s = summarize(results)
    json.dump(s, open(os.path.join(E.OUT, "compress_summary.json"), "w"), indent=1)
    sheet(results, ["Dog", "Eagle_and_Waterfall", "Mt_Fuji", "Teapot", "Ducks_on_a_Misty_Pond", "Flamingos"],
          os.path.join(E.OUT, "compress_examples.jpg"))
    am, d0, pl = s["ascii_muse"], s["ascii_d0"], s["payload"]
    fmt = lambda x: "".join("<td>%s</td>" % ("–" if x.get(m) is None else f % x[m]) for m, f in (("ssim256", "%.3f"), ("ssim64", "%.3f"), ("psnr", "%.1f"), ("mae", "%.3f")))
    rows = "".join("<tr><td>%s</td><td>%s</td>%s</tr>" % (html.escape(lab), b, fmt(x)) for lab, b, x in
                   [("All zooms + key, recreated by Muse Image", "%.0f raw · %.0f gzipped" % (pl["raw"], pl["gzip"]), am),
                    ("All zooms, decoded to grey (no model)", "same text", d0)] +
                   [("Greyscale picture, best JPEG/WebP under %s" % ("%d B" % b if b < 1000 else "%g KB" % (b / 1000)), "%.0f" % (s["budgets"][b]["bytes"] or 0), s["budgets"][b]) for b in BUDGETS] +
                   [("%d B picture + Muse Image (same prompt)" % b, "%.0f" % (s["image_muse_%d" % b]["bytes"] or 0), s["image_muse_%d" % b]) for b in MUSE_BUDGETS])
    m, m0 = s["match"], s["match_d0"]
    page = PAGE % {
        "n": s["photos"], "raw": pl["raw"] / 1000, "gz": pl["gzip"] / 1000, "gzm": pl["gzip_per_message"] / 1000, "zooms": pl["zooms"],
        "match": "%.0f B" % m["ssim256"]["median_bytes"] if m["ssim256"]["median_bytes"] else "–",
        "match_d0": "%.0f B" % m0["ssim256"]["median_bytes"] if m0["ssim256"]["median_bytes"] else "–",
        "ratio_raw": s["ratio"]["raw"] or 0, "ratio_gz": s["ratio"]["gzip"] or 0, "chart": svg_chart(s), "rows": rows,
        "at_gz_ssim": s["at_text_size"]["gzip"]["ssim256"] or 0, "at_raw_ssim": s["at_text_size"]["raw"]["ssim256"] or 0,
        "am_ssim": am["ssim256"] or 0, "d0_ssim": d0["ssim256"] or 0,
        "beats_gz": s["photos_where_text_beats_same_size_picture"]["gzip"], "beats_raw": s["photos_where_text_beats_same_size_picture"]["raw"],
    }
    open(os.path.join(E.OUT, "compress.html"), "w").write(page)
    return s


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Text vs picture</title><style>
:root { --bg:#f7f7f5; --fg:#1d1d1f; --quiet:#6b6b70; --line:#dcdcd6; --card:#fff; --a:#2f6fdf; --b:#d9480f; --c:#2b8a3e; --d:#7048e8; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141416; --fg:#ececf0; --quiet:#9a9aa3; --line:#2f2f35; --card:#1d1d21; --a:#7aa7ff; --b:#ff8a50; --c:#69db7c; --d:#b197fc; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:24px 16px; } main { max-width:1000px; margin:0 auto; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:19px; margin:30px 0 8px; } .note { color:var(--quiet); font-size:13px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-top:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; } .big { font-size:26px; font-weight:650; }
table { border-collapse:collapse; width:100%%; background:var(--card); font-size:14px; } th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; }
th:first-child, td:first-child { text-align:left; }
svg .grid { stroke:var(--line); } svg text { fill:var(--quiet); font:11px system-ui; } svg .tick { text-anchor:end; } svg .tickx { text-anchor:middle; }
svg .axis { text-anchor:middle; font-size:12px; } svg .img { fill:none; stroke:var(--a); stroke-width:2.5; } svg .imgdot { fill:var(--a); }
svg .txt { fill:var(--b); } svg .txtgz { fill:none; stroke:var(--b); stroke-width:2.5; } svg .d0 { fill:var(--d); } svg .mus { fill:var(--c); } svg .lab { fill:var(--fg); }
.legend span { display:inline-block; margin-right:14px; font-size:13px; } .sw { display:inline-block; width:11px; height:11px; border-radius:6px; margin-right:5px; vertical-align:-1px; }
</style></head><body><main>
<h1>All zooms + key, as an image codec</h1>
<p class="note">%(n)d photos. Ground truth: the photo in greyscale (colour is never sent). Every score is deterministic pixel
similarity to it: SSIM at 256 and 64 px wide, PSNR and mean absolute error at 256 px. Text = the base drawing, every zoom the
greedy order sends (median %(zooms).0f), each with a header and a tone legend, and the key.</p>
<div class="cards">
<div class="card"><div class="big">%(raw).1f KB</div>text sent (median), raw UTF-8</div>
<div class="card"><div class="big">%(gz).1f KB</div>gzipped as one stream (%(gzm).1f KB message by message)</div>
<div class="card"><div class="big">%(match)s</div>greyscale picture with the same SSIM as the text's Muse recreation (%(am_ssim).2f), median</div>
<div class="card"><div class="big">%(ratio_raw).0f× / %(ratio_gz).0f×</div>text ÷ that picture, raw / gzipped (median per photo)</div>
</div>
<h2>SSIM against bytes</h2>
<p class="legend"><span><i class="sw" style="background:var(--a)"></i>greyscale picture (best JPEG or WebP under the budget)</span>
<span><i class="sw" style="background:var(--b)"></i>text + Muse Image, raw and gzipped</span><span><i class="sw" style="background:var(--d)"></i>text decoded, no model</span>
<span><i class="sw" style="background:var(--c);border-radius:2px"></i>tiny picture + Muse Image</span></p>
%(chart)s
<h2>Every option</h2>
<table><tr><th>What is sent</th><th>bytes (median)</th><th>SSIM 256 px</th><th>SSIM 64 px</th><th>PSNR dB</th><th>mean abs. error</th></tr>%(rows)s</table>
<p class="note">Decoded without a model, the same text reaches SSIM %(d0_ssim).2f, which a %(match_d0)s picture matches (median).
A picture as big as the gzipped text reaches %(at_gz_ssim).2f, as big as the raw text %(at_raw_ssim).2f. The text's Muse recreation
beat a same-size picture in %(beats_gz)d photos (gzipped size) and %(beats_raw)d (raw size) of %(n)d.</p>
</main></body></html>"""


def main():
    items = E.dataset()
    with ThreadPoolExecutor(4) as pool:
        for key, status in pool.map(one, items):
            print("%-28s %s" % (key, status), flush=True)
    results = []
    for key, _, _ in items:
        p = os.path.join(E.OUT, key, "compress.json")
        if os.path.exists(p):
            results.append(json.load(open(p)))
    s = build(results)
    print(json.dumps({k: v for k, v in s.items() if k != "budgets"}, indent=1))
    print("budgets", json.dumps(s["budgets"]))


if __name__ == "__main__":
    main()
