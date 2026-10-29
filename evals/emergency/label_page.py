"""The labelling page for step pictures (after label_set.py): out/eval/label.html, served by the simulator at
http://127.0.0.1:8765/eval/label.html so labels save through /api/labels to out/eval/labels.json.

Each before/after pair is shown as it is sent (128 px black and white, and its text version). Pick who is labelling
(you or Claude), then mark each pair useful or not; Muse Wonder's verdict for a pair is revealed only after it is
labelled. The top shows how often each pair of judges agrees.
"""
import base64
import glob
import html
import json
import os
import pickle

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "out", "eval")
h = html.escape


def main():
    items = []
    for f in sorted(glob.glob(os.path.join(OUT, "labels", "*.pickle"))):
        r = pickle.load(open(f, "rb"))
        for p in r["pairs"]:
            items.append({"key": "%s|%d" % (r["task"], p["n"]), "task": r["task"], "step": p["step"], "item": p["item"],
                          "before": p["before"], "after": p["after"], "wonder": p["picture_ok"], "why": p["picture_why"],
                          "text_ok": p["text_ok"],
                          "imgs": ["data:image/png;base64," + base64.b64encode(x["sent_png"]).decode() for x in p["frames"]],
                          "mono": ["\n".join(x["mono"]) for x in p["frames"]]})
    page = PAGE.replace("__ITEMS__", json.dumps(items))
    open(os.path.join(OUT, "label.html"), "w").write(page)
    print(os.path.join(OUT, "label.html"), len(items), "pairs")


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Label step pictures</title><style>
:root { --bg:#f7f7f5; --fg:#1d1d1f; --quiet:#6b6b70; --line:#d0d0ca; --card:#fff; --good:#2b8a3e; --bad:#c92a2a; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141416; --fg:#ececf0; --quiet:#9a9aa3; --line:#3a3a42; --card:#1d1d21; --good:#69db7c; --bad:#ff8787; } }
:root[data-theme="dark"] { --bg:#141416; --fg:#ececf0; --quiet:#9a9aa3; --line:#3a3a42; --card:#1d1d21; --good:#69db7c; --bad:#ff8787; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px system-ui, sans-serif; }
main { max-width:1100px; margin:0 auto; } h1 { font-size:22px; margin:0 0 6px; } .quiet { color:var(--quiet); }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; margin:12px 0; display:flex; gap:16px; flex-wrap:wrap; }
.card img { width:128px; height:128px; image-rendering:pixelated; background:#fff; border:1px solid var(--line); }
pre { font:6px/7px Menlo, monospace; margin:0; background:#fff; color:#000; padding:4px; }
.frames { display:flex; gap:10px; } .info { flex:1; min-width:260px; } button { font:inherit; padding:6px 12px; border-radius:8px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; margin-right:6px; }
button.on.yes { background:var(--good); color:#fff; } button.on.no { background:var(--bad); color:#fff; }
.verdict { margin-top:8px; font-size:13px; } input[type=text] { width:100%; box-sizing:border-box; margin-top:8px; padding:6px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); }
#agree span { margin-right:18px; }
</style></head><body><main>
<h1>Is this step picture useful?</h1>
<p class="quiet">Each pair is the object before and after a step, as the person gets it: 128 px black and white (and below it the text version). Useful = a person in a hurry would understand the change and it matches the step. Muse Wonder's verdict appears after you label.</p>
<p>Labelling as: <select id="who"><option value="you">you</option><option value="claude">Claude</option></select> · <span id="done"></span></p>
<p id="agree"></p>
<div id="list"></div>
<script>
const ITEMS = __ITEMS__;
let labels = {};
const $ = id => document.getElementById(id);
async function load() { labels = await fetch('/api/labels').then(r => r.json()).catch(() => ({})); render(); }
async function save(key, useful, note) {
  const who = $('who').value;
  (labels[key] = labels[key] || {})[who] = {useful, note};
  render();
  await fetch('/api/labels', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, who, useful, note})});
}
function agree(a, b) {
  let n = 0, same = 0;
  for (const it of ITEMS) { const x = a(it), y = b(it); if (x === undefined || y === undefined) continue; n++; if (x === y) same++; }
  return n ? `${same}/${n}` : '–';
}
function render() {
  const who = $('who').value, L = k => labels[k] || {};
  $('done').textContent = `${ITEMS.filter(it => L(it.key)[who]).length} of ${ITEMS.length} labelled`;
  const you = it => L(it.key).you && L(it.key).you.useful, cl = it => L(it.key).claude && L(it.key).claude.useful, mw = it => it.wonder;
  $('agree').innerHTML = `<span>you vs Muse Wonder: <b>${agree(you, mw)}</b></span><span>Claude vs Muse Wonder: <b>${agree(cl, mw)}</b></span><span>you vs Claude: <b>${agree(you, cl)}</b></span>`;
  const list = $('list'); list.innerHTML = '';
  for (const it of ITEMS) {
    const mine = L(it.key)[who];
    const c = document.createElement('div'); c.className = 'card';
    c.innerHTML = `<div class="frames">${it.imgs.map((s, i) => `<div><div class="quiet">${i ? 'after' : 'before'}</div><img src="${s}" alt=""><pre>${it.mono[i].replace(/&/g,'&amp;').replace(/</g,'&lt;')}</pre></div>`).join('')}</div>
      <div class="info"><div class="quiet">${it.task}</div><div><b>Step ${it.key.split('|')[1]}:</b> ${it.step}</div>
      <div class="quiet">${it.item}: ${it.before} → ${it.after}</div><div style="margin-top:8px"></div></div>`;
    const info = c.querySelector('.info > div:last-child');
    const yes = Object.assign(document.createElement('button'), {textContent: 'Useful', className: 'yes' + (mine && mine.useful ? ' on' : '')});
    const no = Object.assign(document.createElement('button'), {textContent: 'Not useful', className: 'no' + (mine && mine.useful === false ? ' on' : '')});
    const note = Object.assign(document.createElement('input'), {type: 'text', placeholder: 'Why (optional)', value: mine ? mine.note || '' : ''});
    yes.onclick = () => save(it.key, true, note.value); no.onclick = () => save(it.key, false, note.value);
    note.onchange = () => { if (mine) save(it.key, mine.useful, note.value); };
    info.append(yes, no, note);
    if (mine) { const v = document.createElement('div'); v.className = 'verdict';
      v.innerHTML = `Muse Wonder: <b style="color:var(--${it.wonder ? 'good' : 'bad'})">${it.wonder ? 'could be' : "couldn't be"}</b> · ${it.why}` +
        (L(it.key).claude && who !== 'claude' ? ` · Claude: <b>${L(it.key).claude.useful ? 'useful' : 'not useful'}</b>${L(it.key).claude.note ? ' (' + L(it.key).claude.note + ')' : ''}` : '');
      info.appendChild(v); }
    list.appendChild(c);
  }
}
$('who').onchange = render;
load();
</script></main></body></html>"""

if __name__ == "__main__":
    main()
