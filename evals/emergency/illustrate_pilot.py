"""How-to answers end to end (evac/howto.py): cited numbered steps, then a picture of what the reader does (evac/illustrate.py).

    .venv/bin/python evals/emergency/illustrate_pilot.py     -> out/eval/illustrate.html (cached in out/eval/illustrate/)

For five actions: the steps with which ones a citation confirms (Meta Model API web search), then the cited facts for
the picture step, the Muse Image picture, Muse Wonder's check of it, the tiny image that would be sent, and the text
versions with Muse Wonder's checks (sent only if those pass too).
"""
import base64
import html
import io
import json
import os
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402
import visualize_tree as V  # noqa: E402
from PIL import Image  # noqa: E402

from evac import howto, illustrate, verify  # noqa: E402

CASES = ["CPR on an adult", "help a choking adult", "use an EpiPen", "shut off the gas at the meter", "use a fire extinguisher"]
YOU = (40.75491, -73.97441)  # New York: 911
OUT = os.path.join(E.OUT, "illustrate")
h = html.escape


def tiny(img, w=192):
    s = img.convert("RGB").resize((w, w))
    b = io.BytesIO()
    s.convert("P", palette=1, colors=3).save(b, "PNG", optimize=True)
    return b.getvalue()


def one(task):
    path = os.path.join(OUT, task.replace(" ", "_") + ".pickle")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    t = time.time()
    r = howto.answer(task, you=YOU)
    r.update(task=task, seconds=round(time.time() - t))
    for f in r.get("frames_tried") or []:
        for x in f.get("frames") or []:
            if x.get("image") is not None:
                buf = io.BytesIO()
                x["image"].save(buf, "JPEG", quality=88)
                x["image_jpeg"] = buf.getvalue()
            x.pop("image", None)
    pickle.dump(r, open(path, "wb"))
    print(task, r["seconds"], "s · steps", r.get("status"), "· pictures",
          [(f.get("n"), f.get("picture_ok"), f.get("text_ok")) for f in r.get("frames_tried") or []], flush=True)
    return r


def data(b, kind):
    return "data:image/%s;base64,%s" % (kind, base64.b64encode(b).decode())


def section(r):
    if r.get("error"):
        return "<section><h2>%s</h2><p>%s</p></section>" % (h(r["task"]), h(r["error"]))
    marks = "".join("<li>%s <span class='cap'>(%s)</span></li>" % (h(s), h(st)) for s, st in zip(r["steps"], r["status"]))
    srcs = "".join("<li><a href='%s'>%s</a></li>" % (h(x["url"] or ""), h(x["title"][:70] or x["url"] or "")) for x in r["sources"][:8])
    nodes = [V.node("Numbered steps (always sent, SMS)", "<pre class='plain'>%s</pre>" % h(r["text"]), "%d searches by Muse" % len(r.get("searches") or []), "pay"),
             V.node("Which steps a citation confirms", "<ol>%s</ol>" % marks, "confirmed = a citation covers the step's text"),
             V.node("Cited sources", "<ol>%s</ol>" % srcs, "Meta Model API web search")]
    frames = r.get("frames_tried") or []
    seq = []
    for f in frames:
        if f.get("error"):
            seq.append(V.node("Step %d" % f["n"], "<p>Muse Image failed: %s</p>" % h(f["error"]), ""))
            continue
        sent = f["picture_ok"] and r["status"][f["n"] - 1] == "confirmed"
        body = ""
        for label, x in zip(("before", "after"), f["frames"]):
            x["small"] = illustrate.tiny(Image.open(io.BytesIO(x["image_jpeg"])))
            body += "<div class='pair'><div class='lbl'>%s · %s B</div><img src='%s'><img class='px' src='%s'><pre class='art big'>%s</pre></div>" % (
                label, format(x["small"]["bytes"], ","), data(x["image_jpeg"], "jpeg"), x["small"]["data_url"], h("\n".join(x["mono"])))
        seq.append(V.node("Step %d: %s" % (f["n"], h(f["step"])), body,
                          "%s: %s → %s · pictures: %s (%s)%s · text: %s (%s)" % (
                              h(f["item"]), h(f["before"]), h(f["after"]), "could be" if f["picture_ok"] else "couldn't be", h(f["picture_why"]),
                              " · SENT" if sent else "", "could be" if f["text_ok"] else "couldn't be", h(f["text_why"])), "good" if sent else ""))
    pics = ('<div class="branch"><div class="bl">Before and after pictures of the object (one chain of edits)</div><div class="row">%s</div></div>'
            % "".join(seq)) if seq else "<p class='cap'>no picture: no step changes an object (words only)</p>"
    sent = sum(1 for f in frames if not f.get("error") and f["picture_ok"] and r["status"][f["n"] - 1] == "confirmed")
    return ("<section><h2>%s</h2><p class='cap'>%d s · %d of %d pictures sent · %d text versions passed</p><div class='row'>%s</div>%s</section>" % (
        h(r["task"]), r.get("seconds", 0), sent, len(frames), sum(1 for f in frames if f.get("text_ok")), "".join(nodes), pics))


def main():
    os.makedirs(OUT, exist_ok=True)
    with ThreadPoolExecutor(2) as pool:  # each answer already makes up to 4 pictures at once
        rs = list(pool.map(one, CASES))
    style = V.PAGE.split("<style>")[1].split("</style>")[0].replace("%%", "%")
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>How-to answers</title><style>%s pre.big{font:10px/12px Menlo,monospace!important;max-width:none!important}"
            " img.px{width:120px!important;image-rendering:pixelated;margin-left:6px} .node img{display:inline-block!important;vertical-align:top;max-width:180px!important} .pair{display:inline-block;vertical-align:top;margin-right:10px} .lbl{font-size:11px;color:var(--quiet)}</style></head><body><main><h1>How-to answers: cited steps, then a "
            "checked picture per step</h1><p class='intro'>The steps come from Muse Spark with the Meta Model API's web search; a step is "
            "confirmed when a citation covers it. Muse picks at most 3 steps that change an object (a handle turned, a pin pulled, a cap "
            "off), never people or bodies; Muse Image draws the object before and after, black and white, no arrows, as one chain of edits. "
            "%s is asked only “could this be it?” of each pair and of its deliberately blurry text version.</p>%s</main></body></html>") % (style, h(verify.MODEL), "".join(section(r) for r in rs))
    open(os.path.join(E.OUT, "illustrate.html"), "w").write(page)
    print(os.path.join(E.OUT, "illustrate.html"))


if __name__ == "__main__":
    main()
