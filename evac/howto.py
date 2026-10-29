"""How to do something, by text: numbered steps from cited sources, plus one picture only if it was verified.

    howto.answer("CPR on an adult", you=(41.90, 12.50))  -> {"text", "steps", "status", "sources", "frames", ...}

  1. Words: Muse Spark with the Meta Model API's built-in web search (llm.search: it searches, opens pages and cites
     them) writes 3 to 6 short numbered steps, every number that matters kept. Phone numbers never come from the web: the first step uses the emergency number for the person's
     location (a gas company's local number was copied once).
  2. Check, without a judge: a step is confirmed when a citation covers its text; a step with none is kept and
     marked "(not confirmed)". The numbered words always go, with the cited sites named.
  3. Pictures (evac/illustrate.sequence): for at most 3 steps that change an object (a valve handle turned, a pin
     pulled, a cap off), the object before and after, all as one chain of Muse Image edits; never people or bodies.
     A different model (Muse Wonder) is asked "could this be it?" of each pair and of its deliberately blurry text
     version. Only pairs for confirmed steps that pass are sent (as tiny images); text frames only if they pass too.
"""
import re

from . import geo, illustrate, llm

PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
STEP = re.compile(r"^\s*(\d+)[.)]\s+(.*\S)\s*$")
ASK = ("How to %s. Search authoritative sources (official health, safety, fire or utility organisations) and reply in "
       "exactly this form, nothing else:\n"
       "TITLE: 2 to 4 words\n"
       "1. first step\n2. ...\n"
       "(3 to 6 steps, each one line under 90 characters, every number that matters kept: depth, rate, time, distance, "
       "turns. If it can be life-threatening, step 1 says to call %s. No other phone numbers. Cite a source for every step.)")


def _plain(s):
    return re.sub(r"\*\*|__|\[\d+\]", "", s).strip()


def answer(task, you=None, picture=True):
    number = geo.emergency_number(you) if you else "the emergency number (911 in the US, 112 in Europe)"
    unavailable = "Checked steps can't be looked up right now. If it is an emergency, call %s." % number
    if not llm.available():
        return {"error": unavailable}
    try:
        r = llm.search(ASK % (task, number))
    except Exception:  # the person sees a plain sentence, never an exception
        return {"error": unavailable}
    text, cites = r["text"], r["citations"]
    title, steps, status, pos = task, [], [], 0
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if line.strip().upper().startswith("TITLE:"):
            title = _plain(line.split(":", 1)[1])
        elif STEP.match(line) and len(steps) < 6:
            say = _plain(PHONE.sub(lambda m: m.group(0) if m.group(0).strip() == number else "", STEP.match(line).group(2)))
            cited = any(c["start"] < pos and c["end"] > start for c in cites)
            steps.append(say)
            status.append("confirmed" if cited else "not confirmed")
    if not steps:
        return {"error": "no steps", "raw": text}
    srcs = list({c["url"]: {"url": c["url"], "title": c["title"]} for c in cites}.values())
    sites = list(dict.fromkeys(re.sub(r"^www\.", "", re.findall(r"https?://([^/]+)", s["url"] or "")[0]) for s in srcs if s["url"]))
    lines = [title + ":"] + ["%d. %s%s" % (i, s, "" if st == "confirmed" else " (not confirmed)")
                             for i, (s, st) in enumerate(zip(steps, status), 1)]
    lines.append(("Sources: " + ", ".join(sites[:5])) if sites else "(no source cited)")
    out = {"title": title, "steps": steps, "status": status, "text": "\n".join(lines), "sources": srcs,
           "searches": r["searches"], "frames": []}
    if picture and "confirmed" in status:
        frames = illustrate.sequence(task, steps)
        out["frames_tried"] = frames
        out["frames"] = [dict(f, frames=[dict(x, image=None) for x in f["frames"]]) for f in frames
                         if not f.get("error") and f["picture_ok"] and status[f["n"] - 1] == "confirmed"]
    return out
