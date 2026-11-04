"""A static copy of /compare for GitHub Pages: the saved three-way runs, no server and no model calls (so no API key
and no bill), at https://jacobouyang.github.io/muzip/compare/.

    .venv/bin/python demo/static/build.py        # -> docs/compare/ (GitHub Pages serves main's docs/ folder)

It copies sim/compare.html with its three requests pointed at files next to it (compare.json, img/, photos/), the
close-ups and WebPs exactly as Muse got them, and each sample photo shrunk to at most 1600 px for display (the sizes
on the page are the measured ones, not these copies'). The dashboard comes first; the page notes that these are saved
runs and links to the repo for running it live.
"""
import json
import os
import shutil

from PIL import Image

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT = os.path.join(ROOT, "docs", "compare")
FIRST = ["dashboard", "extinguisher", "tick", "almond_milk"]  # the lead examples first; the rest in the eval's order
NOTE = ('<div class="static-note">Saved runs from <code>evals/kit/three_ways.py</code>, no live model calls. To run '
        'MuZip yourself: <a href="https://github.com/JacobOuyang/muzip#try-it">github.com/JacobOuyang/muzip</a>. '
        '&larr; &rarr; or the buttons switch photos.</div>')
STYLE = ('<style>.static-note{padding:8px 26px;font-size:14px;color:#8b98a5;background:#0b1015;border-bottom:1px solid #2a3540}'
         '.static-note a{color:#1a8cff}.static-note code{color:#9fc3e7}'
         '@media (max-width:900px){html,body{overflow:auto;height:auto}main{grid-template-columns:1fr}.pic{min-height:240px}}</style>')


def main():
    src = os.path.join(ROOT, "out", "eval", "three_ways")
    if not os.path.exists(os.path.join(src, "results.json")):
        src = os.path.join(ROOT, "demo", "three_ways")  # a fresh clone: the measured run kept with the demo
    rows = json.load(open(os.path.join(src, "results.json")))
    rows.sort(key=lambda r: (FIRST.index(r["name"]) if r["name"] in FIRST else len(FIRST)))
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "photos"))
    for r in rows:
        for image in [r["webp"]["image"]] + [v["image"] for v in r["muzip"]["views"]]:
            os.makedirs(os.path.dirname(os.path.join(OUT, "img", image)), exist_ok=True)
            shutil.copy(os.path.join(src, image), os.path.join(OUT, "img", image))
        photo = os.path.join(OUT, "photos", r["sample"] + ".jpg")
        os.makedirs(os.path.dirname(photo), exist_ok=True)
        img = Image.open(os.path.join(ROOT, "sim", "samples", r["sample"] + ".jpg")).convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        img.save(photo, quality=85)
    json.dump({"rows": rows}, open(os.path.join(OUT, "compare.json"), "w"))
    page = open(os.path.join(ROOT, "sim", "compare.html")).read()
    for a, b in [("fetch('/api/compare')", "fetch('compare.json')"), ("'/eval/three_ways/'", "'img/'"),
                 ('src="/eval/three_ways/', 'src="img/'), ('src="/samples/', 'src="photos/')]:
        assert a in page, a
        page = page.replace(a, b)
    page = page.replace("<title>Three Ways</title>", "<title>MuZip: Three Ways</title>").replace("</head>", STYLE + "</head>", 1)
    i = page.index("</header>") + len("</header>")
    page = page[:i] + NOTE + page[i:]
    open(os.path.join(OUT, "index.html"), "w").write(page)
    open(os.path.join(ROOT, "docs", "index.html"), "w").write(
        '<!doctype html><meta charset="utf-8"><title>MuZip</title><meta http-equiv="refresh" content="0; url=compare/">'
        '<a href="compare/">MuZip: the three-way comparison</a>\n')
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(OUT) for f in fs)
    print("wrote docs/compare/: %d photos, %.1f MB" % (len(rows), size / 1e6))


if __name__ == "__main__":
    main()
