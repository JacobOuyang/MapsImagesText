"""Can the question be answered from what was sent? Scored for every emergency picture (after run.py).

    .venv/bin/python evals/emergency/score.py     -> out/eval/emergency/scores.json (then run.py again for the page)

Muse answers each picture's multiple-choice question from one input at a time and is marked right or wrong against the
answer in dataset.json (no judging). The choices are shuffled once per picture, the same way for every input.
  none           nothing but the question: how much is guessable from common sense (the floor)
  photo          the picture itself: is the question answerable at all (the ceiling)
  sms            the low SMS as sent: Muse's caption and details, plus the silhouette only if it passed the check
  drawing        the high WhatsApp text (base drawing + every zoom) shown as a person would see it, without words
  rebuild_base   the base drawing decoded to grey (no words): what one drawing carries
  rebuild        the base + zooms decoded to grey (no words): the proof that the text drawings carry the answer
  sms+rebuild    both
"""
import base64
import glob
import hashlib
import io
import json
import os
import pickle
import random
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402
from evac import llm  # noqa: E402

OUT = os.path.join(E.OUT, "emergency")
PICS = os.path.join(HERE, "..", "..", "sim", "samples", "emergency")
CONDITIONS = ["none", "photo", "sms", "drawing", "rebuild_base", "rebuild", "sms+rebuild"]
WHAT = {
    "none": "nothing but this question (the photo is not available)",
    "photo": "a photo",
    "sms": "only a text message describing a photo (you cannot see the photo)",
    "drawing": "only a drawing made of text characters from a photo, shown in several parts: the whole photo, then "
               "closer drawings of some areas (the ZOOM line gives each one's box in percent of the photo)",
    "rebuild_base": "only a rough greyscale reconstruction of a photo, rebuilt from a small text drawing of it",
    "rebuild": "only a rough greyscale reconstruction of a photo, rebuilt from small text drawings of it",
    "sms+rebuild": "a text message describing a photo and a rough greyscale reconstruction of it, rebuilt from small text drawings",
}
ASK = ("You are given %s. Answer this question about the original photo: %s\n%s\nIf you can't tell, pick the most likely. "
       'Reply as JSON: {"answer": one letter}')


def url(im, fmt="PNG"):
    buf = io.BytesIO()
    im.save(buf, fmt)
    return "data:image/%s;base64,%s" % (fmt.lower(), base64.b64encode(buf.getvalue()).decode())


def from_b64(s):
    return Image.open(io.BytesIO(base64.b64decode(s.split(",", 1)[1]))).convert("RGB")


def stacked(messages):
    """Every WhatsApp message (base drawing, zooms) rendered in Menlo, one under the other."""
    ims = [E.drawing_png(text.split("\n"), 12) for _, text in messages]
    w = max(i.width for i in ims)
    out = Image.new("RGB", (w, sum(i.height for i in ims) + 20 * len(ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height + 20
    if out.height > 4000:
        out.thumbnail((4000, 4000))
    return out


def inputs(x, cond):
    """-> (text, [images]) for one condition."""
    if cond == "none":
        return "", []
    if cond == "photo":
        return "", [Image.open(os.path.join(PICS, x["key"] + ".jpg")).convert("RGB")]
    if cond == "sms":
        return "The text message:\n" + x["low"]["sms"], []
    if cond == "drawing":
        return "", [stacked(x["messages"])]
    if cond == "rebuild_base":
        return "", [from_b64(x["base_img"])]
    if cond == "rebuild":
        return "", [from_b64(x["zooms"][-1]["after"] if x["zooms"] else x["base_img"])]
    return "The text message:\n" + x["low"]["sms"], [from_b64(x["zooms"][-1]["after"] if x["zooms"] else x["base_img"])]


def ask(x, cond, order):
    it = x["item"]
    letters = "ABCD"
    choices = "\n".join("%s) %s" % (letters[i], it["choices"][j]) for i, j in enumerate(order))
    text, ims = inputs(x, cond)
    content = [{"type": "text", "text": ASK % (WHAT[cond], it["question"], choices) + ("\n\n" + text if text else "")}]
    content += [{"type": "image_url", "image_url": {"url": url(im)}} for im in ims]
    try:
        got = str(llm.chat_json([{"role": "user", "content": content}]).get("answer") or "").strip().upper()[:1]
    except Exception as ex:
        return {"answer": None, "right": False, "error": type(ex).__name__}
    pick = order[letters.index(got)] if got and got in letters[:len(order)] else None
    return {"answer": it["choices"][pick] if pick is not None else None, "right": pick == it["answer"]}


def one(path):
    x = pickle.load(open(path, "rb"))
    n = len(x["item"]["choices"])
    order = list(range(n))
    random.Random(int(hashlib.md5(x["key"].encode()).hexdigest(), 16)).shuffle(order)
    return x["key"], x["item"]["category"], {c: ask(x, c, order) for c in CONDITIONS}


def main():
    paths = sorted(glob.glob(os.path.join(OUT, "*", "result.pickle")))
    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(one, paths))
    scores = {k: {"category": cat, **r} for k, cat, r in res}
    json.dump(scores, open(os.path.join(OUT, "scores.json"), "w"), indent=1)
    print("%-18s %s" % ("", " ".join("%-12s" % c for c in CONDITIONS)))
    for k, s in scores.items():
        print("%-18s %s" % (k, " ".join("%-12s" % ("right" if s[c]["right"] else "wrong") for c in CONDITIONS)))
    print("%-18s %s" % ("right", " ".join("%-12s" % ("%d / %d" % (sum(s[c]["right"] for s in scores.values()), len(scores))) for c in CONDITIONS)))


if __name__ == "__main__":
    main()
