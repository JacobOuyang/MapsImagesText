"""Make the emergency test pictures with Muse Image (muse-image-1.0), one per entry in dataset.json.

    .venv/bin/python evals/emergency/make_images.py      -> sim/samples/emergency/<name>.jpg (existing ones are kept)
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402

OUT = os.path.join(HERE, "..", "..", "sim", "samples", "emergency")


def make(item):
    path = os.path.join(OUT, item["name"] + ".jpg")
    if os.path.exists(path):
        return item["name"], "kept"
    try:
        im = E.generate(item["prompt"])
        im.thumbnail((768, 768))
        im.save(path, quality=85)
        return item["name"], "made"
    except Exception as ex:
        return item["name"], "failed: %s" % ex


def main():
    os.makedirs(OUT, exist_ok=True)
    items = json.load(open(os.path.join(HERE, "dataset.json")))
    with ThreadPoolExecutor(4) as pool:
        for name, status in pool.map(make, items):
            print(name, status, flush=True)


if __name__ == "__main__":
    main()
