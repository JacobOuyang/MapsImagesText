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
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

from . import codes, llm, photo, render, store, tools, zooms

MAX_ROUNDS = 3
ONCE = {"show_steps", "photo_details", "describe_photo", "emergency_reply", "rain_radar"}  # slow: never twice per request
SYSTEM = ("You help someone on a very slow or text-only connection; replies go out as short text messages. Their GPS "
          "is {lat:.5f}, {lon:.5f}. {photo}Call the tools you need, then reply in plain text, under 500 characters, no greeting or sign-off, one "
          "short sentence per line. Summarise: never list every forecast value, give when rain starts and stops (or that "
          "none is expected). Keep the numbers, times, distances, directions and names you use exactly as the tools "
          "gave them. Don't invent places or facts, and don't mention maps or pictures (the app adds them when it "
          "can). If they were sent a photo, call describe_photo; then, if it shows something they must do with their hands, "
          "show_steps (from_photo true); if knowing what it is or checking a detail matters (an animal, plant, pill, "
          "sign, a part's position), photo_details. When they ask how to do a physical action, or an alert tells them "
          "to do one, call show_steps; the app sends the drawings as separate messages, so your reply only needs the "
          "words. For walking directions give only the current step and the turn after it, as the tool gave them; the "
          "app sends the next step by itself when their position changes, so don't ask them to reply or send anything. For any emergency alert, call emergency_reply with the alert text, plus show_steps only if the alert "
          "tells them to do something with their hands (shut off gas, first aid).")


def _quick(text):
    return {"text": text, "sms": text, "mono": None, "small": None, "image": None, "map": None, "followups": [],
            "tools": [], "log": [], "seconds": 0.0}


def handle(message, you, image=None, who=None):
    """One message from a person (`who`: their number, when known) at `you` (lat, lon), maybe with a photo."""
    t = time.time()
    message = (message or "").strip()
    if not message and not image:  # an empty text once made Muse call three tools for 45 s
        return _quick("Send a question, an alert, or a photo.")
    try:
        lat, lon = float(you[0]), float(you[1])
        ok = -90 <= lat <= 90 and -180 <= lon <= 180
    except (TypeError, ValueError, IndexError):
        ok = False
    if not ok:  # tools would geocode and route from an impossible point
        return _quick("Your location looks wrong (%s). Check the phone's GPS, or name the place you are in." % (you,))
    again = codes.resend(message)  # "RESEND 7k 3": the kit asks for parts that were lost or damaged
    if again is not None:
        return _quick(codes.text(again) if again else "Nothing to resend: that message is no longer kept.")
    closer = codes.zoom(message)  # "ZOOM 3f 2": a closer look at part of a picture sent earlier, from its original
    if closer is not None:
        out = _quick(closer[0] + ("\n" + codes.text(closer[1]) if closer[1] else ""))
        out["kit"] = out["text"]
        return out
    found, log, report = codes.decode(message), [], None
    for c in found["complete"]:  # codes the kit made on the phone: a photo, or a check-in with GPS
        if c["kind"] == "P" and image is None:  # a PNG or WebP; the model calls send JPEG
            buf = io.BytesIO()
            Image.open(io.BytesIO(c["data"])).convert("RGB").save(buf, "JPEG", quality=95)
            image = buf.getvalue()
            log.append("a photo came in as a code (%d bytes)" % len(c["data"]))
        elif c["kind"] == "L":
            loc = codes.read_location(c["data"])
            if loc:
                lat, lon = loc["lat"], loc["lon"]
                log.append("check-in: %(status)s, %(people)s people at %(lat).5f, %(lon).5f (±%(accuracy_m)s m)" % loc)
                # Every check-in is kept as a report (with its photo, if one came with it) for the responders' view.
                photo_code = next((p["data"] for p in found["complete"] if p["kind"] == "P"), None)
                report = store.new("report", meta=dict(loc, text=codes.plain(message)), where=(lat, lon), who=who,
                                   image=Image.open(io.BytesIO(photo_code)) if photo_code else None)
                log.append("kept as report %s" % report)
                if not codes.plain(message):  # a check-in alone: acknowledge it
                    out = _quick("Check-in received: %s, %s %s at %.5f, %.5f. Keep your phone on; reply with any change."
                                 % ({"ok": "safe", "help": "needs help", "hurt": "someone hurt"}.get(loc["status"], loc["status"]),
                                    loc["people"] or 1, "person" if (loc["people"] or 1) == 1 else "people", lat, lon))
                    out.update(log=log, checkin=loc, report=report)
                    return out
    if found["missing"]:
        log.append("incomplete codes: %s" % found["missing"])
    message = codes.plain(message) or ("What is in this photo?" if image else message)
    ctx = tools.Context(you=(lat, lon), image=image)
    ctx.log.extend(log)
    calls, text = _run_model(ctx, message) if llm.available() else _run_rules(ctx, message)
    out = _versions(ctx, text)
    out["kit"] = _kit(ctx, out)
    out.update({"tools": calls, "log": ctx.log, "seconds": round(time.time() - t, 1), "report": report})
    return out


def kit_message(text, parts=(), signed=False):
    """A message for a phone with the kit: the words (or, if they need UCS-2, a T code, so the codes beside them
    stay in the basic alphabet), the codes, and optionally a signature over all of it."""
    words, packed = codes.words(text or "")
    body = "\n".join(x for x in [words, codes.text(list(packed) + list(parts))] if x)
    return body + ("\n" + codes.text(codes.sign(body)) if signed else "")


def kit_alert(r):
    """The kit version of an emergency reply: the alert's words, its map and a signature over the words."""
    words = r.get("text") or r["message"].split("\n\n")[0]
    return kit_message(words, codes.map_scene(r["scene"]) if r.get("scene") else [], signed=True)


def _kit(ctx, out):
    """What a phone with the kit gets (as plain SMS): the same words plus codes the kit draws. None if it would add
    nothing (no codes, and the words already fit the basic alphabet)."""
    s = ctx.store
    try:
        if "emergency_reply" in s:
            return kit_alert(s["emergency_reply"])
        if "walking_route" in s:
            w = s["walking_route"]
            trip = w.get("trip")
            route = codes.route([p for p in [trip.you] if p] + trip.route, trip.name) if trip and trip.route else []
            return kit_message(out.get("text") or "", codes.map_scene(w["scene"]) + route)
        if "show_steps" in s and s["show_steps"].get("pack"):
            return kit_message(out.get("text") or s["show_steps"]["text"], codes.phrase(s["show_steps"]["pack"]))
        text = out.get("text") or ""
        if text and codes.sms_parts(kit_message(text)) < codes.sms_parts(text):
            return kit_message(text)
    except Exception as ex:  # the plain reply never depends on the codes
        ctx.log.append("kit codes failed: %s" % ex)
    return None


def _run_model(ctx, message):
    msgs = [{"role": "system", "content": SYSTEM.format(lat=ctx.you[0], lon=ctx.you[1],
                                                        photo="They attached a photo. " if ctx.image else "")},
            {"role": "user", "content": message}]
    calls, done = [], {}
    for _ in range(MAX_ROUNDS):
        reply = llm.chat_tools(msgs, tools.schemas())
        todo = reply.get("tool_calls") or []
        if not todo:
            return calls, (reply.get("content") or "").strip()
        msgs.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": todo})
        parsed = [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"] or "{}")) for c in todo]
        def once(p):  # a slow tool runs once per request: asked again (even after a failure), it gets the first result
            key = p[1] if p[1] in ONCE else p[1] + json.dumps(p[2], sort_keys=True)
            if key not in done:
                done[key] = tools.call(ctx, p[1], p[2])
            return done[key]
        with ThreadPoolExecutor(max_workers=len(parsed)) as pool:  # radar and routing each take seconds
            results = list(pool.map(once, parsed))
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
    elif re.search(r"\bhow (do|to|can) (i|you|we)\b", m) and re.search(r"\b(cpr|tourniquet|recovery position|shut off|extinguisher|epipen|bleeding)\b", m):
        name, args = "show_steps", {"task": message}
    elif re.search(r"\b(alert|evacuat|warning|avoid|fire|flood|leak)\b", m) and len(message) > 80:
        name, args = "emergency_reply", {"text": message}
    elif "rain" in m or "radar" in m or "weather" in m:
        name, args = "rain_radar", {}
    elif re.search(r"\b(walk|get|directions?)\b.*\bto\b", m):
        route = re.search(r"(?:\bfrom\s+(?P<start>.+?)\s+)?\bto\s+(?P<dest>.+?)(?:\s+from\s+(?P<start2>.+?))?[?.!]*$",
                          message, re.I)
        args = {"destination": route.group("dest").strip()}
        if route.group("start") or route.group("start2"):
            args["start"] = (route.group("start") or route.group("start2")).strip()
        name = "walking_route"
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
        # SMS: the words (and the silhouette only if it passed its check). WhatsApp: only the complete, checked
        # picture (base drawing with the details carried in, close-ups that were measured or read), never pieces.
        r = photo.run(ctx.image, description=s["describe_photo"], whatsapp=False)
        if "photo_details" not in s:
            s["photo_details"] = zooms.complete(tools._image(ctx), subject=s["describe_photo"].get("object", ""))
        c = s["photo_details"]
        body = text or r["text"]
        out.update(text=body, sms=body + r["sms"][len(r["text"]):], map="photo", mono=c["mono"],
                   small=r["small"], understood=c["understood"], seen=c["seen"])
    if out["mono"]:
        out["mono_bytes"], out["mono_gzip"] = len(out["mono"].encode()), len(gzip.compress(out["mono"].encode()))
    out["followups"] = _followups(ctx)
    return out


def steps_pictures(pics):
    """Checked before/after pictures as a message: {"images", "frames" (text, only if they passed), "sms", "mono"}."""
    frames, sms_art, images, kit_codes = [], "", [], []
    for f in pics:
        for label, fr in zip(("before", "after"), f["frames"]):
            images.append(dict(fr["small"], caption="Step %d, %s" % (f["n"], label)))
            if fr.get("kit"):
                kit_codes.append("Step %d, %s:\n%s" % (f["n"], label, fr["kit"]))
            if f.get("text_ok"):
                frames.append("Step %d (%s): %s\n%s" % (f["n"], label, f["step"], "\n".join(fr["mono"])))
                sms_art += "\n\nStep %d, %s:\n%s" % (f["n"], label, "\n".join(fr["sms"]))
    sms_pics = ("Pictures for the kit:\n" + "\n".join(kit_codes)) if kit_codes else ""
    return {"images": images, "frames": frames, "sms": sms_art.strip(), "mono": "\n\n".join(frames),
            "sms_pictures": sms_pics, "sms_pictures_parts": sum(t.count("#P") for t in kit_codes)}


def _followups(ctx):
    """Messages sent after the reply: a step-by-step flip-book and close-ups of details, each in every version.
    {"kind", "title", "text" (SMS words), "sms", "mono", "frames" (WhatsApp frames, played in place)}."""
    s, out = ctx.store, []
    if "show_steps" in s:  # the cited words now; pictures, if any, follow (r["job"]: poll steps_pictures)
        r = s["show_steps"]
        f = dict(kind="steps", title=r["title"], text=r["text"], sms=r["text"], mono=r["text"], frames=[], images=[],
                 mono_bytes=len(r["text"].encode()), job=r.get("job"))
        if r.get("pack"):  # a checked picture already in the kit: only its id travels
            e = r["pack"]
            f["images"] = [{"data_url": "data:image/png;base64," + e[k], "bytes": len(e[k]) * 3 // 4,
                            "caption": "from your kit, %s" % k} for k in ("before", "after")]
            f["pack"] = e["id"]
        if r.get("frames"):  # made in the same request (not in the background)
            f.update(steps_pictures(r["frames"]))
            f["sms"], f["mono"] = r["text"] + f["sms"], r["text"] + f["mono"]
        out.append(f)
    for z in (s.get("photo_details") or {}).get("close_ups", []):  # on WhatsApp they are part of the complete picture
        out.append({"kind": "close_up", "title": z["name"], "text": "ZOOM: %s\n%s" % (z["name"], z["look"]), "sms": z["sms"],
                    "mono": None, "sms_only": True})
    return out
