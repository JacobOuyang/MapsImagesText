"""Build out/eval/report.html (self-contained: charts as inline SVG, thumbnails inline) and out/eval/summary.json
from the per-photo results that photo_eval.py writes."""
import base64
import html
import io
import json
import os

import numpy as np
from PIL import Image

STRATEGIES = [("high", "Base drawing, as sent (subject emphasised)"), ("faithful", "Base drawing, one tone scale"),
              ("parts", "+ zoom on the numbered parts"), ("tiles2", "+ every 2x2 zoom"), ("tiles4", "+ every 2x2 and 4x4 zoom")]
CEILINGS = [("58_glyphs", "58 columns, 5 shading glyphs"), ("58", "58 columns, any grey"), ("116_glyphs", "116 columns, 5 glyphs"),
            ("116", "116 columns, any grey"), ("232_glyphs", "232 columns, 5 glyphs"), ("232", "232 columns, any grey")]
GEN = [("caption_only", "Caption only (no drawing)"), ("drawing", "Drawing only"), ("drawing_key", "Drawing + key"),
       ("zooms_key", "Rebuild from all zooms + key")]
GEN2 = [("caption_only", "Caption only (no drawing)"), ("drawing", "Text drawing only"), ("drawing_key", "Text drawing + key"),
        ("base_decoded_key", "Drawing decoded to grey + key"), ("base_decoded_caption", "Drawing decoded to grey + caption"),
        ("zooms_key", "All zooms decoded + key"), ("zooms_decoded_caption", "All zooms decoded + caption")]


def load(out, names):
    res = []
    for n in names:
        p = os.path.join(out, n, "result.json")
        if os.path.exists(p):
            res.append(json.load(open(p)))
    return res


def mean(vals):
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return float(np.mean(vals)) if vals else None


def pick(r, *path):
    for p in path:
        if not isinstance(r, dict) or p not in r:
            return None
        r = r[p]
    return r


def summarize(results, chance, kind):
    s = {"photos": len(results), "chance_layout_corr": chance, "drawings": {}, "recompose": {}, "generate": {}, "by_kind": {}}
    for tag in ("low", "faithful", "high"):
        d = {}
        for m in ("tone_r", "tone_ssim", "edge_f1", "edge_precision", "edge_recall", "subject_iou", "contrast", "subject_ink",
                  "background_ink", "bytes", "gzip", "read_scene_score"):
            d[m] = mean([pick(r, tag, m) for r in results])
        d["read_subject_match"] = mean([float(pick(r, tag, "read_subject_match")) for r in results if pick(r, tag, "read_subject_match") is not None])
        s["drawings"][tag] = d
    s["seconds"] = {"low": mean([r.get("low_seconds") for r in results]), "high": mean([r.get("high_seconds") for r in results])}
    rec = s["recompose"]
    rec["flat"] = mean([pick(r, "recompose", "flat", "ssim") for r in results])
    for key, _ in CEILINGS:
        rec["ceiling_" + key] = mean([pick(r, "recompose", "ceiling", key, "ssim") for r in results])
    for key, _ in STRATEGIES:
        rec[key] = {"ssim": mean([pick(r, "recompose", key, "ssim") for r in results]),
                    "mae": mean([pick(r, "recompose", key, "mae") for r in results]),
                    "bytes": mean([pick(r, "recompose", key, "bytes") for r in results])}
    rec["greedy_final"] = {"ssim": mean([r["recompose"]["greedy"][-1]["ssim"] for r in results if pick(r, "recompose", "greedy")]),
                           "bytes": mean([r["recompose"]["greedy"][-1]["bytes"] for r in results if pick(r, "recompose", "greedy")])}
    # Share of the way from a flat grey picture to the 232-column ceiling.
    def recovered(r, ssim_value):
        lo, hi = pick(r, "recompose", "flat", "ssim"), pick(r, "recompose", "ceiling", "232", "ssim")
        return (ssim_value - lo) / (hi - lo) if (lo is not None and hi and hi > lo and ssim_value is not None) else None
    rec["recovered"] = {key: mean([recovered(r, pick(r, "recompose", key, "ssim")) for r in results]) for key, _ in STRATEGIES}
    rec["recovered"]["greedy_final"] = mean([recovered(r, r["recompose"]["greedy"][-1]["ssim"]) for r in results if pick(r, "recompose", "greedy")])
    rec["recovered"]["ceiling_58_glyphs"] = mean([recovered(r, pick(r, "recompose", "ceiling", "58_glyphs", "ssim")) for r in results])
    for key, _ in GEN:
        rows = [pick(r, "generate", key) for r in results]
        rows = [x for x in rows if isinstance(x, dict) and "error" not in x]
        s["generate"][key] = {"n": len(rows), **{m: mean([x.get(m) for x in rows]) for m in
                              ("layout_corr", "subject_iou", "judge_subject", "judge_layout", "judge_overall")},
                              "subject_found": mean([float(x.get("subject_found", False)) for x in rows])}
    s["generate2"] = {}
    for key, _ in GEN2:
        rows = [pick(r, "generate2", key) for r in results]
        rows = [x for x in rows if isinstance(x, dict) and "error" not in x]
        s["generate2"][key] = {"n": len(rows), **{m: mean([x.get(m) for x in rows]) for m in
                               ("layout_corr", "subject_iou", "judge_subject", "judge_layout", "judge_overall")},
                               "subject_found": mean([float(x.get("subject_found", False)) for x in rows])}
    a = {"adaptive": {}, "jpeg": {}}
    for cols in (58, 116, 232):
        for levels in (5, 10):
            k = "%d_%d" % (cols, levels)
            a["adaptive"][k] = mean([pick(r, "analysis", "adaptive", k) for r in results])
    for tag in ("base", "tiles2", "tiles4"):
        a["jpeg"][tag] = {"bytes": mean([pick(r, "analysis", "jpeg", tag, "bytes") for r in results]),
                          "ssim": mean([pick(r, "analysis", "jpeg", tag, "ssim") for r in results])}
    for tag in ("faithful", "high"):
        a["figure_ground_" + tag] = mean([pick(r, "analysis", "figure_ground_" + tag) for r in results])
    s["analysis"] = a
    for k in sorted(set(kind.values())):
        group = [r for r in results if kind.get(r["name"]) == k]
        s["by_kind"][k] = {"n": len(group),
                           "high_subject_match": mean([float(pick(r, "high", "read_subject_match")) for r in group if pick(r, "high", "read_subject_match") is not None]),
                           "low_subject_match": mean([float(pick(r, "low", "read_subject_match")) for r in group if pick(r, "low", "read_subject_match") is not None]),
                           "high_scene": mean([pick(r, "high", "read_scene_score") for r in group]),
                           "low_scene": mean([pick(r, "low", "read_scene_score") for r in group]),
                           "high_tone_r": mean([pick(r, "high", "tone_r") for r in group]),
                           "faithful_tone_r": mean([pick(r, "faithful", "tone_r") for r in group]),
                           "drawing_key_judge": mean([pick(r, "generate2", "zooms_decoded_caption", "judge_overall") for r in group]),
                           "caption_judge": mean([pick(r, "generate", "caption_only", "judge_overall") for r in group])}
    return s


def greedy_average(results, points=(0, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000)):
    """Average SSIM reached by the greedy zoom order after each byte budget (beyond the base drawing)."""
    out = []
    for b in points:
        vals = []
        for r in results:
            curve = pick(r, "recompose", "greedy")
            if not curve:
                continue
            base = curve[0]["bytes"]
            best = [c["ssim"] for c in curve if c["bytes"] - base <= b]
            vals.append(best[-1] if best else curve[0]["ssim"])
        out.append((b, float(np.mean(vals)) if vals else None))
    return out


def svg_curve(curve, s):
    W, H, pad = 640, 300, 48
    xs = [math_log(b) for b, _ in curve]
    lo = min([v for v in [s["recompose"]["flat"]] + [y for _, y in curve] if v is not None]) - 0.02
    hi = max([v for v in [s["recompose"]["ceiling_232"]] + [y for _, y in curve] if v is not None]) + 0.02
    X = lambda x: pad + (x - xs[0]) / (xs[-1] - xs[0]) * (W - 2 * pad)
    Y = lambda y: H - pad - (y - lo) / (hi - lo) * (H - 2 * pad)
    parts = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="Recomposition quality against bytes sent">' % (W, H)]
    for label, val, dash in (("232-column ceiling", s["recompose"]["ceiling_232"], "4 3"), ("58 columns, 5 glyphs (one drawing's ceiling)", s["recompose"]["ceiling_58_glyphs"], "2 3"),
                             ("flat grey", s["recompose"]["flat"], "1 4")):
        if val is not None:
            parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="ref" stroke-dasharray="%s"/>' % (pad, W - pad, Y(val), Y(val), dash))
            parts.append('<text x="%d" y="%.1f" class="lab">%s %.2f</text>' % (W - pad - 4, Y(val) - 4, html.escape(label), val))
    pts = " ".join("%.1f,%.1f" % (X(x), Y(y)) for x, (_, y) in zip(xs, curve) if y is not None)
    parts.append('<polyline points="%s" class="line"/>' % pts)
    for x, (b, y) in zip(xs, curve):
        if y is not None:
            parts.append('<circle cx="%.1f" cy="%.1f" r="3.5" class="dot"><title>+%d bytes: SSIM %.3f</title></circle>' % (X(x), Y(y), b, y))
            parts.append('<text x="%.1f" y="%d" class="tick">%s</text>' % (X(x), H - pad + 16, "+0" if b == 0 else "+%gk" % (b / 1000)))
    parts.append('<text x="%d" y="%d" class="axis">bytes of zooms sent after the base drawing (greedy order)</text>' % (W // 2, H - 8))
    parts.append('<text x="12" y="%d" class="axis" transform="rotate(-90 12 %d)">SSIM vs photo</text>' % (H // 2, H // 2))
    parts.append("</svg>")
    return "".join(parts)


def math_log(b):
    return np.log10(b + 250)


def bars(items, maximum, fmt="%.2f"):
    rows = []
    for label, val in items:
        w = 0 if val is None else max(0.0, min(1.0, val / maximum)) * 100
        rows.append('<div class="bar"><span class="bl">%s</span><span class="bt"><span class="bf" style="width:%.1f%%"></span></span>'
                    '<span class="bv">%s</span></div>' % (html.escape(label), w, "–" if val is None else fmt % val))
    return "".join(rows)


def thumb(path, side=220, fmt="JPEG"):
    if not os.path.exists(path):
        return ""
    im = Image.open(path).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, fmt, quality=70)
    return '<img src="data:image/%s;base64,%s" alt="">' % (fmt.lower(), base64.b64encode(buf.getvalue()).decode())


def build(out, names, chance, kind):
    results = load(out, names)
    if not results:
        print("no results yet")
        return
    s = summarize(results, chance, kind)
    s["greedy_curve"] = greedy_average(results)
    json.dump(s, open(os.path.join(out, "summary.json"), "w"), indent=1)
    D = s["drawings"]
    gallery = []
    for r in results:
        d = os.path.join(out, r["name"])
        high = open(os.path.join(d, "high.txt")).read() if os.path.exists(os.path.join(d, "high.txt")) else ""
        gens = "".join('<figure>%s<figcaption>%s<br>layout %s · judge %s</figcaption></figure>' % (
            thumb(os.path.join(d, "gen_%s.jpg" % k), 150), html.escape(lab),
            "–" if pick(r, "generate2", k, "layout_corr") is None else "%.2f" % pick(r, "generate2", k, "layout_corr"),
            "–" if pick(r, "generate2", k, "judge_overall") is None else "%.0f/10" % pick(r, "generate2", k, "judge_overall")) for k, lab in GEN2)
        gallery.append(
            '<section class="photo"><h3>%s <small>%s</small></h3><p class="cap">%s</p><div class="row">'
            '<figure>%s<figcaption>the photo</figcaption></figure>'
            '<figure><pre>%s</pre><figcaption>high drawing, as sent · Muse read: <b>%s</b> (scene %s/10)</figcaption></figure>'
            '<figure>%s<figcaption>rebuilt from drawing + all zooms (D0) · SSIM %.2f</figcaption></figure></div>'
            '<div class="row gens">%s</div></section>' % (
                html.escape(r["name"]), html.escape(kind.get(r["name"], "")), html.escape(r.get("caption", "")),
                thumb(os.path.join(d, "photo.jpg"), 260), html.escape(high), html.escape(str(pick(r, "high", "read_subject") or "–")),
                "–" if pick(r, "high", "read_scene_score") is None else "%.0f" % pick(r, "high", "read_scene_score"),
                thumb(os.path.join(d, "d0_greedy.png"), 260, "PNG"),
                (pick(r, "recompose", "greedy") or [{"ssim": 0}])[-1]["ssim"], gens))
    rec = s["recompose"]
    page = TEMPLATE % {
        "n": s["photos"],
        "metrics_rows": "".join(
            "<tr><td>%s</td>%s</tr>" % (label, "".join("<td>%s</td>" % ("–" if D[t].get(m) is None else f % (100 * D[t][m] if "%%" in f else D[t][m]))
                                                       for t in ("low", "faithful", "high")))
            for label, m, f in [("Muse names the subject (blind)", "read_subject_match", "%.0f%%"),
                                ("Muse's scene vs the caption (0-10)", "read_scene_score", "%.1f"),
                                ("Tone: correlation with the photo", "tone_r", "%.2f"), ("Tone: SSIM (cells)", "tone_ssim", "%.2f"),
                                ("Edges: F1", "edge_f1", "%.2f"), ("Subject shape: IoU", "subject_iou", "%.2f"),
                                ("Subject contrast with its surroundings", "contrast", "%.2f"), ("Background ink", "background_ink", "%.2f"),
                                ("Bytes", "bytes", "%.0f"), ("Bytes gzipped", "gzip", "%.0f")]).replace(
            ">%s<" % "nan", ">–<"),
        "match_low": 100 * (D["low"]["read_subject_match"] or 0), "match_high": 100 * (D["high"]["read_subject_match"] or 0),
        "curve": svg_curve(s["greedy_curve"], s),
        "rec_bars": bars([(lab, rec[k]["ssim"]) for k, lab in STRATEGIES] + [("Greedy, all zooms", rec["greedy_final"]["ssim"])] +
                         [("Ceiling: " + lab, rec["ceiling_" + k]) for k, lab in CEILINGS] + [("Flat grey", rec["flat"])], 1.0),
        "rec_bytes": "".join("<tr><td>%s</td><td>%.0f</td><td>%.0f%%</td></tr>" % (lab, rec[k]["bytes"] or 0, 100 * (rec["recovered"][k] or 0))
                             for k, lab in STRATEGIES) + "<tr><td>Greedy, all zooms</td><td>%.0f</td><td>%.0f%%</td></tr>" % (
                                 rec["greedy_final"]["bytes"] or 0, 100 * (rec["recovered"]["greedy_final"] or 0)),
        "gen2_rows": "".join("<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            lab, s["generate2"][k]["n"], *[("–" if s["generate2"][k][m] is None else f % s["generate2"][k][m]) for m, f in
                                           (("layout_corr", "%.2f"), ("subject_iou", "%.2f"), ("subject_found", "%.2f"),
                                            ("judge_subject", "%.1f"), ("judge_layout", "%.1f"), ("judge_overall", "%.1f"))])
            for k, lab in GEN2),
        "adaptive_rows": "".join("<tr><td>%d columns</td>%s</tr>" % (cols, "".join(
            "<td>%s</td>" % ("–" if v is None else "%.3f" % v) for v in (
                rec["ceiling_%d_glyphs" % cols] if cols != 58 else rec["ceiling_58_glyphs"],
                s["analysis"]["adaptive"]["%d_5" % cols], s["analysis"]["adaptive"]["%d_10" % cols],
                rec["ceiling_%d" % cols] if ("ceiling_%d" % cols) in rec else None))) for cols in (58, 116, 232)),
        "jpeg_rows": "".join("<tr><td>%s</td><td>%.0f</td><td>%.3f</td><td>%.3f</td></tr>" % (
            lab, s["analysis"]["jpeg"][j]["bytes"] or 0, rec[k]["ssim"] or 0, s["analysis"]["jpeg"][j]["ssim"] or 0)
            for lab, k, j in (("Base drawing", "faithful", "base"), ("+ every 2x2 zoom", "tiles2", "tiles2"), ("+ every 2x2 and 4x4 zoom", "tiles4", "tiles4"))),
        "fg_faithful": s["analysis"]["figure_ground_faithful"] or 0, "fg_high": s["analysis"]["figure_ground_high"] or 0,
        "gen_rows": "".join("<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            lab, s["generate"][k]["n"], *[("–" if s["generate"][k][m] is None else f % s["generate"][k][m]) for m, f in
                                          (("layout_corr", "%.2f"), ("subject_iou", "%.2f"), ("subject_found", "%.2f"),
                                           ("judge_subject", "%.1f"), ("judge_layout", "%.1f"), ("judge_overall", "%.1f"))])
            for k, lab in GEN),
        "chance": chance,
        "kind_rows": "".join("<tr><td>%s</td><td>%d</td>%s</tr>" % (k, v["n"], "".join(
            "<td>%s</td>" % ("–" if v[m] is None else f % (100 * v[m] if "match" in m else v[m])) for m, f in
            (("low_subject_match", "%.0f%%"), ("high_subject_match", "%.0f%%"), ("low_scene", "%.1f"), ("high_scene", "%.1f"),
             ("faithful_tone_r", "%.2f"), ("high_tone_r", "%.2f"), ("caption_judge", "%.1f"), ("drawing_key_judge", "%.1f"))))
            for k, v in s["by_kind"].items()),
        "seconds_low": s["seconds"]["low"] or 0, "seconds_high": s["seconds"]["high"] or 0,
        "gallery": "".join(gallery),
    }
    open(os.path.join(out, "report.html"), "w").write(page)
    print("report:", os.path.join(out, "report.html"), "| photos:", len(results))


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Photo drawing evals</title>
<style>
:root { --bg:#f7f7f5; --fg:#1d1d1f; --quiet:#6b6b70; --line:#dcdcd6; --card:#fff; --accent:#2f6fdf; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141416; --fg:#ececf0; --quiet:#9a9aa3; --line:#2f2f35; --card:#1d1d21; --accent:#7aa7ff; } }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0; padding:24px 16px; }
main { max-width:1180px; margin:0 auto; }
h1 { font-size:26px; margin:0 0 4px; } h2 { margin:36px 0 8px; font-size:20px; } h3 { margin:0 0 4px; font-size:16px; }
small, .cap, figcaption, .note { color:var(--quiet); } .note { font-size:13px; }
table { border-collapse:collapse; width:100%%; background:var(--card); font-size:14px; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:right; } th:first-child, td:first-child { text-align:left; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }
.big { font-size:28px; font-weight:650; }
.bar { display:grid; grid-template-columns: 300px 1fr 56px; gap:8px; align-items:center; font-size:13px; margin:3px 0; }
.bt { background:var(--line); height:12px; border-radius:6px; overflow:hidden; } .bf { display:block; height:100%%; background:var(--accent); }
.bv { text-align:right; font-variant-numeric:tabular-nums; }
svg .line { fill:none; stroke:var(--accent); stroke-width:2.5; } svg .dot { fill:var(--accent); }
svg .ref { stroke:var(--quiet); stroke-width:1; } svg text { fill:var(--quiet); font:11px system-ui; }
svg .lab { text-anchor:end; } svg .tick { text-anchor:middle; } svg .axis { text-anchor:middle; font-size:12px; }
.photo { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:14px 0; }
.row { display:flex; gap:14px; flex-wrap:wrap; align-items:flex-start; } figure { margin:0; } figure img { border-radius:6px; display:block; }
pre { font:6.5px/7.6px Menlo, monospace; margin:0; background:#fff; color:#000; padding:6px; border-radius:6px; overflow:auto; max-width:100%%; }
.gens figure img { width:150px; } .gens figcaption { max-width:150px; font-size:12px; } @media (max-width:640px) { .bar { grid-template-columns: 1fr 1fr 48px; } }
</style></head><body><main>
<h1>How good are the photo drawings?</h1>
<p class="note">%(n)d photos (every sample in the simulator). Low = the outline drawing; high = the tonal drawing as sent (subject
emphasised, light background); faithful = the same segmentation on one tone scale. Muse judges see only the drawing.</p>
<div class="cards">
<div class="card"><div class="big">%(match_low).0f%%</div>Muse names the subject from the <b>low</b> outline</div>
<div class="card"><div class="big">%(match_high).0f%%</div>from the <b>high</b> tonal drawing</div>
<div class="card"><div class="big">%(seconds_low).0f s / %(seconds_high).0f s</div>to draw low / high (average)</div>
</div>
<h2>1. The drawings, measured against the photo</h2>
<table><tr><th></th><th>low (outline)</th><th>high, faithful tones</th><th>high, as sent</th></tr>%(metrics_rows)s</table>
<h3 style="margin-top:18px">By kind of photo</h3>
<table><tr><th></th><th>photos</th><th>subject named (low)</th><th>(high)</th><th>scene (low)</th><th>(high)</th><th>tone r faithful</th><th>as sent</th><th>recreated from caption</th><th>from all zooms decoded + caption</th></tr>%(kind_rows)s</table>
<h2>2. Recomposition: rebuilding the photo from text alone</h2>
<p class="note">D0, no model: every drawing decoded back into grey with its tone legend (a ~40-byte line saying which grey each
glyph stands for, counted in the bytes), zooms pasted over their boxes, scored with SSIM against the photo at 232 px wide.
The greedy order sends next the zoom that removes the most error per byte.</p>
%(curve)s
%(rec_bars)s
<table style="margin-top:12px"><tr><th>Strategy</th><th>bytes (average)</th><th>of the way from flat grey to the 232-column ceiling</th></tr>%(rec_bytes)s</table>
<h3 style="margin-top:18px">What limits the rebuild: tones or resolution?</h3>
<p class="note">SSIM of the photo itself averaged into the grid and snapped to: the 5 shading glyphs' ink (no legend),
its own best 5 greys, its own best 10 greys, or any grey. Our drawings reach 0.603 (base) to 0.766 (all zooms).</p>
<table><tr><th>Grid</th><th>5 glyphs, fixed ink</th><th>5 adaptive greys</th><th>10 adaptive greys</th><th>any grey</th></tr>%(adaptive_rows)s</table>
<h3 style="margin-top:18px">For scale: a JPEG with the same number of bytes</h3>
<table><tr><th>Text sent</th><th>bytes</th><th>SSIM, text drawings</th><th>SSIM, greyscale JPEG of the same size</th></tr>%(jpeg_rows)s</table>
<p class="note">Subject ink ÷ background ink (how much the subject stands out overall): faithful %(fg_faithful).2f, as sent %(fg_high).2f.</p>
<h3 style="margin-top:18px">Recomposed by Muse Image (D1)</h3>
<p class="note">Layout = correlation of 16-column grey thumbnails with the photo (unrelated photos: %(chance).2f). Subject = SAM's box
for the subject on the photo vs on the recreation (IoU), and how often SAM finds it at all. Judge = Muse comparing photo and
recreation (0-10). "Decoded" = the drawing turned back into grey (D0) before the image model sees it.</p>
<table><tr><th>Input</th><th>n</th><th>layout r</th><th>subject IoU</th><th>subject found</th><th>judge: subject</th><th>layout</th><th>overall</th></tr>%(gen2_rows)s</table>
<h2>3. Every photo</h2>
%(gallery)s
</main></body></html>"""
