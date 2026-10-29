"""The kit's phrasebook (sim/pack.json): checked how-to steps with the pictures a person labelled useful.

    .venv/bin/python evals/emergency/build_pack.py

Each entry: id, task, the step, its before/after pictures (1-bit 128 px PNG, base64). The kit has them before any
emergency, so a reply only needs "#S..." with the entry's id: a checked picture for a few characters.
"""
import base64
import io
import json
import os
import pickle
import glob
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from evac import smsimage  # noqa: E402

OUT = os.path.join(HERE, "..", "..", "out", "eval")
labels = json.load(open(os.path.join(OUT, "labels.json")))
entries = []
for f in sorted(glob.glob(os.path.join(OUT, "labels", "*.pickle"))):
    r = pickle.load(open(f, "rb"))
    for p in r["pairs"]:
        key = "%s|%d" % (r["task"], p["n"])
        if not labels.get(key, {}).get("you", {}).get("useful"):
            continue
        pics = [base64.b64encode(smsimage.png(Image.open(io.BytesIO(x["sent_png"])))).decode() for x in p["frames"]]
        entries.append({"id": "%02d" % (len(entries) + 1), "task": r["task"], "step": p["step"], "item": p["item"],
                        "before": pics[0], "after": pics[1]})
pack = {"version": 1, "entries": entries}
path = os.path.join(HERE, "..", "..", "sim", "pack.json")
json.dump(pack, open(path, "w"), separators=(",", ":"))
print(path, len(entries), "entries,", os.path.getsize(path), "bytes")
