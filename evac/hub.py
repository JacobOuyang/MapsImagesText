"""A community hub: one laptop with the only working connection, coordinating a neighbourhood whose phones can only
text. The coordinator's view (sim/dashboard.html) is built on these operations; everything is a record in the store.

    person    a neighbour or volunteer: name, number (`who`), role, language, home
    incident  the emergency: hazard, zone, the hub, the alert's text
    sms       every text in or out, with its SMS count (the night's data bill)
    report    a check-in (status, people, battery, note, maybe a photo), with Muse's triage of the photo
    zoom      a closer look a phone sent back, cropped from its own original
    closure   a spot nobody should pass (a downed line), marked from a report
    trip      a volunteer's walk to a neighbour, re-routed around closures

Phones are reached through `deliver` hooks (the simulator's phones; a real SMS gateway later).
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor


from . import closer, codes, directions, geo, llm, pipeline, router, sam, store

SMS_BYTES = 140
deliver = []            # functions (who, text) called for every text the hub sends
_pool = ThreadPoolExecutor(6)
_summary = {"key": None, "value": None}
_lock = threading.Lock()


# ---------- people and the incident ----------

def person(who):
    return next((p for p in store.find("person") if p["who"] == who), None)


def people(role=None):
    return [p for p in store.find("person") if role is None or p["meta"].get("role") == role]


def incident():
    found = store.find("incident", limit=1)
    return found[0] if found else None


# ---------- texts ----------

def log(direction, who, text):
    """One text in or out, with what it costs."""
    return store.new("sms", who=who, meta={"dir": direction, "text": text, "parts": codes.sms_parts(text), "at": time.time()})


def send(who, text, translate=True, raw=False):
    """A text from the hub. In the person's language when it isn't English (Muse translates); packed for the kit
    when the words need UCS-2. raw=True sends `text` as it is (it already holds codes)."""
    p = person(who)
    lang = (p or {}).get("meta", {}).get("lang", "en")
    if not raw:
        if translate and lang != "en" and llm.available():
            try:
                text = llm.chat([{"role": "system", "content": "Translate this text message into %s. Keep it short and plain, "
                                  "keep every number, street and place name exactly. Reply with the translation only." % LANGS.get(lang, lang)},
                                 {"role": "user", "content": text}]).strip()
            except Exception:
                pass
        text = router.kit_message(text)
    _deliver(who, text)
    return text


def _deliver(who, text):
    """Log a text the hub sends and hand it to the phones (the simulator's; an SMS gateway later)."""
    log("out", who, text)
    for f in deliver:
        try:
            f(who, text)
        except Exception:
            pass


LANGS = {"pt": "Portuguese", "es": "Spanish", "zh": "Chinese", "ar": "Arabic", "ht": "Haitian Creole", "ru": "Russian"}


def receive(who, text):
    """A text from a phone: through the router (check-ins become reports), the reply sent back, photos triaged."""
    log("in", who, text)
    p = person(who)
    home = (p or {}).get("where") or (incident() or {}).get("where") or (0, 0)
    out = router.handle(text, home, who=who)
    reply = out.get("kit") or out.get("text")
    if reply and (p or {}).get("meta", {}).get("lang", "en") != "en" and not codes.CODE.search(reply):
        send(who, reply)  # an acknowledgement in their own language
    elif reply:
        _deliver(who, reply)
    if out.get("report") and not out.get("zoom") and (store.get(out["report"]) or {}).get("file"):  # a new photo report
        _pool.submit(triage, out["report"])
    return out


# ---------- the alert ----------

def broadcast_alert(alert_text, personal=6):
    """The official alert, re-sent to every neighbour as a signed kit message. Those inside the zone (up to
    `personal` of them) get their own way out; everyone else gets the version drawn for the hub's area."""
    inc = incident()
    hub_at = inc["where"]
    centre, radius = inc["meta"]["zone"]
    residents = people("resident")
    inside = [p for p in residents if p["where"] and geo.haversine_m(p["where"], centre) <= radius][:personal]
    generic = pipeline.run(alert_text, user=hub_at)
    generic_kit = router.kit_alert(generic) if generic.get("scene") is not None else generic["message"]

    def personal_kit(p):
        try:
            r = pipeline.run(alert_text, user=tuple(p["where"]))
            return p["who"], router.kit_alert(r) if r.get("scene") is not None else r["message"]
        except Exception:
            return p["who"], generic_kit

    own = dict(_pool.map(personal_kit, inside))
    for p in residents + people("volunteer"):
        send(p["who"], own.get(p["who"], generic_kit), raw=True)
    store.update(inc["id"], meta={"alert_sent": time.time(), "personal": len(own)})
    return {"sent": len(residents) + len(people("volunteer")), "personal": len(own), "kit": generic_kit}


# ---------- reports ----------

TRIAGE = ("You help a volunteer coordinator at a neighbourhood hub during a storm. A neighbour sent this photo (it "
          "arrived by text, so it is small) with their check-in. Their note: \"{note}\" Their status: {status}. "
          "Reply with JSON: {{\"urgency\": \"critical\" | \"high\" | \"medium\" | \"low\", \"hazard\": a few words, "
          "\"look_at\": the one small detail a responder would want to see closer (the wire on the road, not the whole "
          "tree), as a short noun phrase a segmentation model could find (or \"\"), "
          "\"look_box\": a tight box around that detail, [left, top, right, bottom] as fractions of the photo's width and "
          "height, at most half the photo wide, "
          "\"advice\": one short sentence for the coordinator, \"keep_away_m\": metres people should keep away (0 if none)}}")


def triage(report_id):
    """Muse looks at the photo the phone sent: how urgent, what hazard, where to look closer. SAM's box replaces Muse's
    only when it agrees."""
    rep, img = store.get(report_id), store.image(report_id)
    if rep is None or img is None:
        return None
    t = {}
    if llm.available():
        try:
            t = llm.chat_json([{"role": "user", "content": [
                {"type": "text", "text": TRIAGE.format(note=rep["meta"].get("note") or "", status=rep["meta"].get("status"))},
                llm.image_part(closer.enlarged(img))]}])
        except Exception:
            t = {}
    fr = closer.box_fractions(t.get("look_box"))
    t = {"urgency": str(t.get("urgency") or "medium"), "hazard": str(t.get("hazard") or ""), "look_at": str(t.get("look_at") or ""),
         "advice": str(t.get("advice") or ""), "keep_away_m": int(t.get("keep_away_m") or 0)}
    # Where to look: Muse's box. At 128 px SAM finds nothing smaller than ~25 px (evals/closer/RESULTS.md), so its
    # box is only taken when it is confident and agrees with Muse (its centre inside Muse's box): then it is tighter.
    box = None
    if fr:
        W, H = img.size
        box = [round(fr[0] * W), round(fr[1] * H), max(4, round((fr[2] - fr[0]) * W)), max(4, round((fr[3] - fr[1]) * H))]
        t["box_from"] = "Muse"
    if t["look_at"] and sam.available():
        try:
            found = [f for f in sam.segment(img, t["look_at"]) if f["score"] >= 0.6]
        except Exception:
            found = []
        for f in found:
            x1, y1, x2, y2 = f["box"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            if box is None or (box[0] <= cx <= box[0] + box[2] and box[1] <= cy <= box[1] + box[3]):
                box = [round(x1), round(y1), max(4, round(x2 - x1)), max(4, round(y2 - y1))]
                t["box_from"] = "SAM 3.1 (%s, %.2f), agreeing with Muse" % (t["look_at"], f["score"])
                break
    store.update(report_id, meta={"triage": t, "look_box": box})
    return t


def ask_closer(report_id, box=None):
    """Ask the phone that sent a photo for a closer look at `box` (or at what triage found): one SMS out; the phone
    crops its own original and answers with a zoom code."""
    rep = store.get(report_id)
    pic = rep["meta"].get("pic")
    box = box or rep["meta"].get("look_box")
    if not pic or not box:
        return None
    return send(rep["who"], "ZOOM %s %d %d %d %d" % (pic["id"], *box), raw=True)


def block(where, radius_m=25, note="", warn_within_m=400):
    """Mark a spot nobody should pass: volunteers' walks go around it and neighbours nearby are told."""
    closure = store.new("closure", where=where, meta={"radius_m": radius_m, "note": note})
    rerouted = []
    for t in store.find("trip"):
        if t["meta"].get("done"):
            continue
        trip = store.obj(t["id"])
        if trip is None:
            continue
        directions.block(trip, where, radius_m)
        old = sum(geo.haversine_m(a, b) for a, b in zip([trip.you] + trip.route, trip.route))
        out = directions.move(trip, trip.you)
        new = sum(geo.haversine_m(a, b) for a, b in zip([trip.you] + trip.route, trip.route))
        store.update(t["id"], obj=trip)
        text = "Route changed: %s. %s" % (note or "blocked ahead", out["text"])
        send(t["who"], router.kit_message(text, codes.map_scene(out["scene"]) + codes.route([trip.you] + trip.route, trip.name))
             if out.get("scene") is not None else text, raw=True)
        rerouted.append({"trip": t["id"], "extra_m": round(new - old)})
    warned = []
    for p in people("resident"):
        if p["where"] and geo.haversine_m(p["where"], where) <= warn_within_m:
            send(p["who"], "Warning from the hub: %s. Keep at least %d m away." % (note or "danger", max(10, radius_m)))
            warned.append(p["who"])
    return {"closure": closure, "rerouted": rerouted, "warned": len(warned)}


# ---------- volunteers ----------

def assign(volunteer_who, report_id):
    """Send a volunteer to a neighbour: a walk that avoids the flood and every closure, as words, a map and a
    route their map app opens."""
    v, rep, inc = person(volunteer_who), store.get(report_id), incident()
    target = person(rep["who"])
    name = (target or {}).get("meta", {}).get("name", "your neighbour")
    centre, radius = inc["meta"]["zone"]
    avoid = {"kind": inc["meta"].get("hazard"), "zones": [(tuple(centre), radius)],
             "blocked": [(tuple(c["where"]), c["meta"].get("radius_m", 25)) for c in store.find("closure")]}
    start = tuple(v["meta"].get("at") or v["where"])
    out = directions.run(start, (name, tuple(rep["where"])), avoid=avoid)
    if out.get("error"):
        return out
    trip = out["trip"]
    t = store.new("trip", obj=trip, who=volunteer_who, where=start, meta={"volunteer": volunteer_who, "report": report_id, "to": name})
    store.update(report_id, meta={"assigned": volunteer_who, "trip": t})
    note = rep["meta"].get("note") or ""
    words = "Please go to %s%s. %s" % (name, " (%s)" % note if note else "", out["text"])
    kit = router.kit_message(words, codes.map_scene(out["scene"]) + codes.route([trip.you] + trip.route, trip.name))
    send(volunteer_who, kit, raw=True)
    send(rep["who"], "%s from the hub is on the way to you, about %d min." % (v["meta"]["name"], max(1, round(out["distance_m"] / 75))))
    return {"trip": t, "distance_m": out["distance_m"], "kit": kit}


def walk(trip_id, metres=60):
    """Move a volunteer along their route (the simulated GPS of their phone)."""
    t = store.get(trip_id)
    trip = store.obj(trip_id)
    you = directions.along(trip, metres)
    out = directions.move(trip, you)
    store.update(trip_id, obj=trip, where=you)
    if out.get("state") == "arrived":
        store.update(trip_id, meta={"done": True})
        store.update(t["meta"]["report"], meta={"reached": time.time()})
    return {"state": out.get("state"), "you": you}


# ---------- the dashboard ----------

def thread(who):
    """Everything between the hub and one phone, oldest first (and rumours forwarded to it)."""
    p = person(who)
    texts = sorted((r for r in store.find("sms", limit=100000) if r["who"] == who), key=lambda r: r["created"])
    return {"person": p and {"who": who, **p["meta"]},
            "texts": [{"dir": r["meta"]["dir"], "text": r["meta"]["text"], "parts": r["meta"]["parts"], "at": r["created"]} for r in texts]}


def latest_reports():
    """Each person's latest check-in."""
    seen, out = set(), []
    for r in store.find("report"):
        if r["who"] not in seen:
            seen.add(r["who"])
            out.append(r)
    return out


RANK = {"hurt": 0, "help": 1, "ok": 2}


def counts():
    reps = latest_reports()
    n = {"ok": 0, "help": 0, "hurt": 0}
    for r in reps:
        n[r["meta"].get("status", "ok")] = n.get(r["meta"].get("status", "ok"), 0) + 1
    sms = [s for s in store.find("sms", limit=100000) if s["meta"].get("dir") != "rumour"]  # the hub's own traffic
    parts = sum(s["meta"].get("parts", 1) for s in sms)
    return {"checked_in": len(reps), "residents": len(people("resident")), **n,
            "sms": parts, "texts": len(sms), "bytes": parts * SMS_BYTES,
            "reached": sum(1 for t in store.find("trip") if t["meta"].get("done"))}


SUMMARY = ("You help a volunteer coordinator at a neighbourhood hub during a storm. These are the latest check-ins "
           "(JSON). Pick the three neighbours to help first and say why in a few words each (battery, floor, injury, "
           "danger nearby, no volunteer yet). Reply with JSON: {\"first\": [{\"who\": number, \"why\": short reason}]}. "
           "Use only facts in the data.\n\n%s")


def summary():
    """Who to help first (Muse), cached until the reports change. The counts themselves come from code."""
    reps = [r for r in latest_reports() if r["meta"].get("status") in ("help", "hurt")]
    key = tuple((r["id"], r["version"]) for r in reps)
    with _lock:
        if key == _summary["key"]:
            return _summary["value"]
    facts = [{"who": r["who"], "name": (person(r["who"]) or {}).get("meta", {}).get("name"), "status": r["meta"].get("status"),
              "people": r["meta"].get("people"), "battery": r["meta"].get("battery"), "note": r["meta"].get("note") or r["meta"].get("n"),
              "photo": (r["meta"].get("triage") or {}).get("hazard"), "volunteer_assigned": bool(r["meta"].get("assigned")),
              "reached": bool(r["meta"].get("reached"))} for r in reps]
    first = []
    if facts and llm.available():
        try:
            first = llm.chat_json([{"role": "user", "content": SUMMARY % json.dumps(facts, ensure_ascii=False)}]).get("first") or []
        except Exception:
            first = []
    known = {re.sub(r"\D", "", f["who"]): f["who"] for f in facts}  # Muse sometimes drops the "+" from a number
    first = [dict(f, who=known[re.sub(r"\D", "", str(f.get("who")))]) for f in first if re.sub(r"\D", "", str(f.get("who"))) in known]
    if not first:  # without the model: injuries, then help, lowest battery first
        first = [{"who": f["who"], "why": "%s, battery %s%%" % (f["status"], f["battery"])}
                 for f in sorted(facts, key=lambda f: (RANK.get(f["status"], 3), f["battery"] or 100))[:3]]
    with _lock:
        _summary.update(key=key, value=first)
    return first


def state():
    """Everything the dashboard draws."""
    inc = incident()
    reps = latest_reports()
    trips = []
    for t in store.find("trip"):
        trip = store.obj(t["id"])
        if trip is None or "volunteer" not in t["meta"]:
            continue
        trips.append({"id": t["id"], "who": t["who"], "you": trip.you, "route": trip.route, "done": t["meta"].get("done", False),
                      "report": t["meta"]["report"], "to": t["meta"].get("to")})
    return {"incident": inc and {"name": inc["meta"].get("name"), "hub": inc["where"], "hub_name": inc["meta"].get("hub_name"),
                                 "zone": inc["meta"]["zone"], "hazard": inc["meta"].get("hazard"), "alert": inc["meta"].get("alert"),
                                 "alert_sent": inc["meta"].get("alert_sent"), "clock": inc["meta"].get("clock")},
            "people": [{"who": p["who"], "where": p["where"], **p["meta"]} for p in store.find("person")],
            "reports": [{"id": r["id"], "who": r["who"], "where": r["where"], "created": r["created"], "photo": bool(r["file"]), **r["meta"]} for r in reps],
            "closures": [{"id": c["id"], "where": c["where"], **c["meta"]} for c in store.find("closure")],
            "trips": trips, "counts": counts()}
