"""A set of step pictures to label by eye, for calibrating Muse Wonder's check against people.

    .venv/bin/python evals/emergency/label_set.py     -> out/eval/labels/<task>.pickle (every before/after pair tried)

Then open http://127.0.0.1:8765/eval/label.html in the simulator: each pair as it is sent (128 px black and
white, and its text version) with useful / not useful buttons. Labels are saved to out/eval/labels.json by the
simulator (/api/labels); Muse Wonder's verdict for a pair is shown only after it is labelled.
"""
import io
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from evac import howto, illustrate  # noqa: E402

OUT = os.path.join(HERE, "..", "..", "out", "eval", "labels")
TASKS = ["shut off the gas at the meter", "turn off the main water shut-off valve", "switch off the electricity at the breaker panel",
         "use a fire extinguisher", "use a fire blanket on a small pan fire", "turn off a gas stove burner", "use an EpiPen",
         "open a car door when the electronic lock fails", "turn off a running car engine with a push-button start",
         "close the valve on a propane tank", "turn on an AED", "reset a GFCI outlet", "turn off a space heater",
         "close a fireplace damper", "shut off the water to a toilet"]


def one(task):
    path = os.path.join(OUT, task.replace(" ", "_")[:50] + ".pickle")
    if os.path.exists(path):
        return task, "cached"
    t = time.time()
    r = howto.answer(task, you=(40.75491, -73.97441))
    pairs = []
    for f in r.get("frames_tried") or []:
        if f.get("error"):
            continue
        frames = []
        for x in f["frames"]:
            buf = io.BytesIO()
            illustrate._as_sent(x["image"]).resize((128, 128)).save(buf, "PNG")
            frames.append({"sent_png": buf.getvalue(), "mono": x["mono"], "pic_chars": len(x.get("pic") or "")})
        pairs.append({k: f[k] for k in ("n", "step", "item", "before", "after", "picture_ok", "picture_why", "text_ok", "text_why")}
                     | {"frames": frames})
    pickle.dump({"task": task, "steps": r.get("steps"), "status": r.get("status"), "pairs": pairs, "seconds": round(time.time() - t)},
                open(path, "wb"))
    return task, "%d pairs, %d s" % (len(pairs), time.time() - t)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(2) as pool:
        for task, msg in pool.map(one, TASKS):
            print(task, msg, flush=True)
