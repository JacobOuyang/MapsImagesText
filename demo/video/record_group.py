"""Record a group chat's questions about one shared photo, in order, for a video scene: one phone keeps the photo and
sends its sketch once; each question starts from the close-ups the phone already sent for the questions before it
(evac/closer.py: start), and the phone answers only what is new. Each question is kept like demo/video/record_run.py
keeps a run (sim/video/<name>/run.json + pictures), its views marked `known` when carried over.

    .venv/bin/python demo/video/record_group.py real/storm_street street_ben "My pickup's on the left ..." \\
        street_chen "Is the silver SUV at the far end ..."

Every question must end sure, or the whole group is recorded again (up to --tries times).
"""
import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
from PIL import Image  # noqa: E402

from evac import closer, codes, llm, router, store  # noqa: E402
from phones import Phone  # noqa: E402


def group(img, questions, n):
    """All the questions, one after another, about one photo on one phone. -> [(inquiry, its messages)]."""
    log = []
    phone = Phone("+155508%04d" % n, lambda who, text: reply(router.handle(text, None, who=who)["text"]), delay=0)
    heard, sent = phone.got, phone.text

    def reply(text):
        log.append({"from": "muse", "text": text, "texts": codes.sms_parts(text)})
        return heard(text)

    def text(message):
        log.append({"from": "phone", "text": message, "texts": codes.sms_parts(message),
                    "bytes": sum(len(c["data"]) for c in codes.decode(message)["complete"])})
        return sent(message)
    phone.text = text
    out = []
    first = phone.picture(img)
    phone.text(questions[0] + "\n" + codes.text(first))
    inq = next(r for r in store.find("inquiry") if r["who"] == phone.who)
    out.append((inq["id"], log[:]))
    pic, sketch = inq["meta"]["pic"]["id"], store.image(inq["id"])
    for q in questions[1:]:
        log.clear()
        log.append({"from": "phone", "text": q, "texts": codes.sms_parts(q), "bytes": 0})  # the question alone: the group has the sketch
        rid, first_reply = closer.start(phone.who, sketch, pic, q)
        reply(first_reply)
        out.append((rid, log[:]))
    return [(store.get(rid), msgs) for rid, msgs in out]


def keep(img, sample, name, question, inq, log):
    out = os.path.join(ROOT, "sim", "video", name)
    os.makedirs(out, exist_ok=True)
    store.image(inq["id"]).save(os.path.join(out, "0_sketch.png"))
    kept = []
    for i, v in enumerate(closer.got(inq), 1):
        store.image(v["zoom"]).save(os.path.join(out, "%d_closeup.png" % i))
        kept.append({"image": "%d_closeup.png" % i, "box": v["got_box"], "asked": v["box"], "colour": v["colour"], "high": v.get("high", False),
                     "full": v.get("full", False), "bytes": v.get("bytes"), "texts": v.get("texts"), "why": v.get("why"),
                     "object": v.get("object"), "sam": v.get("sam"), "known": v.get("known", False)})
    run = {"sample": sample, "question": question, "size": list(img.size), "sketch": inq["meta"]["pic"]["size"], "views": kept,
           "decisions": inq["meta"]["decisions"], "messages": log, "answer": log[-1]["text"],
           "phone_bytes": sum(m.get("bytes", 0) for m in log if m["from"] == "phone"),
           "photo_bytes": os.path.getsize(os.path.join(ROOT, "sim", "samples", sample + ".jpg"))}
    with open(os.path.join(out, "run.json"), "w") as f:
        json.dump(run, f, indent=1, default=str)
    print("kept %s: %d close-ups (%d carried over), %d bytes from the phone | %s" % (
        name, len(kept), sum(v["known"] for v in kept), run["phone_bytes"], run["answer"][:100].replace("\n", " ")))


def main():
    a = argparse.ArgumentParser()
    a.add_argument("sample")
    a.add_argument("pairs", nargs="+", help="name question name question ...")
    a.add_argument("--tries", type=int, default=4)
    args = a.parse_args()
    names, questions = args.pairs[0::2], args.pairs[1::2]
    llm._load_dotenv()
    store.use(tempfile.mkdtemp())
    img = Image.open(os.path.join(ROOT, "sim", "samples", args.sample + ".jpg")).convert("RGB")
    for n in range(args.tries):
        runs = group(img, questions, n)
        print("try %d:" % n, [(r["meta"].get("sure"), len(closer.got(r))) for r, _ in runs])
        if all(r["meta"].get("sure") == "yes" for r, _ in runs):
            for (r, log), name, q in zip(runs, names, questions):
                keep(img, args.sample, name, q, r, log)
            return
    sys.exit("No try ended sure on every question.")


if __name__ == "__main__":
    main()
