"""Record MuZip runs step by step for a video scene: every message both ways (the phone's code texts, Muse's replies
with their ZOOM lines), every view Muse got (box, colour, detail, picture, SAM 3.1's outline) and every decision.
Several runs go at once; the first that matches `--shape` is kept in sim/video/<name>/ (run.json + the pictures).

    .venv/bin/python demo/video/record_run.py hard/dashboard dashboard \\
        "Two warning lights came on on my dashboard. Which ones are they, and what do they mean?" --shape big,small,small
"""
import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
from PIL import Image  # noqa: E402

from evac import closer, codes, llm, router, store  # noqa: E402
from phones import Phone  # noqa: E402


def one(img, question, n):
    """One run through the router; -> everything it sent and saw."""
    log = []
    phone = Phone("+155507%04d" % n, lambda who, text: reply(router.handle(text, None, who=who)["text"]), delay=0)

    def reply(text):
        log.append({"from": "muse", "text": text, "texts": codes.sms_parts(text)})
        return heard(text)
    heard = phone.got
    sent = phone.text

    def text(message):
        log.append({"from": "phone", "text": message, "texts": codes.sms_parts(message),
                    "bytes": sum(len(c["data"]) for c in codes.decode(message)["complete"])})
        return sent(message)
    phone.text = text
    phone.text(question + "\n" + codes.text(phone.picture(img)))
    inq = next(r for r in store.find("inquiry") if r["who"] == phone.who)
    return inq, log


def main():
    a = argparse.ArgumentParser()
    a.add_argument("sample"), a.add_argument("name"), a.add_argument("question")
    a.add_argument("--shape", default="", help="e.g. big,small,small: the close-ups wanted (small = under a quarter of the photo's width)")
    a.add_argument("--runs", type=int, default=4)
    a.add_argument("--together", action="store_true", help="keep only a run that asked for two or more close-ups at once")
    a.add_argument("--sam-first", action="store_true", help="keep only a run where SAM 3.1 outlined something on the first close-up")
    a.add_argument("--colour-first", action="store_true", help="keep only a run whose first close-up is in colour")
    args = a.parse_args()
    llm._load_dotenv()
    store.use(tempfile.mkdtemp())
    img = Image.open(os.path.join(ROOT, "sim", "samples", args.sample + ".jpg")).convert("RGB")
    with ThreadPoolExecutor(args.runs) as pool:
        runs = list(pool.map(lambda n: one(img, args.question, n), range(args.runs)))
    want = [s for s in args.shape.split(",") if s]
    for inq, log in runs:
        views, w = closer.got(inq), inq["meta"]["pic"]["size"][0]
        shape = ["small" if v["got_box"][2] < w / 4 else "big" for v in views]
        at_once = [len(d.get("needs") or ([d["need"]] if d.get("need") else [])) for d in inq["meta"]["decisions"]]
        print(inq["meta"].get("sure"), shape, "asked per round", at_once, [m["texts"] for m in log],
              "first: colour" if views and views[0].get("colour") else "first: grey", "SAM %s" % (views and (views[0].get("sam") or {}).get("score")),
              views and views[0].get("object"))
        if inq["meta"].get("sure") == "yes" and (not want or shape == want) and (not args.together or max(at_once) >= 2) \
                and (not args.sam_first or (views and views[0].get("sam"))) \
                and (not args.colour_first or (views and views[0].get("colour"))):
            break
    else:
        sys.exit("No run had that shape; try again or change --shape.")
    out = os.path.join(ROOT, "sim", "video", args.name)
    os.makedirs(out, exist_ok=True)
    store.image(inq["id"]).save(os.path.join(out, "0_sketch.png"))
    kept = []
    for i, v in enumerate(views, 1):
        store.image(v["zoom"]).save(os.path.join(out, "%d_closeup.png" % i))
        kept.append({"image": "%d_closeup.png" % i, "box": v["got_box"], "asked": v["box"], "colour": v["colour"], "high": v.get("high", False),
                     "full": v.get("full", False), "bytes": v.get("bytes"), "texts": v.get("texts"), "why": v.get("why"),
                     "object": v.get("object"), "sam": v.get("sam")})
    run = {"sample": args.sample, "question": args.question, "size": list(img.size), "sketch": inq["meta"]["pic"]["size"],
           "views": kept, "decisions": inq["meta"]["decisions"], "messages": log, "answer": log[-1]["text"],
           "phone_bytes": sum(m.get("bytes", 0) for m in log if m["from"] == "phone"),
           "photo_bytes": os.path.getsize(os.path.join(ROOT, "sim", "samples", args.sample + ".jpg"))}
    with open(os.path.join(out, "run.json"), "w") as f:
        json.dump(run, f, indent=1, default=str)
    print("kept in", out, "-", run["phone_bytes"], "bytes from the phone")


if __name__ == "__main__":
    main()
