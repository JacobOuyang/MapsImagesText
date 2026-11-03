"""The look-closer loop's own eval: every question in dataset.json, RUNS times each, all at once, through the router
with a simulated phone that keeps the original (evals/kit/three_ways.py's muzip_run). Run it after every change to
the loop (evac/closer.py, the ZOOM code in evac/codes.py, evac/smsimage.py): it prints, per question, how often Muse
was right and sure (a separate Muse call judges each answer against the reference), the texts, close-ups and
seconds it took, and the change against BASELINE.json.

    .venv/bin/python evals/loop/run.py                 # 3 runs per question, all in parallel: a few minutes
    .venv/bin/python evals/loop/run.py --runs 5 --only bus_timetable,whole_milk
    .venv/bin/python evals/loop/run.py --save          # make this run the baseline (BASELINE.json and RESULTS.md)

Kinds: "generated" (Muse Image photos, sim/samples/hard/), "real" (Wikimedia Commons, sim/samples/real/), both with a
small detail that decides the answer; "easy" (real photos whose detail a 5 KB WebP already shows): there the loop
should answer in a look or two, so they measure what it wastes. Each run is kept by a hash of the loop's code
(out/eval/loop/<hash>/), so re-running an unchanged loop costs nothing.
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "evals", "kit"))
from PIL import Image  # noqa: E402

import three_ways  # noqa: E402
from evac import store  # noqa: E402

LOOP = ["evac/closer.py", "evac/codes.py", "evac/smsimage.py", "evac/router.py", "evac/sam.py", "evac/llm.py"]
BASELINE = os.path.join(HERE, "BASELINE.json")


def loop_hash():
    """What the results depend on: the loop's code and the dataset."""
    h = hashlib.sha1()
    for f in LOOP + ["evals/loop/dataset.json"]:
        with open(os.path.join(ROOT, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:10]


def one(item, img, k, out):
    """Run k of one question; kept in `out`, so it isn't re-run."""
    path = os.path.join(out, "%s.%d.json" % (item["id"], k))
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    t = time.time()
    try:
        r = three_ways.muzip_run(img, item["question"], item["reference"], 1000 + 100 * item["n"] + k)
    except Exception as ex:
        r = {"answer": "(error: %s)" % str(ex)[:120], "sure": "no", "verdict": "not sure", "texts": 0, "bytes": 0, "views": 0}
    r = dict(r, id=item["id"], k=k, seconds=round(time.time() - t, 1))
    with open(path, "w") as f:
        json.dump(r, f)
    return r


def summarise(item, runs):
    right = sum(r["verdict"] == "right" and r["sure"] == "yes" for r in runs)
    return {"id": item["id"], "kind": item["kind"], "runs": len(runs), "right": right,
            "hedged": sum(r["verdict"] == "right" and r["sure"] != "yes" for r in runs),
            "wrong": sum(r["verdict"] == "wrong" for r in runs),
            "texts": statistics.median(r["texts"] for r in runs), "looks": statistics.median(r["views"] for r in runs),
            "seconds": statistics.median(r["seconds"] for r in runs), "answers": [r["answer"].split("\n")[0][:100] for r in runs]}


def table(rows, base):
    def delta(row, key, fmt):
        b = base.get(row["id"])
        if not b:
            return ""
        d = row[key] / row["runs"] - b[key] / b["runs"] if key == "right" else row[key] - b[key]
        return (" (%+.0f%%)" % (100 * d) if key == "right" else " (" + fmt % d + ")") if d else ""
    lines = ["| Question | Kind | Right and sure | Hedged | Wrong | Texts | Close-ups | Seconds |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %d/%d%s | %d | %d | %g%s | %g | %g |" % (
            r["id"], r["kind"], r["right"], r["runs"], delta(r, "right", ""), r["hedged"], r["wrong"],
            r["texts"], delta(r, "texts", "%+g"), r["looks"], r["seconds"]))
    for kind in ("generated", "real", "easy"):
        part = [r for r in rows if r["kind"] == kind]
        if part:
            lines.append("| **all %s** | | **%d/%d** | %d | %d | %g | | |" % (kind, sum(r["right"] for r in part), sum(r["runs"] for r in part),
                         sum(r["hedged"] for r in part), sum(r["wrong"] for r in part), statistics.median(r["texts"] for r in part)))
    return "\n".join(lines)


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    a.add_argument("--runs", type=int, default=3)
    a.add_argument("--only", help="comma-separated question ids")
    a.add_argument("--save", action="store_true", help="make this run the baseline")
    args = a.parse_args()
    with open(os.path.join(HERE, "dataset.json")) as f:
        items = [dict(it, n=n) for n, it in enumerate(json.load(f))]
    if args.only:
        items = [it for it in items if it["id"] in args.only.split(",")]
    out = os.path.join(ROOT, "out", "eval", "loop", loop_hash())
    os.makedirs(out, exist_ok=True)
    store.use(tempfile.mkdtemp())
    images = {it["photo"]: Image.open(os.path.join(ROOT, "sim", "samples", it["photo"] + ".jpg")).convert("RGB") for it in items}
    jobs = [(it, k) for it in items for k in range(args.runs)]
    print("loop %s: %d questions x %d runs, all at once" % (os.path.basename(out), len(items), args.runs), flush=True)
    t = time.time()
    with ThreadPoolExecutor(min(32, len(jobs))) as pool:
        results = list(pool.map(lambda j: one(j[0], images[j[0]["photo"]], j[1], out), jobs))
    rows = [summarise(it, [r for r in results if r["id"] == it["id"]]) for it in items]
    base = {}
    if os.path.exists(BASELINE):
        with open(BASELINE) as f:
            base = {r["id"]: r for r in json.load(f)["rows"]}
    text = table(rows, base)
    print(text + "\n\n%.0f s; (+/-) is against BASELINE.json" % (time.time() - t))
    if args.save:
        with open(BASELINE, "w") as f:
            json.dump({"loop": os.path.basename(out), "runs": args.runs, "rows": rows}, f, indent=1)
        with open(os.path.join(HERE, "RESULTS.md"), "w") as f:
            f.write("# The look-closer loop's eval (baseline)\n\nGenerated by `evals/loop/run.py --save` (loop %s, %d runs per "
                    "question). Right and sure = a separate Muse call judged the answer the same as the reference, and Muse said "
                    "it was sure. Texts, close-ups and seconds are medians.\n\n%s\n" % (os.path.basename(out), args.runs, table(rows, {})))


if __name__ == "__main__":
    main()
