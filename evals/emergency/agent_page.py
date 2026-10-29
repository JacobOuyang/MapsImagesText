"""Everything the agent sends for each emergency picture (and a few text-only questions), on one page.

    .venv/bin/python evals/emergency/agent_page.py [--only cpr_hands,pill]   -> out/eval/agent.html

Each picture goes through router.handle exactly as the simulator's Photo tab sends it: Muse picks the tools
(describe_photo, show_steps, photo_details), and the page shows the tools it chose, the reply, every SMS, the
WhatsApp messages, step flip-books (animated, with the Muse Image frames they were drawn from) and close-ups with
what to look for. Results are cached in out/eval/agent/<name>.pickle; delete one to run it again.
"""
import argparse
import html
import json
import os
import pickle
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import photo_eval as E  # noqa: E402
import visualize_tree as V  # noqa: E402
import visualize_zooms as VZ  # noqa: E402
from evac import router  # noqa: E402

PICS = os.path.join(HERE, "..", "..", "sim", "samples", "emergency")
OUT = os.path.join(E.OUT, "agent")
YOU = (41.9010, 12.5016)
PHOTO_ASK = "A friend sent me this photo but it will not load. What is it, and is there anything I should look at closely or do?"
TEXT_ASKS = {"ask_cpr": "How do I do CPR?", "ask_gas": "How do I shut off the gas at my meter?",
             "ask_recovery": "How do I put someone in the recovery position?",
             "ask_tourniquet": "How do I put a tourniquet on a bleeding arm?",
             "ask_extinguisher": "How do I use a fire extinguisher?", "ask_epipen": "How do I use an EpiPen?",
             "ask_choking": "Someone is choking, what do I do?"}
h = html.escape


def one(job):
    name, text, image = job
    path = os.path.join(OUT, name + ".pickle")
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    t = time.time()
    try:
        res = router.handle(text, YOU, image=open(image, "rb").read() if image else None)
    except Exception as ex:
        traceback.print_exc()
        res = {"error": "%s: %s" % (type(ex).__name__, ex)}
    res.update(name=name, ask=text, image=image, wall=round(time.time() - t, 1))
    pickle.dump(res, open(path, "wb"))
    print(name, res.get("wall"), "s", [c["tool"] for c in res.get("tools", [])], flush=True)
    return res


def bubbles(text):
    parts = VZ.sms_parts(text)
    return len(parts), '<div class="bubbles">%s</div>' % "".join(
        '<div class="sms"><span class="pn">%d/%d</span><pre>%s</pre></div>' % (i, len(parts), h(p)) for i, p in enumerate(parts, 1))


def steps_node(f, idx):
    frames = f.get("frames") or []
    flip = ('<pre class="art flip" data-frames="%s">%s</pre><div class="play">▶ plays like one WhatsApp message edited in place</div>'
            % (h(json.dumps(frames)), h(frames[0]))) if frames else "<p>(no frames: Muse Image failed, the steps went as words)</p>"
    pics = "".join('<figure class="thumb"><img class="thumb" src="%s"><figcaption>%s</figcaption></figure>' % (p["data_url"], h(src or ""))
                   for p, src in zip(f.get("pictures") or [], f.get("sources") or [None] * 99) if p)
    static = "".join('<pre class="art small">%s</pre>' % h(fr) for fr in frames)
    n, sms = bubbles(f["sms"])
    return "".join([
        V.node("Flip-book: %s" % h(f["title"]), flip, "%d frames · %s bytes for WhatsApp" % (len(frames), format(f.get("mono_bytes") or 0, ",")), "good"),
        V.node("The pictures it was drawn from", '<div class="thumbs">%s</div>' % pics,
               "web pictures (Wikimedia Commons, Openverse) chosen by Muse and cropped to what matters; Muse Image only "
               "where nothing fit, or edits of the photo when the person sent one"),
        V.node("Every frame", '<div class="frames">%s</div>' % static, "WhatsApp, one message per step"),
        V.node("SMS version", sms, "%d SMS · + = × the things, # the part to look at, figure spaces as blanks" % n, "pay"),
    ])


def sms_only_node(f):
    n, sms = bubbles(f["sms"])
    return V.node("Close-up by SMS: %s" % h(f["title"]), sms, "%d SMS; on WhatsApp it is part of the complete picture" % n, "pay")


def close_up_node(f):
    n, sms = bubbles(f["sms"])
    return V.node("Close-up: %s" % h(f["title"]), '<pre class="art">%s</pre>' % h(f["mono"]) + "<p class='lbl'>SMS (%d):</p>%s" % (n, sms),
                  "%s bytes for WhatsApp" % format(f.get("mono_bytes") or 0, ","), "good")


def section(r, item):
    title = h(r["name"].replace("_", " "))
    if r.get("error"):
        return "<section id='%s'><h2>%s</h2><p>Failed: %s</p></section>" % (r["name"], title, h(r["error"]))
    tools = ", ".join("%s(%s)" % (c["tool"], ", ".join("%s=%s" % kv for kv in c["args"].items())) for c in r.get("tools", [])) or "none"
    root = V.img(V.b64(__import__("PIL.Image", fromlist=["Image"]).open(r["image"]))) if r.get("image") else "<p class='big'>%s</p>" % h(r["ask"])
    q = ""
    if item:
        q = "<p><b>%s</b> → %s</p>" % (h(item["question"]), h(item["choices"][item["answer"]]))
    n, sms = bubbles(r.get("sms") or r.get("text") or "")
    first = [V.node("The person texts", "<p>%s</p>%s" % (h(r["ask"]), q), "%s" % (h(item["category"]) if item else "a question with no photo")),
             V.node("Muse picks the tools", "<code>%s</code>" % h(tools), "%.0f s in all" % r["wall"]),
             V.node("Reply · SMS", sms, "%d SMS" % n, "pay")]
    smsups = [f for f in r.get("followups") or [] if f.get("sms_only")]
    first += [sms_only_node(f) for f in smsups]
    if r.get("mono"):
        ok = r.get("understood")
        first.append(V.node("Reply · WhatsApp, sent once complete", "<p>%s</p><pre class=\"art\">%s</pre>" % (h(r.get("text") or ""), h(r["mono"])),
                            ("checked: Muse, shown only the drawings, made out %s · %s" % (h(", ".join(r.get("seen") or [])) or "nothing",
                             "understood, sent" if ok else "the whole drawing was not understood, so it was not sent"))
                            if ok is not None else "", "good" if ok else ""))
    follow = []
    for i, f in enumerate(f for f in r.get("followups") or [] if not f.get("sms_only")):
        follow.append('<div class="row">%s</div>' % (steps_node(f, i) if f["kind"] == "steps" else close_up_node(f)))
    total = n + sum(len(VZ.sms_parts(f["sms"])) for f in r.get("followups") or [])
    kinds = [f["kind"] for f in r.get("followups") or []]
    cap = "%s · %d SMS in all%s" % (", ".join(sorted(set(kinds))) or "reply only", total,
                                     "" if not kinds else " · %d follow-up message%s" % (len(kinds), "" if len(kinds) == 1 else "s"))
    return ('<section id="%s"><h2>%s</h2><p class="cap">%s</p><div class="tree"><div class="root">%s</div>'
            '<div class="branch"><div class="bl">Message and reply</div><div class="row">%s</div></div>'
            '<div class="branch"><div class="bl">Follow-up messages</div>%s</div></div></section>') % (
        r["name"], title, cap, root, "".join(first), "".join(follow) or "<p class='cap'>none</p>")


CSS = """
figure.thumb { margin:0; width:130px; } figure.thumb figcaption { font-size:10px; color:var(--quiet); }
.thumbs { display:flex; flex-wrap:wrap; gap:6px; } img.thumb { width:120px !important; max-height:none !important; border-radius:6px; }
.frames { display:flex; flex-wrap:wrap; gap:8px; } pre.art.small { font:5px/6px Menlo, monospace; max-width:240px; }
pre.flip { font:9px/11px Menlo, monospace !important; max-width:none !important; } .play { font-size:11px; color:var(--good); margin-top:4px; }
td.ok { color:var(--good); } td.no { color:#e03131; } .lbl { margin:8px 0 2px; font-size:12px; color:var(--quiet); } .node { max-width:560px; } table.sum td:first-child { text-align:left; }
"""
SCRIPT = """<script>
document.querySelectorAll('pre.flip').forEach(p => { const f = JSON.parse(p.dataset.frames); let i = 0;
  if (f.length > 1) setInterval(() => { i = (i + 1) % f.length; p.textContent = f[i]; }, 1600); });
</script>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    items = {it["name"]: it for it in json.load(open(os.path.join(HERE, "dataset.json")))}
    jobs = [(n, PHOTO_ASK, os.path.join(PICS, n + ".jpg")) for n in items] + [(k, q, None) for k, q in TEXT_ASKS.items()]
    if a.only:
        jobs = [j for j in jobs if j[0] in a.only.split(",")]
    with ThreadPoolExecutor(a.workers) as pool:
        rs = list(pool.map(one, jobs))
    rows = []
    for r in rs:
        kinds = [f["kind"] for f in r.get("followups") or []]
        u = r.get("understood")
        rows.append("<tr><td><a href='#%s'>%s</a></td><td>%s</td><td>%s</td><td>%d</td><td>%d</td><td class='%s'>%s</td><td>%s</td><td>%.0f</td></tr>" % (
            r["name"], h(r["name"].replace("_", " ")), h(items[r["name"]]["category"]) if r["name"] in items else "text only",
            h(", ".join(c["tool"] for c in r.get("tools", [])) or r.get("error", "")), kinds.count("steps"), kinds.count("close_up"),
            "" if u is None else ("ok" if u else "no"), "" if u is None else ("sent" if u else "withheld"),
            h(", ".join(r.get("seen") or [])[:60]), r.get("wall", 0)))
    table = ("<table class='sum'><tr><th>message</th><th>category</th><th>tools Muse called</th><th>flip-books</th>"
             "<th>close-ups</th><th>whole drawing</th><th>Muse saw (drawings only)</th><th>seconds</th></tr>%s</table>" % "".join(rows))
    style = V.PAGE.split("<style>")[1].split("</style>")[0].replace("%%", "%")
    page = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Agent replies</title><style>%s%s%s</style></head><body><main><h1>What the agent sends</h1>"
            "<p class='intro'>Each emergency picture is sent the way the simulator's Photo tab sends it (“%s”), plus three questions "
            "with no photo. Muse Spark picks the tools; steps come back as a flip-book of text frames, details as close-ups that say what "
            "to look for. Every SMS and every WhatsApp message is shown.</p>%s%s</main>%s</body></html>") % (
        style, VZ.EXTRA_CSS, CSS, h(PHOTO_ASK), table, "".join(section(r, items.get(r["name"])) for r in rs), SCRIPT)
    out = os.path.join(E.OUT, "agent.html")
    open(out, "w").write(page)
    print(out, len(page) // 1000, "KB")


if __name__ == "__main__":
    main()
