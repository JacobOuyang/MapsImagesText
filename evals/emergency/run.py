"""Run the current photo pipeline on the emergency pictures and lay out everything it would send.

    .venv/bin/python evals/emergency/run.py [--only cpr_hands,pill]   -> out/eval/emergency.html, out/eval/emergency/<name>/

Per picture: the low reply (photo.run: caption + SMS silhouette, WhatsApp outline checked by Muse), then the high
drawing and the zooms the server chooses (tonal.draw + zooms.plan), every SMS of both, and the rebuild from the text.
The question in dataset.json is shown next to each picture; nothing is scored yet.
"""
import argparse
import html
import io
import json
import os
import pickle
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402
import visualize_tree as V  # noqa: E402
import visualize_zooms as VZ  # noqa: E402
from evac import photo, tonal, zooms  # noqa: E402

PICS = os.path.join(HERE, "..", "..", "sim", "samples", "emergency")
OUT = os.path.join(E.OUT, "emergency")
h = html.escape


def one(item):
    name = item["name"]
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    cached = os.path.join(d, "result.pickle")
    if os.path.exists(cached):  # delete it (or out/eval/emergency/) to run the models again
        return pickle.load(open(cached, "rb"))
    path = os.path.join(PICS, name + ".jpg")
    data = open(path, "rb").read()
    t = time.time()
    low = photo.run(data)
    low_s = time.time() - t
    work = Image.open(path).convert("RGB")
    t = time.time()
    base = tonal.draw(work, check=False)
    details = zooms.focus(work)
    x = VZ.example(name, path, base, details)
    high_s = time.time() - t
    open(os.path.join(d, "low_sms.txt"), "w").write(low["sms"])
    open(os.path.join(d, "low_whatsapp.txt"), "w").write(low["mono"])
    open(os.path.join(d, "high_and_zooms.txt"), "w").write("\n\n".join(t for _, t in x["messages"]))
    low.pop("small", None)
    x.update(item=item, low=low, low_seconds=low_s, high_seconds=high_s)
    pickle.dump(x, open(cached, "wb"))
    return x


def low_branch(x):
    low, it = x["low"], x["item"]
    sms_parts = VZ.sms_parts(low["sms"])
    check = low.get("check") or {}
    q = "<p><b>%s</b></p><ol type='A'>%s</ol>" % (h(it["question"]), "".join(
        "<li%s>%s</li>" % (" class='ans'" if i == it["answer"] else "", h(c)) for i, c in enumerate(it["choices"])))
    nodes = "".join([
        V.node("The question someone needs answered", q, "%s · the right answer is highlighted" % h(it["category"])),
        V.node("Low · SMS (sent first)", '<div class="bubbles">%s</div>' % "".join(
            '<div class="sms"><span class="pn">%d/%d</span><pre>%s</pre></div>' % (i, len(sms_parts), h(p)) for i, p in enumerate(sms_parts, 1)),
            "%s characters · %d SMS · %s" % (
                format(len(low["sms"]), ","), len(sms_parts),
                ("caption + a 24-column silhouette of “%s” (%s)" if low.get("sms_shape") else "caption only: the silhouette of “%s” was dropped (%s)")
                % (h(low.get("object") or ""), h(low.get("sms_shape_why") or ""))), "pay"),
        V.node("Low · WhatsApp outline", '<pre class="art">%s</pre>' % h(low["mono"]),
               "%s bytes · Muse, shown only the drawing, recognised: %s · %.0f s" % (
                   format(low["mono_bytes"], ","), h(", ".join(check.get("recognised") or []) or "nothing"), x["low_seconds"])),
    ])
    return '<div class="branch"><div class="bl">Low data: what is sent first</div><div class="row">%s</div></div>' % nodes


SCORED = ["none", "photo", "sms", "drawing", "rebuild_base", "rebuild", "sms+rebuild"]


def summary(xs):
    sp = os.path.join(OUT, "scores.json")
    scores = json.load(open(sp)) if os.path.exists(sp) else {}
    rows = []
    for x in xs:
        low_sms = len(VZ.sms_parts(x["low"]["sms"]))
        high_sms = sum(len(VZ.sms_parts(t)) for _, t in x["messages"])
        sc = scores.get(x["key"], {})
        marks = "".join("<td class='%s'>%s</td>" % (("ok", "✓") if sc[c]["right"] else ("no", "✗ " + h(str(sc[c]["answer"])[:14])))
                        if c in sc else "<td></td>" for c in SCORED)
        rows.append("<tr><td><a href='#%s'>%s</a></td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%d</td><td>%.2f</td>%s</tr>" % (
            x["key"], h(x["key"].replace("_", " ")), h(x["item"]["category"]), "kept" if x["low"].get("sms_shape") else "dropped", low_sms,
            len(x["zooms"]), format(x["bytes"], ","), high_sms, x["final_ssim"], marks))
    if scores:
        hard = [k for k, v in scores.items() if not v["none"]["right"]]
        tot = "".join("<td><b>%d/%d</b></td>" % (sum(v[c]["right"] for v in scores.values()), len(scores)) for c in SCORED)
        hrd = "".join("<td><b>%d/%d</b></td>" % (sum(scores[k][c]["right"] for k in hard), len(hard)) for c in SCORED)
        rows.append("<tr class='tot'><td colspan=9>all questions</td>%s</tr><tr class='tot'><td colspan=9>questions not guessable without seeing anything</td>%s</tr>" % (tot, hrd))
    return ("<p class='intro'>Muse answers each question from one input at a time, marked right or wrong: <b>none</b> = the question alone "
            "(guessable?), <b>photo</b> = the picture, <b>sms</b> = the low SMS as sent, <b>drawing</b> = the WhatsApp text drawings "
            "shown as a person sees them, no words, <b>rebuild base / rebuild</b> = the base drawing / base + zooms decoded to grey, "
            "no words (the proof the text carries the answer), <b>sms+rebuild</b> = both.</p>"
            "<table class='sum'><tr><th>picture</th><th>category</th><th>SMS silhouette</th><th>low SMS</th>"
            "<th>zooms</th><th>high + zooms B</th><th>high SMS</th><th>rebuild SSIM</th>%s</tr>%s</table>"
            % ("".join("<th>%s</th>" % c for c in SCORED), "".join(rows)))


CSS = """
td.ok { color:var(--good); text-align:center; } td.no { color:#e03131; text-align:left; } tr.tot td { border-top:2px solid var(--line); }
.ans { color:var(--good); font-weight:700; } table.sum { margin:10px 0 20px; } table.sum td:first-child, table.sum td:nth-child(2), table.sum td:nth-child(3) { text-align:left; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    items = json.load(open(os.path.join(HERE, "dataset.json")))
    if a.only:
        items = [it for it in items if it["name"] in a.only.split(",")]

    def safe(it):
        try:
            x = one(it)
            print(it["name"], "ok", "%d zooms, %d B" % (len(x["zooms"]), x["bytes"]), flush=True)
            return x
        except Exception:
            traceback.print_exc()
            return None

    with ThreadPoolExecutor(a.workers) as pool:
        xs = [x for x in pool.map(safe, items) if x]
    style = V.PAGE.split("<style>")[1].split("</style>")[0].replace("%%", "%")
    cats = list(dict.fromkeys(x["item"]["category"] for x in xs))
    nav = " · ".join("<b>%s</b>: %s" % (h(c), " ".join('<a href="#%s">%s</a>' % (x["key"], h(x["key"].replace("_", " ")))
                                                          for x in xs if x["item"]["category"] == c)) for c in cats)
    body = "".join(VZ.section(x, low_branch(x), "%s <span class='cap'>· %s</span>" % (h(x["key"].replace("_", " ")), h(x["item"]["category"])))
                   for x in xs)
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Emergency pictures as text</title><style>%s%s%s</style></head><body><main><h1>Emergency pictures as text</h1>"
            "<p class='intro'>%d pictures made with Muse Image, one clear subject on a plain background each, in five groups. Each runs "
            "through the current pipeline: the low reply (caption + SMS silhouette, WhatsApp outline), then the high drawing and the "
            "zooms the server chooses. Every SMS is listed. SSIM: the rebuild from the text vs the greyscale picture at 232 px.</p>"
            "<p>%s</p>%s%s</main></body></html>") % (style, VZ.EXTRA_CSS, CSS, len(xs), nav, summary(xs), body)
    out = os.path.join(E.OUT, "emergency.html")
    open(out, "w").write(page)
    json.dump([{"name": x["key"], "category": x["item"]["category"], "object": x["low"].get("object"), "caption": x["low"]["caption"],
                "low_sms": len(VZ.sms_parts(x["low"]["sms"])), "low_whatsapp_bytes": x["low"]["mono_bytes"],
                "recognised": (x["low"].get("check") or {}).get("recognised"), "mode": x["mode"], "zooms": len(x["zooms"]),
                "high_bytes": x["bytes"], "high_sms": sum(len(VZ.sms_parts(t)) for _, t in x["messages"]),
                "ssim": x["final_ssim"], "picture_ssim": x["picture_ssim"], "low_seconds": round(x["low_seconds"], 1),
                "high_seconds": round(x["high_seconds"], 1)} for x in xs], open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    print(out, len(page) // 1000, "KB")


if __name__ == "__main__":
    main()
