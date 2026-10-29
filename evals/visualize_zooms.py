"""Step by step through the server-chosen zooms (evac/zooms.py) for a few photos, with every SMS that would be sent.

    .venv/bin/python evals/visualize_zooms.py [--only Dog,Mt_Fuji]   -> out/eval/zooms_tree.html

Runs the real thing (Muse lists the parts, SAM segments them; SAM answers are cached) and records each round of the
zoom choice: the error map, every candidate box with what it would remove, and which one was kept.
"""
import argparse
import html
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import focus_zooms as F  # noqa: E402
import photo_eval as E  # noqa: E402
import visualize_tree as V  # noqa: E402
from evac import tonal, zooms  # noqa: E402

EXAMPLES = ["Dog", "Mt_Fuji", "Forest_in_Mist", "Isles"]
h = html.escape


def grey_img(a):
    return Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))


def box_px(pct, size, k=1.0):
    return tuple(v * k for v in (pct[0] * size[0] / 100, pct[1] * size[1] / 100, pct[2] * size[0] / 100, pct[3] * size[1] / 100))


def subject_overlay(full, mask):
    g = np.asarray(full.convert("L").convert("RGB"), float) * 0.5 + 60
    if mask is not None:
        g[mask] = g[mask] * 0.35 + np.array([230, 73, 128]) * 0.65
    return Image.fromarray(g.clip(0, 255).astype(np.uint8))


def error_view(full, rnd, weight):
    """The error the receiver would still see (red = worse, weighted), with every candidate box of the round."""
    e = rnd["error"] * weight
    e = e / max(1e-6, np.quantile(e, 0.99))
    g = np.asarray(full.convert("L").resize(e.shape[::-1]), float)[..., None] * np.ones(3) * 0.45 + 70
    heat = np.clip(e, 0, 1)[..., None]
    out = g * (1 - heat) + np.array([220, 40, 40]) * heat
    im = Image.fromarray(out.clip(0, 255).astype(np.uint8)).resize((600, round(600 * e.shape[0] / e.shape[1])), Image.NEAREST)
    d = ImageDraw.Draw(im)
    seen = {}
    for t in sorted(rnd["tried"], key=lambda t: t["kept"]):
        if t["pct"] in seen and not t["kept"]:
            continue
        seen[t["pct"]] = True
        x0, y0, x1, y1 = box_px(t["pct"], im.size)
        col = (40, 200, 90) if t["kept"] else (90, 160, 255)
        d.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], outline=col, width=4 if t["kept"] else 2)
    return im


def sms_parts(text, n=zooms.SMS_PART):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def example(key, path, base=None, details=None):
    full = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    full.thumbnail((2048, 2048))
    work = full.copy()
    work.thumbnail((1600, 1600))
    base = base or tonal.draw(work, check=False)
    plan = zooms.plan(full, base, details=details)
    mask = zooms.subject_mask(base, full.size)
    L, gray, named, main, root = base["layers"]
    ref = plan["ref"]
    spans = (0, 0)
    if mask is not None:
        ys, xs = np.nonzero(mask)
        spans = ((xs.max() - xs.min() + 1) / mask.shape[1], (ys.max() - ys.min() + 1) / mask.shape[0])
    tiles, tbytes = F.tiles_at(full, base, plan["bytes"])
    pic, pbytes = F.picture_at(full.convert("L"), plan["bytes"])
    canvases = [plan["base_canvas"]]
    for z in plan["zooms"]:  # rebuild after each zoom, as the receiver would
        c = canvases[-1].copy()
        _, rows, leg = zooms.draw_zoom(full, z["pct"], z["cols"], mask if plan["mode"] == "subject" else None)
        zooms.paste(c, zooms.decode(rows, leg, z["cols"], transparent=plan["mode"] == "subject"), box_px(z["pct"], full.size), full.size)
        canvases.append(c)
    messages = [("Base drawing", plan["base_text"])] + [("Zoom %d" % i, z["text"]) for i, z in enumerate(plan["zooms"], 1)]
    return {"key": key, "names": base["parts"], "details": plan["details"], "canvas": plan["canvas"], "base_canvas": plan["base_canvas"], "main": base["main"], "mode": plan["mode"],
            "area": float(mask.mean()) if mask is not None else 0.0, "spans": spans,
            "photo": V.b64(full), "parts_img": V.b64(V.part_overlay(work, L, named, root)),
            "subject_img": V.b64(subject_overlay(work, zooms.subject_mask(base, work.size))),
            "base_text": plan["base_text"], "base_bytes": len(plan["base_text"].encode()),
            "base_img": V.b64(grey_img(plan["base_canvas"]).resize(full.size)), "base_ssim": zooms.ssim(plan["base_canvas"], ref),
            "rounds": [{"img": V.b64(error_view(full, r, plan["weight"])),
                        "tried": sorted(r["tried"], key=lambda t: -t["removed"])} for r in plan["rounds"]],
            "zooms": [dict(z, after=V.b64(grey_img(c).resize(full.size)), ssim=zooms.ssim(c, ref))
                      for z, c in zip(plan["zooms"], canvases[1:])],
            "final_ssim": zooms.ssim(plan["canvas"], ref), "bytes": plan["bytes"],
            "tiles": V.b64(grey_img(tiles).resize(full.size)), "tiles_ssim": zooms.ssim(tiles, ref), "tiles_bytes": tbytes,
            "picture": V.b64(pic) if pic is not None else "", "picture_bytes": pbytes,
            "picture_ssim": zooms.ssim(zooms.lightness(pic, zooms.CANVAS_W, ref.shape[0]), ref) if pic is not None else 0,
            "grey": V.b64(full.convert("L")), "messages": messages}


def candidates_table(tried, keep):
    rows = []
    for t in tried[:12]:
        per_kb = 1000 * t["removed"] / t["bytes"]
        verdict = "kept" if t["kept"] else ("below %d%%" % round(100 * keep) if t["removed"] < keep else "less per byte")
        rows.append("<tr class='%s'><td>%d %d %d %d</td><td>%d</td><td>%s</td><td>%.1f%%</td><td>%.2f%%</td><td>%s</td></tr>" % (
            "kept" if t["kept"] else "", *t["pct"], t["cols"], format(t["bytes"], ","), 100 * t["removed"], 100 * per_kb, verdict))
    more = "<tr><td colspan=6>… %d more</td></tr>" % (len(tried) - 12) if len(tried) > 12 else ""
    return ("<table><tr><th>box (%% of photo)</th><th>cols</th><th>bytes</th><th>removes</th><th>per KB</th><th></th></tr>%s%s</table>"
            % ("".join(rows), more))


def sms_block(messages):
    out, total = [], 0
    for title, text in messages:
        parts = sms_parts(text)
        total += len(parts)
        bubbles = "".join('<div class="sms"><span class="pn">%d/%d</span><pre>%s</pre></div>' % (i, len(parts), h(p))
                          for i, p in enumerate(parts, 1))
        out.append("<details><summary><b>%s</b> · %s characters · %d SMS</summary><div class='bubbles'>%s</div></details>" % (
            h(title), format(len(text), ","), len(parts), bubbles))
    return total, "".join(out)


def section(x, before="", title=None):
    """Same shape as tree.html: the photo, then the server's steps, what is sent, what the receiver rebuilds, and what
    it is compared with. Every zoom is a node; every SMS is listed."""
    keep = zooms.KEEP
    why = ("covers %.0f%% of the photo, spans %.0f%% × %.0f%% of the frame" % (100 * x["area"], 100 * x["spans"][0], 100 * x["spans"][1]))
    steps = [
        V.node("1 · Muse lists the parts", "<ol>%s</ol>" % "".join("<li>%s</li>" % h(n) for n in x["names"]),
               "%d parts, no fixed number; duplicates and specks dropped" % len(x["names"])),
        V.node("2 · SAM segments each part", V.img(x["parts_img"]), "one colour per part"),
        V.node("3 · Subject or scene?", V.img(x["subject_img"]),
               "<b>%s mode</b> · main part: %s · %s. Subject mode needs 1–60%% and not the whole frame." % (
                   x["mode"], h(x["main"] or "none"), why)),
        V.node("3b · What matters", "<ol>%s</ol>" % "".join("<li>%s</li>" % h(n) for n in x.get("details") or []) if x.get("details") else "<p>(nothing named)</p>",
               "Muse names the 1–3 details that decide the photo's meaning; SAM finds them; only their error counts for zooms"),
        V.node("4 · Base drawing, 58 columns", '<pre class="art">%s</pre>' % h(x["base_text"]),
               "%s · %s bytes" % ("emphasised (subject stands out, background light)" if x["mode"] == "subject" else "faithful (one tone scale)",
                                  format(x["base_bytes"], ","))),
    ]
    for i, r in enumerate(x["rounds"]):
        z = x["zooms"][i] if i < len(x["zooms"]) else None
        table = "<details><summary>%d candidates tried</summary>%s</details>" % (len(r["tried"]), candidates_table(r["tried"], keep))
        if z:
            steps.append(V.node("%d · Zoom %d: where is it still wrong?" % (5 + i, i + 1), V.img(r["img"]) + table +
                                '<pre class="art">%s</pre>' % h(z["text"]),
                                "red = error left; blue = candidates; <b>green = kept</b> · %d × %d cells · %s bytes · removes %.0f%% of the base's error"
                                % (z["cols"], z["rows"], format(z["bytes"], ","), 100 * z["removed"]), "good"))
        else:
            best = max((t["removed"] for t in r["tried"]), default=0)
            steps.append(V.node("%d · Stop" % (5 + i), V.img(r["img"]) + table,
                                "no candidate removes %d%% of the base's error (best %.1f%%), so nothing more is sent" % (round(100 * keep), 100 * best)))
    total_sms, sms = sms_block(x["messages"])
    payload = V.node("The text that is sent", "<p class='big'>%s bytes · %d messages · %d SMS</p>%s" % (
        format(x["bytes"], ","), len(x["messages"]), total_sms, sms),
        "click a message to see its SMS. Each SMS holds 67 characters (UCS-2: these glyphs aren't in the basic SMS alphabet); "
        "the phone joins them into one bubble, but Messages' proportional font doesn't keep the rows aligned", "pay")
    receiver = [V.node("A · Decode the base drawing", V.img(x["base_img"]), "glyph → legend grey · SSIM %.2f" % x["base_ssim"])]
    for i, z in enumerate(x["zooms"]):
        receiver.append(V.node("%s · Paste zoom %d" % ("BCDEFGHIJ"[i], i + 1), V.img(z["after"]),
                               "over box %d %d %d %d%s · SSIM <b>%.2f</b>" % (*z["pct"], " (blank = keep what's there)" if x["mode"] == "subject" else "", z["ssim"]),
                               "good" if i == len(x["zooms"]) - 1 else ""))
    compare = "".join([
        V.node("Ground truth", V.img(x["grey"]), "the photo in greyscale"),
        V.node("Old way, same bytes", V.img(x["tiles"]), "faithful base + 2×2/4×4 tiles · %s bytes · SSIM %.2f" % (format(x["tiles_bytes"], ","), x["tiles_ssim"])),
        V.node("A picture, same bytes", V.img(x["picture"]) if x["picture"] else "", "%s bytes · SSIM %.2f" % (format(x["picture_bytes"], ","), x["picture_ssim"])),
    ])
    return ('<section id="%s"><h2>%s</h2><p class="cap">%s mode · %d zoom%s · %s bytes · %d SMS · SSIM %.2f (old tiles %.2f, picture %.2f)</p><div class="tree">'
            '<div class="root">%s<div class="ns">the photo</div></div>%s'
            '<div class="branch"><div class="bl">Server (has the photo)</div><div class="row">%s</div></div>'
            '<div class="branch mid"><div class="row one">%s</div></div>'
            '<div class="branch"><div class="bl">Receiver (has only the text)</div><div class="row">%s</div></div>'
            '<div class="branch"><div class="bl">Compared with</div><div class="row">%s</div></div>'
            '</div></section>') % (x["key"], title or h(x["key"].replace("_", " ")), x["mode"].capitalize(), len(x["zooms"]),
                                   "" if len(x["zooms"]) == 1 else "s", format(x["bytes"], ","), total_sms, x["final_ssim"],
                                   x["tiles_ssim"], x["picture_ssim"], V.img(x["photo"]), before, "".join(steps), payload, "".join(receiver), compare)


EXTRA_CSS = """
table { border-collapse:collapse; font:12px Menlo, monospace; } td, th { padding:2px 8px; border-bottom:1px solid var(--line); text-align:right; }
th { color:var(--quiet); font-weight:500; } tr.kept td { color:var(--good); font-weight:700; }
details { margin:6px 0; } summary { cursor:pointer; } .bubbles { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
.sms { background:#e9e9eb; color:#000; border-radius:14px; padding:6px 10px; max-width:260px; position:relative; }
.sms pre { margin:0; font:12px/1.3 -apple-system, system-ui, sans-serif; white-space:pre-wrap; word-break:break-all; } .pn { font-size:10px; color:#666; }
.node.pay { max-width:1200px; } .node details { margin:6px 0; } .node table { margin-top:4px; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    want = a.only.split(",") if a.only else EXAMPLES
    items = {k: p for k, _, p in E.dataset()}
    xs = [example(k, items[k]) for k in want]
    style = V.PAGE.split("<style>")[1].split("</style>")[0].replace("%%", "%")
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Chosen zooms</title><style>%s%s</style></head><body><main><h1>How the zooms are chosen</h1>"
            "<p class='intro'>The server makes the base drawing, then repeatedly asks: if I sent a closer drawing of this area, how much "
            "of what the receiver gets wrong would it fix? It keeps a zoom only if it fixes at least %d%%, and stops when nothing does. "
            "Error is 1 − SSIM per pixel against the greyscale photo at 232 px wide.</p><nav>%s</nav>%s</main></body></html>") % (
        style, EXTRA_CSS, round(100 * zooms.KEEP),
        "".join('<a href="#%s">%s</a>' % (x["key"], h(x["key"].replace("_", " "))) for x in xs), "".join(section(x) for x in xs))
    out = os.path.join(E.OUT, "zooms_tree.html")
    open(out, "w").write(page)
    print(out, len(page) // 1000, "KB")
    lines = []
    for x in xs:  # every SMS, as plain text
        lines.append("=" * 20 + " %s (%s mode)" % (x["key"], x["mode"]))
        for title, text in x["messages"]:
            parts = sms_parts(text)
            for i, p in enumerate(parts, 1):
                lines.append("--- %s · SMS %d/%d" % (title, i, len(parts)))
                lines.append(p)
    open(os.path.join(E.OUT, "zooms_sms.txt"), "w").write("\n".join(lines) + "\n")
    for x in xs:
        print(x["key"], x["mode"], "zooms", len(x["zooms"]), "bytes", x["bytes"], "ssim %.3f tiles %.3f picture %.3f" % (
            x["final_ssim"], x["tiles_ssim"], x["picture_ssim"]), "sms", sum(len(sms_parts(t)) for _, t in x["messages"]))


if __name__ == "__main__":
    main()
