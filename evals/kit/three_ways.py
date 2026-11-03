"""One photo, one question, three ways to send it over a thin link, each judged by the same model (Muse Spark):

  full   the photo as it is (the normal way: the JPEG file)
  webp   the whole photo squeezed by classical compression into what MuZip used, WebP's best shot: every width and
         quality that fits, in colour and in grey, the best of them reported
  muzip  the look-closer loop (evac/closer.py) through the router, with a simulated phone keeping the original: a
         128 px grey picture, then only the close-ups Muse asks for

Questions are open ("Which of these can I take for a headache, and where is it?"), each with a reference answer.
Each way runs RUNS times (Muse's answers vary); a separate, text-only Muse call judges every answer against the
reference (right / wrong / not sure), the same way for all three. An answer counts as right only when it agrees with
the reference AND Muse said it was sure ("sure": "yes"); a right answer Muse hedged ("fairly", "no") is counted apart.

    .venv/bin/python evals/kit/three_ways.py             # runs everything (a few minutes), writes THREE_WAYS.md
    .venv/bin/python evals/kit/three_ways.py --report    # rewrites THREE_WAYS.md from the last run

Results and the pictures each way sent are kept in out/eval/three_ways/ (sim/compare.html shows them).
"""
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "sim"))
from PIL import Image  # noqa: E402

from evac import closer, codes, llm, router, smsimage, store  # noqa: E402
from phones import Phone  # noqa: E402

RUNS = 5
OUT = os.path.join(ROOT, "out", "eval", "three_ways")
# Photos where small details decide the answer (sim/samples/hard/, made with Muse Image at 1600 px; prompts.json).
# Each passed a screen first: Muse is right and sure from the full photo, and no WebP at about 3 KB lets it be sure.
# The spider samples failed it (the full black widow looks like a toy to Muse; a 3 KB colour WebP shows its red mark).
# The medicine cabinet is asked two questions: the same photo, two different sets of close-ups.
PHOTOS = [  # name, sample, question, the reference answer
    ("medicine_headache", "hard/medicine_cabinet", "I have a headache. Which of these can I take, and where is it?",
     "The ibuprofen (IBUPROFEN 200 MG PAIN RELIEVER), on the bottom shelf of the cabinet."),
    ("medicine_allergy", "hard/medicine_cabinet", "Is there anything here for allergies? Which one, and where?",
     "Loratadine (LORATADINE ALLERGY RELIEF), on the middle shelf of the cabinet."),
    ("tick", "hard/tick", "What's that on my leg?", "A tick: a small black tick on the lower leg, just above the sock."),
    ("extinguisher", "hard/extinguisher", "Can I use this fire extinguisher?", "No: it is marked EMPTY / RECHARGE, so it isn't ready to use."),
    # Real photos (sim/samples/real/, Wikimedia Commons, 2,600-4,000 px; CREDITS.md). Two more failed the screen
    # because their detail is big enough for a 5 KB WebP: a handwritten label on a box, and street parking signs.
    ("bus_timetable", "real/bus_timetable", "It's Saturday, 9 am. When's the next bus from this stop?", "10:06 am (the Saturday timetable has a 10:06)."),
    ("whole_milk", "real/whole_milk_aisle", "How much is a gallon of whole milk here?", "$3.69."),
    ("almond_milk", "real/milk_aisle", "Is there any almond milk here? Which brand?", "Yes: Almond Breeze (Blue Diamond)."),
    ("cinnamon", "real/pharmacy_shelves", "Is there anything with cinnamon on these shelves? What does its label say?", "Yes: a jar labelled SIR. CINNAMOMI (cinnamon syrup)."),
]
SIDES = [128, 256, 384]  # the widths that were ever WebP's best (192 and 512 never were), in colour and grey
WHATSAPP_SIDE, WHATSAPP_Q = 1600, 75  # WhatsApp's standard quality, roughly: long side 1600 px, JPEG (an estimate)
HERE = (40.6776, -74.0086)

ASK = """Someone texted you a question about a photo. Question: "{question}"
Answer it from the photo in one or two short plain sentences. Reply with JSON: {{"answer": your answer, "sure": "yes" |
"fairly" | "no"}}"""
JUDGE = """Question: "{question}"
Reference answer: "{truth}"
Answer to judge: "{answer}"
Does the answer agree with the reference? The thing it names, the fact or the yes / no must be the same; wording,
extra details and how it describes where something is may differ. If it says it can't tell or names nothing, that is
"not sure". Reply with JSON: {{"verdict": "right" | "wrong" | "not sure"}}"""


def sms(data):
    """Texts it takes to send these bytes as kit codes."""
    return codes.sms_parts(codes.text(codes.parts("P", data, "ab")))


FULL_SIDE = 2048  # the full photo as Muse gets it: phone originals (4,000 px) are too big to send whole


def full_view(img):
    """The photo the normal way, as Muse is shown it: JPEG, at most FULL_SIDE px."""
    img = img.convert("RGB")
    img.thumbnail((FULL_SIDE, FULL_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), "jpeg"


def verdict(question, answer, truth):
    """right / wrong / not sure against the reference answer."""
    try:
        v = llm.chat_json([{"role": "user", "content": JUDGE.format(question=question, truth=truth, answer=answer)}]).get("verdict")
    except Exception:
        v = None
    return v if v in ("right", "wrong") else "not sure"


def one_look(shown, question, truth):
    """Muse's answer from one picture (PNG bytes, or (bytes, kind)), as the 'full' and 'webp' ways get it."""
    shown = shown if isinstance(shown, tuple) else (shown, "png")
    try:
        d = llm.chat_json([{"role": "user", "content": [{"type": "text", "text": ASK.format(question=question)}, llm.image_part(*shown)]}])
        answer, sure = str(d.get("answer") or ""), str(d.get("sure") or "no")
    except Exception as ex:
        answer, sure = "(error: %s)" % str(ex)[:80], "no"
    return {"answer": answer, "sure": sure, "verdict": verdict(question, answer, truth)}


def score(runs):
    """(right and sure, right but hedged)"""
    return (sum(r["verdict"] == "right" and r["sure"] == "yes" for r in runs),
            sum(r["verdict"] == "right" and r["sure"] != "yes" for r in runs))


def webp(img, side, quality, colour):
    small = img.convert("RGB") if colour else img.convert("L")
    small.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def normally(path, img):
    """How the photo is normally sent, estimated without Muse: WhatsApp's standard quality (1600 px JPEG) and an
    iPhone's own HEIC (macOS sips at its default quality, from the photo at its full size)."""
    small = img.copy()
    small.thumbnail((WHATSAPP_SIDE, WHATSAPP_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=WHATSAPP_Q)
    heic = None
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "x.heic")
        if subprocess.run(["sips", "-s", "format", "heic", path, "--out", out], capture_output=True).returncode == 0:
            heic = os.path.getsize(out)
    return {"whatsapp": len(buf.getvalue()), "heic": heic}


def candidates(img, budget):
    """For each width and colour or grey: the highest WebP quality that fits in `budget` bytes."""
    out = []
    for colour in (True, False):
        for side in SIDES:
            fits = [q for q in range(1, 96) if len(webp(img, side, q, colour)) <= budget]
            if fits:
                q = max(fits)
                out.append({"side": side, "quality": q, "colour": colour, "bytes": len(webp(img, side, q, colour))})
    return out


def muzip_run(img, question, truth, n):
    """The loop through the router, a simulated phone keeping the original (`n`: its number, unique across every
    run going at once, since its inquiry is found by number). -> answer, texts, bytes, views."""
    phone = Phone("+155501%04d" % n, lambda who, text: (phone.got(router.handle(text, HERE, who=who)["text"])), delay=0)
    replies = []
    got = phone.got

    def heard(text):
        replies.append(text)
        return got(text)
    phone.got = heard
    phone.text(question + "\n" + codes.text(phone.picture(img)))
    sent = [c for m in phone.outbox for c in codes.decode(m)["complete"]]
    inq = next((r for r in store.find("inquiry") if r["who"] == phone.who), None)
    answer = replies[-1] if replies else ""
    return {"answer": answer, "sure": (inq and inq["meta"].get("sure")) or "no", "verdict": verdict(question, answer, truth), "texts": sum(codes.sms_parts(m) for m in phone.outbox),
            "bytes": sum(len(c["data"]) for c in sent), "views": len(phone.outbox) - 1, "inquiry": inq and inq["id"]}


def measure(k, name, rel, question, truth, pool):
    """One question, all three ways (photo k of PHOTOS); kept in OUT/<name>.json, so a re-run skips it."""
    kept = os.path.join(OUT, name + ".json")
    if os.path.exists(kept):
        with open(kept) as f:
            return json.load(f)
    path = os.path.join(ROOT, "sim", "samples", rel + ".jpg")
    img = Image.open(path).convert("RGB")
    os.makedirs(os.path.join(OUT, name), exist_ok=True)
    print("%s: MuZip x%d" % (name, RUNS), flush=True)
    muzip = list(pool.map(lambda n: muzip_run(img, question, truth, 100 * k + n), range(RUNS)))
    budget = int(statistics.median(r["bytes"] for r in muzip))
    best = max(muzip, key=lambda r: (score([r]), -r["bytes"]))  # the run the page shows: right, and smallest
    inq = store.get(best["inquiry"])
    views = [{"image": "%s/muzip_0.png" % name, "label": "128 px, grey"}]
    store.image(inq["id"]).save(os.path.join(OUT, name, "muzip_0.png"))
    for i, v in enumerate(closer.got(inq), 1):
        store.image(v["zoom"]).save(os.path.join(OUT, name, "muzip_%d.png" % i))
        views.append({"image": "%s/muzip_%d.png" % (name, i), "label": "closer look %d%s" % (i, ", colour" if v["colour"] else ""),
                      "box": v["got_box"], "full": v.get("full", False)})
    print("%s: the full photo x%d, WebP at %d bytes" % (name, RUNS, budget), flush=True)
    full = list(pool.map(lambda _: one_look(full_view(img), question, truth), range(RUNS)))
    tries = []
    for c in candidates(img, budget):
        shown = closer.enlarged(Image.open(io.BytesIO(webp(img, c["side"], c["quality"], c["colour"]))))  # decoded once: threads share it
        c["runs"] = list(pool.map(lambda _: one_look(shown, question, truth), range(RUNS)))
        c["right"], c["guessed"] = score(c["runs"])
        tries.append(c)
    top = max(tries, key=lambda c: (c["right"], c["guessed"], c["side"]))
    Image.open(io.BytesIO(webp(img, top["side"], top["quality"], top["colour"]))).save(os.path.join(OUT, name, "webp.png"))
    row = {"name": name, "sample": rel, "question": question, "truth": truth, "size": list(img.size), "normally": normally(path, img),
            "full": {"bytes": os.path.getsize(path), "texts": sms(open(path, "rb").read()), "runs": full,
                     **dict(zip(("right", "guessed"), score(full)))},
            "webp": {**{k: top[k] for k in ("side", "quality", "colour", "bytes", "runs", "right", "guessed")}, "texts": sms(b"x" * top["bytes"]),
                     "image": "%s/webp.png" % name, "tried": [{k: c[k] for k in ("side", "quality", "colour", "bytes", "right", "guessed")} for c in tries]},
            "muzip": {"runs": muzip, **dict(zip(("right", "guessed"), score(muzip))), "bytes": budget,
                      "texts": int(statistics.median(r["texts"] for r in muzip)), "views": views, "answer": best["answer"]}}
    with open(kept, "w") as f:
        json.dump(row, f, indent=1)
    print("%s: done" % name, flush=True)
    return row


def kb(b):
    return "%d B" % b if b < 1000 else "%.1f KB" % (b / 1000)


def report(rows):
    lines = ["# One photo, one question, three ways (measured)", "",
             "Generated by `evals/kit/three_ways.py`. Each way ran %d times; Muse Spark answered and a separate text-only Muse "
             "call judged each answer against the reference (right / wrong / not sure). Right = agrees with the reference, and Muse said it was sure; (+n) = right but hedged. WebP is given its best shot at MuZip's median size: 128, 256 and 384 px "
             "at the highest quality that fits, colour and grey, and the one with the most right answers is reported (ties: the most "
             "right but hedged, then the widest; all tried are below). Texts count the code's base64 and headers, as the kit sends it." % RUNS, "",
             "| Photo | Question (reference answer) | The full photo | WebP at MuZip's size | MuZip |", "|---|---|---|---|---|"]
    for r in rows:
        f, w, m = r["full"], r["webp"], r["muzip"]
        res = lambda x: "**%d/%d right**%s" % (x["right"], RUNS, " (+%d)" % x["guessed"] if x["guessed"] else "")
        lines.append("| %s | %s (%s) | %s, %d texts: %s | %s (%d px, q%d, %s), %d texts: %s | %s, %d texts: %s |" % (
            r["name"].replace("_", " "), r["question"], r["truth"], kb(f["bytes"]), f["texts"], res(f),
            kb(w["bytes"]), w["side"], w["quality"], "colour" if w["colour"] else "grey", w["texts"], res(w),
            kb(m["bytes"]), m["texts"], res(m)))
    lines += ["", "## Against how the photo is normally sent", "",
              "Estimates, no Muse calls: WhatsApp's standard quality as a %d px JPEG at quality %d, an iPhone's own HEIC "
              "as macOS `sips` encodes the photo at full size. MuZip is its median over the runs." % (WHATSAPP_SIDE, WHATSAPP_Q), "",
              "| Photo | Size | iPhone HEIC (est.) | WhatsApp standard (est.) | MuZip | MuZip vs WhatsApp |", "|---|---|---|---|---|---|"]
    for r in rows:
        n, m = r["normally"], r["muzip"]["bytes"]
        lines.append("| %s | %d×%d | %s | %s | %s | %.0f× smaller |" % (r["name"].replace("_", " "), r["size"][0], r["size"][1],
                     kb(n["heic"]) if n["heic"] else "?", kb(n["whatsapp"]), kb(m), n["whatsapp"] / m))
    for r in rows:
        lines += ["", "## %s" % r["name"].replace("_", " "), "", "MuZip, the run shown: \"%s\" (%d closer looks)." % (
            r["muzip"]["answer"].replace("\n", " "), len(r["muzip"]["views"]) - 1), "",
                  "| WebP tried | Bytes | Right |", "|---|---|---|"]
        lines += ["| %d px, q%d, %s | %s | %d/%d (+%d) |" % (c["side"], c["quality"], "colour" if c["colour"] else "grey", kb(c["bytes"]), c["right"], RUNS, c["guessed"])
                  for c in r["webp"]["tried"]]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(os.path.dirname(__file__), "THREE_WAYS.md"), "w") as f:
        f.write(text)
    print(text)


def main():
    path = os.path.join(OUT, "results.json")
    if "--report" not in sys.argv:
        if not llm.available():
            sys.exit("Needs a Meta Model API key in .env.")
        store.use(tempfile.mkdtemp())
        os.makedirs(OUT, exist_ok=True)
        with ThreadPoolExecutor(16) as pool, ThreadPoolExecutor(len(PHOTOS)) as photos:  # every photo at once
            rows = list(photos.map(lambda kp: measure(kp[0], *kp[1], pool), enumerate(PHOTOS)))
        os.makedirs(OUT, exist_ok=True)
        with open(path, "w") as f:
            json.dump(rows, f, indent=1)
    with open(path) as f:
        report(json.load(f))


if __name__ == "__main__":
    main()
