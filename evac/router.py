"""One entry point for any request: Muse Spark picks tools, the results become one reply in every version.

    router.handle("is it going to rain on my walk to the Colosseum?", you=(41.9010, 12.5016))

1. Muse sees the message and the tool descriptions (evac/tools.py) and asks for tool calls; they run in parallel
   and their summaries go back to it, for up to MAX_ROUNDS rounds. It then writes the reply text.
2. The bulky results the tools kept (ctx.store) decide what gets drawn: an emergency reply or a walking route is
   a Scene (drawn by evac/render.py); radar and photos still bring their own versions until they become Scenes.
3. Emergencies use the fixed, tested reply verbatim: the model routes to it but never rewrites it.
Without a model key, a few keyword rules pick the tool instead.
"""
import gzip
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import llm, photo, render, tools

MAX_ROUNDS = 3
SYSTEM = ("You help someone on a very slow or text-only connection; replies go out as short text messages. Their GPS "
          "is {lat:.5f}, {lon:.5f}. {photo}Call the tools you need, then reply in plain text, under 500 characters, one "
          "short sentence per line. Summarise: never list every forecast value, give when rain starts and stops (or that "
          "none is expected). Keep the numbers, times, distances, directions and names you use exactly as the tools "
          "gave them. Don't invent places or facts, and don't mention maps or pictures (the app adds them when it "
          "can). For any emergency alert, call emergency_reply with the alert text and nothing else.")


def handle(message, you, image=None):
    t = time.time()
    ctx = tools.Context(you=tuple(you), image=image)
    calls, text = _run_model(ctx, message) if llm.available() else _run_rules(ctx, message)
    out = _versions(ctx, text)
    out.update({"tools": calls, "log": ctx.log, "seconds": round(time.time() - t, 1)})
    return out


def _run_model(ctx, message):
    msgs = [{"role": "system", "content": SYSTEM.format(lat=ctx.you[0], lon=ctx.you[1],
                                                        photo="They attached a photo. " if ctx.image else "")},
            {"role": "user", "content": message}]
    calls = []
    for _ in range(MAX_ROUNDS):
        reply = llm.chat_tools(msgs, tools.schemas())
        todo = reply.get("tool_calls") or []
        if not todo:
            return calls, (reply.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": todo})
        parsed = [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")) for c in todo]
        with ThreadPoolExecutor(max_workers=len(parsed)) as pool:  # radar and routing each take seconds
            results = list(pool.map(lambda p: tools.call(ctx, p[1], p[2]), parsed))
        for (cid, name, args), res in zip(parsed, results):
            calls.append({"tool": name, "args": args})
            msgs.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(res, ensure_ascii=False)[:4000]})
        if "emergency_reply" in ctx.store:  # the tested text is the reply; no rewrite
            return calls, ""
    return calls, llm.chat(msgs + [{"role": "user", "content": "Reply now in plain text."}]).strip()


def _run_rules(ctx, message):
    """No model: route on keywords."""
    m = message.lower()
    if ctx.image:
        name, args = "describe_photo", {}
    elif re.search(r"\b(alert|evacuat|warning|avoid|fire|flood|leak)\b", m) and len(message) > 80:
        name, args = "emergency_reply", {"text": message}
    elif "rain" in m or "radar" in m or "weather" in m:
        name, args = "rain_radar", {}
    elif re.search(r"\b(walk|get|directions?) to\b", m):
        name, args = "walking_route", {"destination": re.split(r"\bto\b", message, 1)[1].strip(" ?.")}
    else:
        return [], "Sorry, I can only help with alerts, photos, rain and walking directions without the model."
    res = tools.call(ctx, name, args)
    text = res.get("message") or res.get("caption") or res.get("error") or ""
    return [{"tool": name, "args": args}], text


def _versions(ctx, text):
    """What to send, in every version: {"text", "sms", "mono", "mono_bytes", "mono_gzip", "small", "image", "map"}."""
    s = ctx.store
    out = {"text": text, "sms": text, "mono": None, "small": None, "image": None, "map": None}
    if "emergency_reply" in s:
        r = s["emergency_reply"]
        out.update(text=r.get("text") or r["message"], sms=r["message"], map="emergency", small=r.get("small"), image=r.get("image"),
                   mono=r["laptop"]["text"] if r.get("laptop") else None)
    elif "walking_route" in s:
        scene = s["walking_route"]["scene"]
        if text:
            scene.text = text  # the model's reply may also cover the rain, the time, ...
        sms, _ = render.sms(scene)
        out.update(text=scene.text, sms=sms, map="walking route", mono=render.mono(scene, 58)["text"],
                   small=render.small(scene), image=render.picture(scene))
    elif "rain_radar" in s:
        r = s["rain_radar"]
        body = text or r["text"]
        out.update(text=body, sms=body + r["sms"][len(r["text"]):], map="radar", mono=r["mono"], small=r["small"],
                   image=r["image"])
    elif "describe_photo" in s and ctx.image:
        r = photo.run(ctx.image, description=s["describe_photo"])
        body = text or r["text"]
        out.update(text=body, sms=body + r["sms"][len(r["text"]):], map="photo", mono=r["mono"], small=r["small"])
    if out["mono"]:
        out["mono_bytes"], out["mono_gzip"] = len(out["mono"].encode()), len(gzip.compress(out["mono"].encode()))
    return out
